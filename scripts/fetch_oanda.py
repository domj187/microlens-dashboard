#!/usr/bin/env python3
"""Fetch historical FX candles from OANDA's v20 REST API and build
1H/4H/8H/Daily CSVs in the same format as the rest of data/.

Why OANDA rather than Dukascopy/HistData/Stooq — see scripts/README_sources.md.
Short version: the repo's reference exports (OANDA_*.csv) came from OANDA, a
free practice account gives a permanent API token, 3 years of H1 for one pair
is four requests (5000 candles each) against a ~120 req/min budget, and the
same endpoint serves bid and ask, which is what the spread analysis needs.

    # one-time: create a free practice account at oanda.com, then
    # Manage API Access -> generate a token
    export OANDA_API_TOKEN=xxxxxxxx-yyyyyyyyyyyy

    python3 scripts/fetch_oanda.py --pairs GBPUSD USDJPY GBPJPY EURGBP
    python3 scripts/fetch_oanda.py --pairs GBPUSD --price all   # mid + bid + ask
    python3 scripts/fetch_oanda.py --offline                    # rebuild from cache

Candle conventions are inherited from scripts/fetch_dukascopy.py so the output
is byte-compatible with the existing files: 4H/8H buckets anchored to 17:00
America/New_York, daily candles covering one 17:00->17:00 NY session labeled
with the close date, intraday timestamps ISO 8601 in Europe/London, daily as
plain YYYY-MM-DD. JPY pairs need no special handling — prices arrive as decimal
strings, not scaled integers.

Raw API pages are cached as JSON under data/raw_oanda/ so --offline rebuilds
and re-runs cost nothing.
"""

import argparse
import json
import os
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_dukascopy import (  # noqa: E402  - shared, already-validated helpers
    DATA_DIR, REPO_ROOT, UTC, aggregate, aggregate_daily, validate, write_csv,
)

HOSTS = {"practice": "https://api-fxpractice.oanda.com",
         "live": "https://api-fxtrade.oanda.com"}
RAW_DIR = os.path.join(DATA_DIR, "raw_oanda")
MAX_COUNT = 5000                  # v20 hard cap per request
PRICE_FIELD = {"mid": "M", "bid": "B", "ask": "A"}


def instrument(pair):
    """EURUSD -> EUR_USD (the v20 instrument name)."""
    return f"{pair[:3]}_{pair[3:]}"


def token(cli_token):
    if cli_token:
        return cli_token
    env = os.environ.get("OANDA_API_TOKEN")
    if env:
        return env.strip()
    path = os.path.expanduser("~/.oanda_token")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    raise SystemExit(
        "No API token. Create a free practice account at oanda.com, generate a\n"
        "token under Manage API Access, then either:\n"
        "  export OANDA_API_TOKEN=...   or   echo ... > ~/.oanda_token\n"
        "  or pass --token ...")


