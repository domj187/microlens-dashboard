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
import lzma
import os
import struct
import sys
import time as _time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
LON = ZoneInfo("Europe/London")
UTC = timezone.utc

BASE = "https://datafeed.dukascopy.com/datafeed"
# Dukascopy stores prices as integers scaled by the instrument's decimal
# places: 5 decimals for most pairs, 3 for JPY-quoted ones. Using 1e5 on a
# JPY pair silently yields prices 100x too small (146.20 -> 1.4620), which
# passes every OHLC sanity check, so it must be picked per pair.
PRICE_SCALE_DEFAULT = 1e5
PRICE_SCALE_JPY = 1e3
# Metals are not FX and do not follow the 5/3-decimal rule. Dukascopy is
# believed to serve XAUUSD scaled by 1e3, but that is NOT verified here
# (the feed is unreachable from this environment), so it is stated openly
# and guarded: sanity_prices() rejects an implausible result at fetch time,
# and --price-scale overrides the table if the feed disagrees.
PRICE_SCALE_TABLE = {"XAUUSD": 1e3, "XAGUSD": 1e3}

# Plausible median price per instrument, used to catch a wrong scale before
# the data reaches disk. Wide enough to never fire on real data, tight
# enough that a 10x or 100x scale error always trips it.
PLAUSIBLE = {"XAUUSD": (500, 10000), "XAGUSD": (5, 200)}
RECORD = struct.Struct(">iiiiif")  # time_offset, open, close, low, high, volume

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")


def month_range(start, end):
    """Yield (year, month) covering [start, end] inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_url(url, retries=4):
    delay = 2
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 404:
                return b""  # month not published (e.g. current partial month edge)
            if attempt == retries:
                raise
            print(f"    retry {attempt + 1} after error: {e}", file=sys.stderr)
            _time.sleep(delay)
            delay *= 2


def fetch_month(pair, year, month, side):
    """Download one monthly hour-candle file, using the local cache. Returns bytes."""
    cache = os.path.join(RAW_DIR, f"{pair}-{year}-{month:02d}-{side}.bi5")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    # Dukascopy months are 0-indexed in the URL path (00 = January)
    url = f"{BASE}/{pair}/{year}/{month - 1:02d}/{side}_candles_hour_1.bi5"
    data = fetch_url(url)
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(cache, "wb") as f:
        f.write(data)
    return data


def price_scale(pair, override=None):
    """Integer price scale for an instrument.

    FX majors are stored to 5 decimals, JPY crosses to 3; metals follow
    their own convention (see PRICE_SCALE_TABLE). --price-scale wins over
    everything when the feed turns out to disagree.
    """
    if override:
        return float(override)
    pair = pair.upper()
    if pair in PRICE_SCALE_TABLE:
        return PRICE_SCALE_TABLE[pair]
    return PRICE_SCALE_JPY if pair[3:] == "JPY" else PRICE_SCALE_DEFAULT


def parse_month(data, year, month, pair, scale_override=None):
    """Parse a monthly bi5 into {utc_datetime: (o, h, l, c, volume)}."""
    if not data:
        return {}
    raw = lzma.decompress(data)
    if len(raw) % RECORD.size:
        raise ValueError(f"corrupt bi5: {len(raw)} bytes not a multiple of {RECORD.size}")
    month_start = datetime(year, month, 1, tzinfo=UTC)
    scale = price_scale(pair, scale_override)
    out = {}
    offsets = [RECORD.unpack_from(raw, i * RECORD.size)[0] for i in range(len(raw) // RECORD.size)]
    # offsets are seconds from month start; guard against a millisecond variant
    divisor = 1000 if offsets and max(offsets) > 32 * 24 * 3600 else 1
    for i in range(len(raw) // RECORD.size):
        toff, o, c, lo, hi, vol = RECORD.unpack_from(raw, i * RECORD.size)
        t = month_start + timedelta(seconds=toff // divisor)
        out[t] = (o / scale, hi / scale, lo / scale, c / scale, vol)
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
                f"for {pair} — price scale is probably wrong "
                f"(scale used: {price_scale(pair):g}; override with --price-scale)"]
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
    ap.add_argument("--offline", action="store_true", help="use only cached raw files")
    args = ap.parse_args()

    def parse_day(text, what):
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise SystemExit(f"--{what} must be YYYY-MM-DD, got {text!r}")

    # --end is inclusive of the whole day; --start wins over --years
    end = (parse_day(args.end, "end") + timedelta(days=1) - timedelta(seconds=1)
           if args.end else datetime.now(UTC))
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

    summary = []
    for pair in args.pairs:
        print(f"== {pair} ==")
        candles = {}  # utc time -> {side: (o,h,l,c,vol)}
        for year, month in month_range(start, end):
            per_side = {}
            for side in sides:
                cache = os.path.join(RAW_DIR, f"{pair}-{year}-{month:02d}-{side}.bi5")
                if args.offline:
                    if not os.path.exists(cache):
                        print(f"  {year}-{month:02d} missing from cache, skipping")
                        break
                    with open(cache, "rb") as f:
                        data = f.read()
                else:
                    data = fetch_month(pair, year, month, side)
                per_side[side] = parse_month(data, year, month, pair, args.price_scale)
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
            print(f"  no data for {pair} — nothing written", file=sys.stderr)
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
