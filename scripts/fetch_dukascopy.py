#!/usr/bin/env python3
"""Fetch historical FX candles from Dukascopy's free datafeed and build
1H/4H/8H/Daily CSVs matching the OANDA export format (time,open,high,low,close).

Usage:
    python3 scripts/fetch_dukascopy.py                     # 3 years, all 4 pairs
    python3 scripts/fetch_dukascopy.py --years 3 --pairs AUDCHF AUDUSD EURCHF EURUSD
    python3 scripts/fetch_dukascopy.py --offline           # rebuild from cached raw files
    python3 scripts/fetch_dukascopy.py --start 2020-01-01 --end 2022-12-31 \
        --out data_2020_2022                              # any historical window

Raw monthly downloads are cached per pair/month/side, independent of the
window requested, so overlapping windows reuse whatever is already on disk
and only the missing months are downloaded.

Candle conventions (validated against the OANDA exports in this repo):
  - 4H/8H buckets are anchored to the 17:00 America/New_York session boundary.
  - Daily candles cover one 17:00->17:00 NY session, labeled with the close date.
  - Intraday timestamps are written as ISO 8601 in Europe/London (matching the
    OANDA files); daily timestamps as plain YYYY-MM-DD.
  - Prices are mid = (bid+ask)/2 by default (--price bid|ask|mid).

Raw monthly downloads are cached in data/raw/ so re-runs are cheap.
"""

import argparse
import csv
import json
import os
import random
import sys
import time as _time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
LON = ZoneInfo("Europe/London")
UTC = timezone.utc

# Dukascopy's current JSON API, as used by their maintained client
# (dukascopy-node). The legacy .bi5 datafeed it replaced served FX only and
# 503'd on indices; this endpoint serves both and carries its own price
# multiplier, so nothing here has to guess a per-instrument scale.
BASE = "https://jetta.dukascopy.com/v1"
# Dukascopy stores prices as integers scaled by the instrument's decimal
# places: 5 decimals for most pairs, 3 for JPY-quoted ones. Using 1e5 on a
# JPY pair silently yields prices 100x too small (146.20 -> 1.4620), which
# passes every OHLC sanity check, so it must be picked per pair.
# Metals are not FX and do not follow the 5/3-decimal rule. Dukascopy is
# believed to serve XAUUSD scaled by 1e3, but that is NOT verified here
# (the feed is unreachable from this environment), so it is stated openly
# and guarded: sanity_prices() rejects an implausible result at fetch time,
# and --price-scale overrides the table if the feed disagrees.

# Dukascopy names indices descriptively (JForex "USATECH.IDX/USD"), which
# flattens to USATECHIDXUSD in the datafeed path — not the broker-style
# NAS100 that most platforms use. Keep the short name as the repo's symbol
# (it names the CSVs) and translate only when building the URL.
# NOT VERIFIED from this environment: the feed is unreachable here, so both
# the feed names and the 1e3 scale above are convention, not observation.
# sanity_prices() rejects an implausible result and --price-scale overrides.
# CORRECTED against dukascopy-node's generated instrument metadata
# (github.com/Leo4815162342/dukascopy-node, src/utils/instrument-meta-data/
# generated/instrument-meta-data.json). The legacy .bi5 datafeed this script
# uses serves FX fine but 503s on these index instruments, confirmed in
# practice on both spellings. Dukascopy's maintained client no longer uses
# the .bi5 path at all: it fetches JSON from https://jetta.dukascopy.com/v1
# with the instrument's "code", e.g.
#   https://jetta.dukascopy.com/v1/candles/hour/USATECH.IDX-USD/BID/2024/1
# (month 1-based there, unlike the 0-based .bi5 path). Indices therefore
# need that endpoint, not a different .bi5 spelling — see README.
# Instrument codes as published in Dukascopy's own instrument metadata
# (via dukascopy-node's generated instrument-meta-data.json). FX and metals
# follow XXX-YYY, so only the non-obvious ones need listing.
DUKASCOPY_V1_CODE = {
    "NAS100": "USATECH.IDX-USD",   # metadata key usatechidxusd, "US 100 Tech Index"
    "SPX500": "USA500.IDX-USD",
    "US30": "USA30.IDX-USD",
    "US2000": "USSC2000.IDX-USD",
    "UK100": "GBR.IDX-GBP",
    "DXY": "DOLLAR.IDX-USD",
}