def get_json(url, tok, retries=5):
    """GET with backoff on 429/5xx, honouring Retry-After when present."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "microlens-dashboard/1.0",
    })
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            if e.code in (401, 403):
                raise SystemExit(
                    f"OANDA rejected the token ({e.code}). Check it is a v20 token for the\n"
                    f"--env you chose (practice tokens do not work on live and vice versa).\n{body}")
            if e.code == 400:
                raise SystemExit(f"OANDA rejected the request (400): {body}\nURL: {url}")
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise SystemExit(f"OANDA request failed ({e.code}): {body}\nURL: {url}")
            wait = float(e.headers.get("Retry-After") or delay)
            print(f"    {e.code}; retrying in {wait:.0f}s", file=sys.stderr)
            _time.sleep(wait)
            delay *= 2
        except urllib.error.URLError as e:
            if attempt == retries - 1:
                raise SystemExit(f"Network error reaching OANDA: {e.reason}")
            _time.sleep(delay)
            delay *= 2
    raise SystemExit("unreachable")


def rfc3339(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_pages(pair, start, end, price_arg, host, tok, cfg):
    """Page through H1 candles, caching each page. Returns the raw candle dicts."""
    os.makedirs(RAW_DIR, exist_ok=True)
    tag = f"{pair}_{price_arg}_{start:%Y%m%d}_{end:%Y%m%d}"
    candles, page, cursor = [], 0, start
    while cursor < end:
        cache = os.path.join(RAW_DIR, f"{tag}_p{page:03d}.json")
        if os.path.exists(cache):
            with open(cache) as f:
                data = json.load(f)
        elif cfg.offline:
            break
        else:
            url = (f"{host}/v3/instruments/{instrument(pair)}/candles"
                   f"?granularity=H1&price={price_arg}&count={MAX_COUNT}"
                   f"&from={rfc3339(cursor)}&includeFirst=true")
            data = get_json(url, tok)
            with open(cache, "w") as f:
                json.dump(data, f)
            _time.sleep(cfg.sleep)
        got = data.get("candles", [])
        if not got:
            break
        candles.extend(got)
        last = parse_time(got[-1]["time"])
        if last <= cursor:            # no forward progress: stop rather than loop
            break
        cursor = last + timedelta(seconds=1)
        page += 1
        print(f"    page {page}: {len(got)} candles through {last:%Y-%m-%d}")
    return candles


def parse_time(s):
    """RFC3339 with nanosecond precision -> aware datetime."""
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac, tz = rest[:-6], rest[-6:]
        s = f"{head}.{frac[:6]}{tz}"
    return datetime.fromisoformat(s).astimezone(UTC)


def to_bars(candles, side, start, end):
    """OANDA candle dicts -> sorted [(t, o, h, l, c)] of complete bars in range."""
    key = {"mid": "mid", "bid": "bid", "ask": "ask"}[side]
    rows, seen = [], set()
    for c in candles:
        if not c.get("complete", False) or key not in c:
            continue
        t = parse_time(c["time"])
        if t < start or t >= end or t in seen:
            continue
        seen.add(t)
        p = c[key]
        rows.append((t, float(p["o"]), float(p["h"]), float(p["l"]), float(p["c"])))
    rows.sort()
    return rows


def build(pair, h1, out_dir, quiet=False):
    """Write the 60/240/480/1D set for one pair; returns per-file report rows."""
    os.makedirs(out_dir, exist_ok=True)
    report = []
    for tf, rows, hours, daily in (
        ("60", h1, 1, False),
        ("240", aggregate(h1, 4), 4, False),
        ("480", aggregate(h1, 8), 8, False),
        ("1D", aggregate_daily(h1), 24, True),
    ):
        path = os.path.join(out_dir, f"{pair}_{tf}.csv")
        write_csv(path, rows, daily=daily)
        problems = [] if daily else validate(rows, hours, f"{pair}_{tf}")
        report.append((tf, len(rows), rows[0][0], rows[-1][0], problems))
        if not quiet:
            print(f"    {os.path.relpath(path, REPO_ROOT):<34} {len(rows):>6} candles"
                  + (f"   {len(problems)} gap/dup warnings" if problems else ""))
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", nargs="+",
                    default=["GBPUSD", "USDJPY", "GBPJPY", "EURGBP"])
    ap.add_argument("--years", type=float, default=3)
    ap.add_argument("--price", choices=["mid", "bid", "ask", "all"], default="mid",
                    help="'all' fetches mid+bid+ask in one pass and writes "
                         "data/, data_bid/ and data_ask/ (what the spread "
                         "analysis in backtest/pair_character.py needs)")
    ap.add_argument("--env", choices=["practice", "live"], default="practice")
    ap.add_argument("--token", default=None, help="overrides OANDA_API_TOKEN / ~/.oanda_token")
    ap.add_argument("--end", default=None, help="end date YYYY-MM-DD (default: today)")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="seconds between requests (default 0.4; the budget is ~120/min)")
    ap.add_argument("--offline", action="store_true", help="use only cached pages")
    ap.add_argument("--out", default=None, help="output dir (default: data/)")
    cfg = ap.parse_args()

    end = (datetime.strptime(cfg.end, "%Y-%m-%d").replace(tzinfo=UTC)
           if cfg.end else datetime.now(UTC))
    end = end.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=round(cfg.years * 365.25))

    sides = ["mid", "bid", "ask"] if cfg.price == "all" else [cfg.price]
    price_arg = "MBA" if cfg.price == "all" else PRICE_FIELD[cfg.price]
    host = HOSTS[cfg.env]
    tok = "offline" if cfg.offline else token(cfg.token)

    print(f"OANDA {cfg.env} · H1 {start:%Y-%m-%d} -> {end:%Y-%m-%d} · "
          f"{', '.join(sides)}\n")
    summary = []
    for pair in cfg.pairs:
        print(f"  {pair} ({instrument(pair)})")
        candles = fetch_pages(pair, start, end, price_arg, host, tok, cfg)
        if not candles:
            print("    no candles — "
                  + ("nothing cached for --offline" if cfg.offline else "check the date range")
                  + "\n")
            continue
        for side in sides:
            h1 = to_bars(candles, side, start, end)
            if not h1:
                print(f"    no {side} prices in the response "
                      f"(was --price {cfg.price} used on the cached pages?)")
                continue
            out_dir = cfg.out or (DATA_DIR if side == "mid"
                                  else os.path.join(REPO_ROOT, f"data_{side}"))
            if len(sides) > 1:
                print(f"    [{side}]")
            for tf, n, first, last, problems in build(pair, h1, out_dir):
                summary.append((pair, side, tf, n, first, last, len(problems)))
        print()

    if summary:
        print("pair,price,timeframe,candles,first,last,warnings")
        for row in summary:
            print(",".join(str(x) for x in row))


if __name__ == "__main__":
    main()
