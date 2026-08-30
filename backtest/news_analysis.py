#!/usr/bin/env python3
"""Correlate backtest trades with high-impact economic news releases.

Reads a trades.csv produced by backtest.py and a historical economic
calendar (see data/news/README.md for the file format), then classifies
every closed trade as news-affected or clean:

  - "entry window":  a high-impact release for one of the trade's two
    currencies within +/- WINDOW hours of the entry time, and/or
  - "in trade":      such a release between entry and exit.

Reports win rate and average R for affected vs clean trades, the split by
trigger, and a per-pair breakdown; writes an annotated trade list and a
summary JSON next to the trades file (in a news/ subdirectory).

Stdlib only.  Usage:

  python3 backtest/news_analysis.py \
      --trades backtest/results/origin-swing-filtered/trades.csv \
      --calendar data/news/high_impact.csv \
      --window-hours 4
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CURRENCIES = {"USD", "EUR", "AUD", "CHF", "GBP", "JPY", "CAD", "NZD", "CNY"}

DT_FORMATS = [
    "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M", "%m-%d-%Y %H:%M", "%m/%d/%Y %H:%M",
    "%d.%m.%Y %H:%M", "%d-%m-%Y %H:%M",
]
TIME_12H = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*$", re.I)


def parse_dt(text: str, tz) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:  # ISO 8601, possibly with offset
        dt = datetime.fromisoformat(text)
        return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(timezone.utc)
    except ValueError:
        pass
    for f in DT_FORMATS:
        try:
            return datetime.strptime(text, f).replace(tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def parse_date_and_time(date_s: str, time_s: str, tz) -> datetime | None:
    m = TIME_12H.match(time_s or "")
    if m:
        h, mi = int(m.group(1)) % 12, int(m.group(2))
        if m.group(3).lower() == "pm":
            h += 12
        time_s = f"{h:02d}:{mi:02d}"
    if not time_s or not time_s.strip() or not time_s[0].isdigit():
        return None  # all-day / tentative rows carry no usable timestamp
    return parse_dt(f"{date_s.strip()} {time_s.strip()}", tz)


def pick(fieldnames: list[str], *cands: str) -> str | None:
    lower = {f.lower().strip(): f for f in fieldnames}
    for c in cands:
        if c in lower:
            return lower[c]
    return None


def load_calendar(path: str, tz, impact_filter: str):
    """Return {currency: sorted list of event datetimes (UTC)} plus metadata.

    Accepts flexible headers: a single datetime column (datetime_utc /
    datetime / timestamp) or separate date + time columns; currency or
    country; impact; event or title.  Times without an offset are
    interpreted in --calendar-tz (default UTC).
    """
    events = []          # (dt, currency, event_name)
    skipped = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        fn = rdr.fieldnames or []
        col_dt = pick(fn, "datetime_utc", "datetime", "timestamp", "date_time")
        col_date = pick(fn, "date", "day")
        col_time = pick(fn, "time", "time_utc")
        col_cur = pick(fn, "currency", "country", "cur", "ccy")
        col_imp = pick(fn, "impact", "importance", "volatility")
        col_evt = pick(fn, "event", "title", "name", "description")
        if col_cur is None or (col_dt is None and col_date is None):
            raise SystemExit(
                f"calendar {path}: need a currency/country column and either a "
                f"datetime column or date+time columns; found {fn}")
        for row in rdr:
            cur = (row.get(col_cur) or "").strip().upper()
            if cur not in CURRENCIES:
                skipped += 1
                continue
            if col_imp is not None and impact_filter != "all":
                imp = (row.get(col_imp) or "").strip().lower()
                if not ("high" in imp or "red" in imp or imp == "3"):
                    skipped += 1
                    continue
            if col_dt is not None:
                dt = parse_dt(row.get(col_dt) or "", tz)
            else:
                dt = parse_date_and_time(row.get(col_date) or "",
                                         (row.get(col_time) or "") if col_time else "", tz)
            if dt is None:
                skipped += 1
                continue
            events.append((dt, cur, (row.get(col_evt) or "").strip() if col_evt else ""))
    by_cur: dict[str, list] = {}
    for dt, cur, name in events:
        by_cur.setdefault(cur, []).append((dt, name))
    for cur in by_cur:
        by_cur[cur].sort()
    return by_cur, len(events), skipped


def events_between(by_cur, currencies, t0, t1):
    """All (dt, currency, event) for the given currencies with t0 <= dt <= t1."""
    out = []
    for cur in currencies:
        evs = by_cur.get(cur, [])
        keys = [e[0] for e in evs]
        for k in range(bisect.bisect_left(keys, t0), bisect.bisect_right(keys, t1)):
            out.append((evs[k][0], cur, evs[k][1]))
    out.sort()
    return out


def bucket_stats(trades):
    n = len(trades)
    wins = sum(1 for t in trades if t["result"] == "win")
    losses = sum(1 for t in trades if t["result"] == "loss")
    rs = [float(t["r_multiple"]) for t in trades]
    return {
        "trades": n, "wins": wins, "losses": losses,
        "scratches": n - wins - losses,
        "win_rate_pct": round(100.0 * wins / n, 2) if n else None,
        "avg_r": round(sum(rs) / n, 3) if n else None,
        "total_r": round(sum(rs), 2),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trades", required=True, help="trades.csv from a backtest run")
    ap.add_argument("--calendar", required=True, help="economic calendar file (see data/news/README.md)")
    ap.add_argument("--calendar-tz", default="UTC",
                    help="IANA timezone of calendar times without an explicit "
                         "offset (default UTC; e.g. America/New_York for a "
                         "ForexFactory export left on Eastern time)")
    ap.add_argument("--window-hours", type=float, default=4.0,
                    help="entry window half-width in hours (default 4)")
    ap.add_argument("--impact", choices=["high", "all"], default="high",
                    help="which calendar rows to use (default: high-impact only)")
    ap.add_argument("--out", default=None,
                    help="output directory (default: news/ next to the trades file)")
    args = ap.parse_args()

    tz = timezone.utc if args.calendar_tz.upper() == "UTC" else ZoneInfo(args.calendar_tz)
    by_cur, n_events, n_skipped = load_calendar(args.calendar, tz, args.impact)
    win = timedelta(hours=args.window_hours)

    with open(args.trades, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["result"] in ("win", "loss", "scratch")]

    P = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    affected, clean = [], []
    annotated = []
    n_entry_win = n_in_trade = 0
    for r in rows:
        curs = (r["pair"][:3], r["pair"][3:])
        entry, exit_ = P(r["entry_date_utc"]), P(r["exit_date_utc"])
        ev_entry = events_between(by_cur, curs, entry - win, entry + win)
        ev_trade = events_between(by_cur, curs, entry, exit_)
        seen = set()
        ev_all = [e for e in ev_entry + ev_trade if not (e in seen or seen.add(e))]
        is_aff = bool(ev_all)
        (affected if is_aff else clean).append(r)
        n_entry_win += bool(ev_entry)
        n_in_trade += bool(ev_trade)
        annotated.append({
            **{k: r[k] for k in ("entry_date_utc", "exit_date_utc", "pair",
                                 "direction", "result", "r_multiple")},
            "news_affected": "yes" if is_aff else "",
            "events_entry_window": len(ev_entry),
            "events_in_trade": len(ev_trade),
            "events": "; ".join(f"{dt:%Y-%m-%d %H:%M} {cur} {name}".strip()
                                for dt, cur, name in ev_all[:6])
                      + (" ..." if len(ev_all) > 6 else ""),
        })

    summary = {
        "trades_file": args.trades,
        "calendar_file": args.calendar,
        "calendar_events_used": n_events,
        "calendar_rows_skipped": n_skipped,
        "window_hours": args.window_hours,
        "news_affected": bucket_stats(affected),
        "clean": bucket_stats(clean),
        "affected_via_entry_window": n_entry_win,
        "affected_via_in_trade": n_in_trade,
        "per_pair": {},
    }
    for p in sorted({r["pair"] for r in rows}):
        summary["per_pair"][p] = {
            "news_affected": bucket_stats([r for r in affected if r["pair"] == p]),
            "clean": bucket_stats([r for r in clean if r["pair"] == p]),
        }

    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.trades)), "news")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "trades_annotated.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(annotated[0].keys()))
        w.writeheader()
        w.writerows(annotated)
    with open(os.path.join(out_dir, "news_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    a, c = summary["news_affected"], summary["clean"]
    print(f"Calendar: {n_events} high-impact events used ({n_skipped} rows skipped)")
    print(f"Window:   +/-{args.window_hours:g}h around entry, plus entry->exit\n")
    print(f"{'':16}{'trades':>8}{'wins':>7}{'losses':>8}{'win rate':>10}{'avg R':>8}{'total R':>9}")
    for name, s in (("news-affected", a), ("clean", c)):
        print(f"{name:<16}{s['trades']:>8}{s['wins']:>7}{s['losses']:>8}"
              f"{(str(s['win_rate_pct'])+'%') if s['win_rate_pct'] is not None else '—':>10}"
              f"{s['avg_r'] if s['avg_r'] is not None else '—':>8}{s['total_r']:>9}")
    print(f"\naffected via entry window: {n_entry_win} | via release during trade: {n_in_trade}")
    print(f"\nPer pair (affected | clean win rate):")
    for p, s in summary["per_pair"].items():
        pa, pc = s["news_affected"], s["clean"]
        print(f"  {p}: {pa['trades']:>3} @ {pa['win_rate_pct']}%  |  {pc['trades']:>3} @ {pc['win_rate_pct']}%")
    print(f"\nOutputs: {out_dir}/trades_annotated.csv, news_summary.json")


if __name__ == "__main__":
    main()