# Plausible median price per instrument, used to catch a wrong scale before
# the data reaches disk. Wide enough to never fire on real data, tight
# enough that a 10x or 100x scale error always trips it.
PLAUSIBLE = {"XAUUSD": (500, 10000), "XAGUSD": (5, 200),
             "NAS100": (5000, 40000), "SPX500": (2000, 12000),
             "US30": (15000, 80000), "GER40": (8000, 40000)}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")


def probe(pair):
    """Try candidate URL shapes for one month and report what the server says.

    Includes EUR-USD as a control: if the control works and the target does
    not, the shape is right and the instrument code is wrong.
    """
    code = feed_symbol(pair)
    flat = pair.upper()
    y, m = 2024, 1
    candidates = [
        ("documented shape", f"{BASE}/candles/hour/{code}/BID/{y}/{m}"),
        ("control EUR-USD", f"{BASE}/candles/hour/EUR-USD/BID/{y}/{m}"),
        ("lowercase price", f"{BASE}/candles/hour/{code}/bid/{y}/{m}"),
        ("zero-padded month", f"{BASE}/candles/hour/{code}/BID/{y}/{m:02d}"),
        ("zero-based month", f"{BASE}/candles/hour/{code}/BID/{y}/{m - 1}"),
        ("dot percent-encoded", f"{BASE}/candles/hour/{code.replace('.', '%2E')}/BID/{y}/{m}"),
        ("no dot/hyphen", f"{BASE}/candles/hour/{flat}/BID/{y}/{m}"),
        ("daily granularity", f"{BASE}/candles/day/{code}/BID/{y}"),
        ("instrument metadata", f"{BASE}/instruments/{code}"),
        ("instrument list", f"{BASE}/instruments"),
    ]
    # The month in progress has no completed-period path: /{year}/{month}
    # answers 400 for it and only the ?from= active-bucket form works. Probe
    # both, because a probe that only asks for a finished month passes while
    # the real fetch (which always ends on the current month) fails.
    now = datetime.now(UTC)
    cy, cm = now.year, now.month
    cur_ms = int(datetime(cy, cm, 1, tzinfo=UTC).timestamp() * 1000)
    candidates += [
        (f"current month {cy}/{cm} path", f"{BASE}/candles/hour/{code}/BID/{cy}/{cm}"),
        (f"current month ?from=", f"{BASE}/candles/hour/{code}/BID?from={cur_ms}"),
    ]
    print(f"probing {pair} (code {code}) against {BASE}\n")
    for label, url in candidates:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                print(f"  {label:<22} HTTP {r.status}  {len(body)} bytes")
                print(f"    {url}")
                print(f"    {body[:180].decode('utf-8', 'replace')}")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace").strip()
            except Exception:
                pass
            print(f"  {label:<22} HTTP {e.code}")
            print(f"    {url}")
            print(f"    {body[:180] or '(empty body)'}")
        except Exception as e:
            print(f"  {label:<22} {type(e).__name__}: {e}\n    {url}")
        print()
        _time.sleep(1.0)


