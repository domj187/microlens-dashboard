#!/usr/bin/env python3
"""Fetch historical FX candles from Dukascopy's free datafeed and build
1H/4H/8H/Daily CSVs matching the OANDA export format (time,open,high,low,close).

Usage:
    python3 scripts/fetch_dukascopy.py                     # 3 years, all 4 pairs
    python3 scripts/fetch_dukascopy.py --years 3 --pairs AUDCHF AUDUSD EURCHF EURUSD
    python3 scripts/fetch_dukascopy.py --offline           # rebuild from cached raw files

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
PRICE_SCALE = 1e5  # 5-decimal pairs (all four defaults); JPY pairs would be 1e3
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


def parse_month(data, year, month):
    """Parse a monthly bi5 into {utc_datetime: (o, h, l, c, volume)}."""
    if not data:
        return {}
    raw = lzma.decompress(data)
    if len(raw) % RECORD.size:
        raise ValueError(f"corrupt bi5: {len(raw)} bytes not a multiple of {RECORD.size}")
    month_start = datetime(year, month, 1, tzinfo=UTC)
    out = {}
    offsets = [RECORD.unpack_from(raw, i * RECORD.size)[0] for i in range(len(raw) // RECORD.size)]
    # offsets are seconds from month start; guard against a millisecond variant
    divisor = 1000 if offsets and max(offsets) > 32 * 24 * 3600 else 1
    for i in range(len(raw) // RECORD.size):
        toff, o, c, lo, hi, vol = RECORD.unpack_from(raw, i * RECORD.size)
        t = month_start + timedelta(seconds=toff // divisor)
        out[t] = (o / PRICE_SCALE, hi / PRICE_SCALE, lo / PRICE_SCALE, c / PRICE_SCALE, vol)
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
    ap.add_argument("--years", type=float, default=3)
    ap.add_argument("--price", choices=["bid", "ask", "mid"], default="mid")
    ap.add_argument("--offline", action="store_true", help="use only cached raw files")
    args = ap.parse_args()

    end = datetime.now(UTC)
    start = end - timedelta(days=round(args.years * 365.25))
    os.makedirs(DATA_DIR, exist_ok=True)
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
                per_side[side] = parse_month(data, year, month)
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
            path = os.path.join(DATA_DIR, f"{pair}_{tf}.csv")
            write_csv(path, rows, daily=daily)
            problems = [] if daily else validate(rows, hours, f"{pair} {tf}")
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
