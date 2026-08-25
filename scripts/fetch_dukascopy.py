#!/usr/bin/env python3
"""Fetch tick data from the Dukascopy datafeed and aggregate it to OHLC CSVs.

Downloads hourly .bi5 tick files into a local cache, then builds candle CSVs
in data/. Re-running resumes from the cache: hours already on disk (including
empty no-data hours, stored as zero-byte markers) are never re-requested.

The Dukascopy datafeed rate-limits aggressively (HTTP 503), so this client is
deliberately polite: it sleeps 1-2 s between every request and backs off
exponentially, up to 5 minutes per wait, when the server pushes back.

Usage:
    python scripts/fetch_dukascopy.py --start 2026-07-01 --end 2026-08-24
    python scripts/fetch_dukascopy.py --instruments EURUSD,AUDUSD
"""

import argparse
import csv
import lzma
import random
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache"
DATA_DIR = REPO_ROOT / "data"

DEFAULT_INSTRUMENTS = ["EURUSD", "AUDUSD", "AUDCHF", "EURCHF"]
# Candle sizes in minutes; "1D" is kept as a label to match the existing CSVs.
TIMEFRAMES = {"60": 60, "240": 240, "480": 480, "1D": 1440}

# Politeness settings.
MIN_REQUEST_GAP = (1.0, 2.0)   # seconds slept between every request
MAX_RETRIES = 10               # attempts per hour-file before giving up
BACKOFF_BASE = 5.0             # first retry wait in seconds
BACKOFF_CAP = 300.0            # never wait longer than 5 minutes

# Price scale: most FX pairs are stored as integer points of 1e-5
# (JPY-quoted pairs use 1e-3, none of which are fetched by default).
POINT_SCALE = {"default": 1e-5}
JPY_SCALE = 1e-3

TICK_STRUCT = struct.Struct(">3I2f")  # ms offset, ask, bid, ask vol, bid vol


def point_scale(instrument: str) -> float:
    return JPY_SCALE if instrument.endswith("JPY") else POINT_SCALE["default"]


def hour_url(instrument: str, dt: datetime) -> str:
    # Dukascopy months are zero-based in the URL scheme.
    return (f"{BASE_URL}/{instrument}/{dt.year:04d}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def cache_path(instrument: str, dt: datetime) -> Path:
    return (CACHE_DIR / instrument / f"{dt.year:04d}" / f"{dt.month:02d}" /
            f"{dt.day:02d}" / f"{dt.hour:02d}h_ticks.bi5")


def polite_sleep() -> None:
    time.sleep(random.uniform(*MIN_REQUEST_GAP))


def fetch_hour(instrument: str, dt: datetime) -> Path | None:
    """Download one hour of ticks into the cache, resuming if already present.

    Returns the cache file path, or None if the hour is unavailable (404).
    An empty cache file marks an hour known to have no data, so weekends and
    dead hours are only ever requested once.
    """
    path = cache_path(instrument, dt)
    if path.exists():
        return path if path.stat().st_size > 0 else None

    url = hour_url(instrument, dt)
    for attempt in range(MAX_RETRIES):
        polite_sleep()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            return path if body else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # No data for this hour; remember that so we never re-ask.
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
                return None
            retryable = exc.code == 503 or exc.code >= 500
            if not retryable or attempt == MAX_RETRIES - 1:
                raise
            wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
            wait *= random.uniform(0.8, 1.2)  # jitter so retries don't align
            print(f"    HTTP {exc.code} on {url} — retry {attempt + 1}/"
                  f"{MAX_RETRIES} in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
            print(f"    {exc} on {url} — retry {attempt + 1}/{MAX_RETRIES} "
                  f"in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
    return None


def read_ticks(path: Path, hour_start: datetime, scale: float):
    """Yield (timestamp, bid) for each tick in a cached .bi5 file."""
    raw = lzma.decompress(path.read_bytes())
    for offset in range(0, len(raw) - len(raw) % TICK_STRUCT.size,
                        TICK_STRUCT.size):
        ms, _ask, bid, _av, _bv = TICK_STRUCT.unpack_from(raw, offset)
        yield hour_start + timedelta(milliseconds=ms), bid * scale


def build_candles(ticks, minutes: int):
    """Aggregate (timestamp, price) pairs into OHLC candles keyed by bucket."""
    candles = {}
    for ts, price in ticks:
        epoch_min = int(ts.timestamp() // 60)
        bucket = datetime.fromtimestamp((epoch_min - epoch_min % minutes) * 60,
                                        tz=timezone.utc)
        c = candles.get(bucket)
        if c is None:
            candles[bucket] = [price, price, price, price]
        else:
            c[1] = max(c[1], price)
            c[2] = min(c[2], price)
            c[3] = price
    return candles


def write_csv(instrument: str, label: str, candles: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"DUKASCOPY_{instrument}, {label}.csv"
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "open", "high", "low", "close"])
        for bucket in sorted(candles):
            o, h, l, c = candles[bucket]
            writer.writerow([bucket.isoformat(),
                             f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}"])
    return out


def hour_range(start: date, end: date):
    dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    stop = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) \
        + timedelta(days=1)
    while dt < stop:
        yield dt
        dt += timedelta(hours=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instruments",
                        default=",".join(DEFAULT_INSTRUMENTS),
                        help="Comma-separated instrument list")
    parser.add_argument("--start", type=date.fromisoformat,
                        default=date.today() - timedelta(days=30))
    parser.add_argument("--end", type=date.fromisoformat,
                        default=date.today() - timedelta(days=1))
    args = parser.parse_args()

    instruments = [i.strip().upper() for i in args.instruments.split(",") if i.strip()]
    for instrument in instruments:
        print(f"{instrument}: {args.start} .. {args.end}")
        scale = point_scale(instrument)
        ticks = []
        for dt in hour_range(args.start, args.end):
            path = fetch_hour(instrument, dt)
            if path is not None:
                ticks.extend(read_ticks(path, dt, scale))
        if not ticks:
            print(f"  no ticks fetched for {instrument}", file=sys.stderr)
            continue
        ticks.sort(key=lambda t: t[0])
        for label, minutes in TIMEFRAMES.items():
            out = write_csv(instrument, label, build_candles(ticks, minutes))
            print(f"  wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