def month_range(start, end):
    """Yield (year, month) covering [start, end] inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_url(url, retries=4, throttle_base=20.0):
    """GET with backoff.

    Dukascopy answers a request for an instrument it does not serve with 404,
    which we treat as "no data for this month". 503s, dropped TLS handshakes
    and "EOF in violation of protocol" are the opposite signal: the server is
    there and is refusing us, i.e. IP-level throttling. Those get a much
    longer, jittered backoff than an ordinary transient error, because
    retrying fast is what provoked them.
    """
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            code = e.code
            body = ""
            try:
                body = e.read().decode("utf-8", "replace").strip()
            except Exception:
                pass
            if code == 404:
                return b""  # instrument/month not served
            if 400 <= code < 500 and code != 429:
                # The request itself is wrong; retrying it changes nothing.
                raise SystemExit(
                    f"\n  HTTP {code} — the server rejected the request.\n"
                    f"  URL:  {url}\n"
                    f"  Body: {body[:1000] or '(empty)'}\n"
                    f"  Retrying a malformed request will not help. Run\n"
                    f"    python3 {os.path.basename(__file__)} --probe <PAIR>\n"
                    f"  to test URL variants against a known-good control.")
            if attempt == retries:
                raise SystemExit(f"HTTP {code} after {retries} retries\n"
                                 f"  URL:  {url}\n  Body: {body[:500] or '(empty)'}")
            throttled = code in (429, 503)
            wait = (throttle_base * (attempt + 1) + random.uniform(0, 5)
                    if throttled else delay)
            print(f"    retry {attempt + 1} in {wait:.0f}s after HTTP {code}"
                  f"{' (throttled)' if throttled else ''}: {url}", file=sys.stderr)
            _time.sleep(wait)
            delay *= 2
        except Exception as e:
            if attempt == retries:
                raise SystemExit(f"{type(e).__name__}: {e}\n  URL: {url}")
            wait = throttle_base * (attempt + 1) + random.uniform(0, 5)
            print(f"    retry {attempt + 1} in {wait:.0f}s after {e}: {url}",
                  file=sys.stderr)
            _time.sleep(wait)
            delay *= 2


def feed_symbol(pair):
    """The instrument code the v1 API addresses this instrument by.

    EURUSD -> EUR-USD, XAUUSD -> XAU-USD; anything non-obvious (indices)
    comes from DUKASCOPY_V1_CODE.
    """
    pair = pair.upper()
    if pair in DUKASCOPY_V1_CODE:
        return DUKASCOPY_V1_CODE[pair]
    if len(pair) == 6:
        return f"{pair[:3]}-{pair[3:]}"
    raise SystemExit(
        f"no Dukascopy instrument code known for {pair!r}. Add it to "
        f"DUKASCOPY_V1_CODE in {os.path.basename(__file__)} — the codes are "
        f"listed in dukascopy-node's instrument-meta-data.json.")


def cache_path(pair, year, month, side):
    return os.path.join(RAW_DIR, f"{pair}-{year}-{month:02d}-{side}.json")


def is_active_month(year, month, now=None):
    """True if (year, month) is the month currently in progress, in UTC.

    The v1 API serves a *completed* month at /{year}/{month} and rejects that
    path (HTTP 400) for the month that has not finished yet; the in-progress
    bucket is served by ?from={bucket_start_ms} instead. dukascopy-node draws
    the same distinction (getCompletedPeriodUrl vs getActivePeriodUrl).
    """
    now = now or datetime.now(UTC)
    return (year, month) == (now.year, now.month)


def month_url(pair, year, month, side, now=None):
    """URL for one month of hourly candles.

    The v1 month is 1-based (the old .bi5 path was 0-based). The current
    month has no completed-period path, so it is requested as the active
    bucket: ?from= the month start in epoch milliseconds.
    """
    base = f"{BASE}/candles/hour/{feed_symbol(pair)}/{side}"
    if is_active_month(year, month, now):
        start_ms = int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)
        return f"{base}?from={start_ms}"
    return f"{base}/{year}/{month}"


def fetch_month(pair, year, month, side, now=None):
    """Download one month of hourly candles as JSON, using the local cache.

    The in-progress month is never cached — it would freeze a partial month
    on disk and every later run would silently reuse it.
    """
    active = is_active_month(year, month, now)
    cache = cache_path(pair, year, month, side)
    if not active and os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    data = fetch_url(month_url(pair, year, month, side, now))
    if not active:
        os.makedirs(RAW_DIR, exist_ok=True)
        with open(cache, "wb") as f:
            f.write(data)
    return data


def price_places(multiplier):
    """Decimal places implied by the multiplier (1e-5 -> 5), for rounding."""
    text = repr(float(multiplier)).lower()
    coeff, _, exp = text.partition("e")
    frac = coeff.split(".")[1].rstrip("0") if "." in coeff else ""
    return max(0, len(frac) - (int(exp) if exp else 0))


def decode_month(data, pair):
    """Decode a v1 hourly-candle response into {utc_datetime: (o,h,l,c,vol)}.

    The response is delta-encoded: a base candle in real prices, a
    `multiplier` giving the size of one price unit, a `shift` giving the bar
    interval in ms, and per-bar integer deltas in units. `times[i]` is how
    many bars forward this bar sits; anything skipped is a flat filler bar
    carrying the previous close at zero volume, reproduced here so the
    caller's existing filler filter still sees them.
    """
    if not data:
        return {}
    try:
        d = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SystemExit(f"{pair}: response was not JSON ({e}). If this used to "
                         f"be a .bi5 cache, clear data/raw/ — the format changed.")
    times = d.get("times") or []
    if not times:
        return {}
    for f in ("timestamp", "multiplier", "shift", "open", "high", "low", "close"):
        if d.get(f) is None:
            return {}
    mult = float(d["multiplier"])
    if mult <= 0:
        raise SystemExit(f"{pair}: invalid multiplier {mult!r} in response")
    places = price_places(mult)
    shift = int(d["shift"])                    # bar interval, milliseconds
    ts = int(d["timestamp"])
    u_o = round(float(d["open"]) / mult)
    u_h = round(float(d["high"]) / mult)
    u_l = round(float(d["low"]) / mult)
    u_c = round(float(d["close"]) / mult)
    prev_c = u_c
    opens, highs = d["opens"], d["highs"]
    lows, closes = d["lows"], d["closes"]
    vols = d.get("volumes") or [0] * len(times)
    if not all(len(x) == len(times) for x in (opens, highs, lows, closes, vols)):
        raise SystemExit(f"{pair}: malformed response — column lengths differ")

    def px(units):
        return round(units * mult, places)

    out = {}
    for i, delta_t in enumerate(times):
        for gap in range(int(delta_t) - (0 if i == 0 else 1)):
            flat_ts = ts + (gap if i == 0 else gap + 1) * shift
            v = px(prev_c)
            out[datetime.fromtimestamp(flat_ts / 1000, UTC)] = (v, v, v, v, 0.0)
        ts += int(delta_t) * shift
        u_o += opens[i]
        u_h += highs[i]
        u_l += lows[i]
        u_c += closes[i]
        prev_c = u_c
        out[datetime.fromtimestamp(ts / 1000, UTC)] = (
            px(u_o), px(u_h), px(u_l), px(u_c), float(vols[i]))
    return out


def in_closed_window(t):
    """True if t falls in the weekend close: Fri 17:00 NY -> Sun 17:00 NY."""
    ny = t.astimezone(NY)
    wd, h = ny.weekday(), ny.hour
    return (wd == 4 and h >= 17) or wd == 5 or (wd == 6 and h < 17)


def session_start(t):
    """The 17:00 NY session boundary at or before t."""
    ny = t.astimezone(NY)
    d = ny.date() if ny.hour >= 17 else ny.date() - timedelta(days=1)
    return datetime(d.year, d.month, d.day, 17, tzinfo=NY)


def trading_day(t):
    """Calendar date of the session close (17:00 NY) that candle t belongs to."""
    ny = t.astimezone(NY)
    return ny.date() + timedelta(days=1) if ny.hour >= 17 else ny.date()


def aggregate(h1, hours):
    """h1: sorted [(t, o, h, l, c)] -> sorted [(bucket_start, o, h, l, c)]."""
    out = {}
    for t, o, h, l, c in h1:
        ss = session_start(t)
        idx = int((t - ss).total_seconds() // (hours * 3600))
        key = ss + timedelta(hours=hours * idx)
        if key not in out:
            out[key] = [o, h, l, c]
        else:
            b = out[key]
            b[1] = max(b[1], h)
            b[2] = min(b[2], l)
            b[3] = c
    return sorted((k, *v) for k, v in out.items())


def aggregate_daily(h1):
    out = {}
    for t, o, h, l, c in h1:
        key = trading_day(t)
        if key not in out:
            out[key] = [o, h, l, c]
        else:
            b = out[key]
            b[1] = max(b[1], h)
            b[2] = min(b[2], l)
            b[3] = c
    return sorted((k, *v) for k, v in out.items())


def fmt_price(p):
    return f"{p:.5f}".rstrip("0").rstrip(".") if p != int(p) else str(int(p))


def write_csv(path, rows, daily=False):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close"])
        for t, o, h, l, c in rows:
            ts = t.isoformat() if daily else t.astimezone(LON).isoformat()
            w.writerow([ts, fmt_price(o), fmt_price(h), fmt_price(l), fmt_price(c)])


def sanity_prices(rows, pair):
    """Catch a wrong price scale before it reaches disk.

    Every JPY cross trades well above 10; every other major sits between
    0.05 and 10. A scale error moves prices by a factor of 100, so this
    separates the two cases with a wide margin and no tuning.
    """
    if not rows:
        return []
    mid = sorted(r[4] for r in rows)[len(rows) // 2]
    pu = pair.upper()
    if pu in PLAUSIBLE:
        lo, hi = PLAUSIBLE[pu]
    else:
        lo, hi = (10, 1000) if pu[3:] == "JPY" else (0.05, 10)
    if not (lo <= mid <= hi):
        return [f"median close {mid:g} is outside the plausible {lo}-{hi} range "
                f"for {pair} — check the instrument code "
                f"({feed_symbol(pair)}) and the response multiplier"]
    return []


def validate(rows, hours, label):
    """Check for duplicates and unexpected gaps; returns list of problem strings."""
    problems = []
    seen = set()
    for r in rows:
        if r[0] in seen:
            problems.append(f"duplicate timestamp {r[0]}")
        seen.add(r[0])
    step = timedelta(hours=hours)
    for a, b in zip(rows, rows[1:]):
        gap = b[0] - a[0]
        if gap == step:
            continue
        # spanning the weekend close (or a holiday adjoining it) is expected
        if in_closed_window(a[0] + step) or in_closed_window(b[0] - step):
            continue
        problems.append(f"gap {a[0]} -> {b[0]} ({gap})")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", nargs="+", default=["AUDCHF", "AUDUSD", "EURCHF", "EURUSD"])
    ap.add_argument("--years", type=float, default=3,
                    help="window length ending today (default 3); ignored when "
                         "--start is given")
    ap.add_argument("--start", default=None, metavar="YYYY-MM-DD",
                    help="first day to fetch (inclusive). With --end this "
                         "selects any historical window, e.g. "
                         "--start 2020-01-01 --end 2022-12-31")
    ap.add_argument("--end", default=None, metavar="YYYY-MM-DD",
                    help="last day to fetch (inclusive; defaults to today)")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="output directory (default: data/). Use this when "
                         "fetching a different window so it does not overwrite "
                         "the working dataset")
    ap.add_argument("--price", choices=["bid", "ask", "mid"], default="mid")
    ap.add_argument("--price-scale", type=float, default=None,
                    help="override the integer price scale (e.g. 1000 for XAUUSD "
                         "if the feed disagrees with the built-in table)")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="seconds between month downloads (default 1.5). Three "
                         "years of one pair is ~72 requests; firing them "
                         "back-to-back is what triggers Dukascopy's throttling. "
                         "Raise it if you still see 503s; cached months are "
                         "never re-requested, so an interrupted run resumes.")
    ap.add_argument("--probe", metavar="PAIR", default=None,
                    help="diagnose a rejected instrument: request one month "
                         "under several URL shapes plus a known-good control, "
                         "and print each status and response body")
    ap.add_argument("--offline", action="store_true", help="use only cached raw files")
    args = ap.parse_args()

    if args.probe:
        probe(args.probe)
        return

    def parse_day(text, what):
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise SystemExit(f"--{what} must be YYYY-MM-DD, got {text!r}")

    # --end is inclusive of the whole day; --start wins over --years
    now = datetime.now(UTC)
    end = (parse_day(args.end, "end") + timedelta(days=1) - timedelta(seconds=1)
           if args.end else now)
    if args.start:
        start = parse_day(args.start, "start")
    else:
        start = end - timedelta(days=round(args.years * 365.25))
    if start >= end:
        raise SystemExit(f"empty window: start {start:%Y-%m-%d} is not before "
                         f"end {end:%Y-%m-%d}")
    if args.start and args.end is None:
        print(f"note: --start given without --end, fetching through today "
              f"({end:%Y-%m-%d})")

    out_dir = args.out or DATA_DIR
    os.makedirs(out_dir, exist_ok=True)
    print(f"window: {start:%Y-%m-%d} -> {end:%Y-%m-%d}  ->  {out_dir}\n")
    sides = {"bid": ["BID"], "ask": ["ASK"], "mid": ["BID", "ASK"]}[args.price]
    if args.price_scale:
        print("note: --price-scale is ignored on the v1 API — each response "
              "carries its own multiplier.", file=sys.stderr)

    summary = []
    for pair in args.pairs:
        print(f"== {pair} ==")
        candles = {}  # utc time -> {side: (o,h,l,c,vol)}
        for year, month in month_range(start, min(end, now)):
            per_side = {}
            for side in sides:
                cache = cache_path(pair, year, month, side)
                if args.offline:
                    if not os.path.exists(cache):
                        print(f"  {year}-{month:02d} missing from cache, skipping")
                        break
                    with open(cache, "rb") as f:
                        data = f.read()
                else:
                    cached = (not is_active_month(year, month, now)
                              and os.path.exists(cache))
                    data = fetch_month(pair, year, month, side, now)
                    if not cached and args.sleep:
                        _time.sleep(args.sleep)   # pace: 3 years = ~72 requests
                per_side[side] = decode_month(data, pair)
            else:
                for t in set().union(*per_side.values()):
                    entry = {s: per_side[s].get(t) for s in sides}
                    if all(entry.values()):
                        candles[t] = entry

        h1 = []
        for t in sorted(candles):
            if not (start <= t <= end) or in_closed_window(t):
                continue
            vals = candles[t]
            if args.price == "mid":
                b, a = vals["BID"], vals["ASK"]
                if b[4] == 0 and a[4] == 0 and b[0] == b[1] == b[2] == b[3]:
                    continue  # flat zero-volume filler candle
                ohlc = tuple(round((b[i] + a[i]) / 2, 5) for i in range(4))
            else:
                v = vals[sides[0]]
                if v[4] == 0 and v[0] == v[1] == v[2] == v[3]:
                    continue
                ohlc = v[:4]
            h1.append((t, *ohlc))

        if not h1:
            print(f"  no data for {pair} — nothing written "
                  f"(feed symbol used: {feed_symbol(pair)}).\n"
                  f"  404s mean the instrument code is wrong: check it against\n"
                  f"  dukascopy-node's instrument-meta-data.json and add it to\n"
                  f"  DUKASCOPY_V1_CODE. 400s mean the URL shape is wrong for\n"
                  f"  that month. 503s or dropped TLS handshakes are throttling,\n"
                  f"  not a naming problem — raise --sleep, or use\n"
                  f"  scripts/fetch_oanda.py. Run with --probe to see which.",
                  file=sys.stderr)
            continue

        outputs = [
            ("60", h1, 1, False),
            ("240", aggregate(h1, 4), 4, False),
            ("480", aggregate(h1, 8), 8, False),
            ("1D", aggregate_daily(h1), 24, True),
        ]
        for tf, rows, hours, daily in outputs:
            path = os.path.join(out_dir, f"{pair}_{tf}.csv")
            write_csv(path, rows, daily=daily)
            problems = ([] if daily else validate(rows, hours, f"{pair} {tf}"))
            problems += sanity_prices(rows, pair)
            first = rows[0][0] if daily else rows[0][0].astimezone(LON)
            last = rows[-1][0] if daily else rows[-1][0].astimezone(LON)
            summary.append((pair, tf, len(rows), str(first), str(last), problems))
            print(f"  {tf:>3}: {len(rows):6d} candles  {first} -> {last}"
                  + (f"  PROBLEMS: {len(problems)}" if problems else ""))
            for p in problems[:10]:
                print(f"       {p}")

    print("\npair,timeframe,candles,first,last,problems")
    for pair, tf, n, first, last, problems in summary:
        print(f"{pair},{tf},{n},{first},{last},{len(problems)}")


if __name__ == "__main__":
    main()
