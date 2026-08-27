# Live trade journal

Log every setup you see — taken **and** skipped — in the same shape the
backtester emits, so your real decisions can be compared against what the
system would have done.

Two ways to keep it:

- **The journal page** (published Artifact) — log setups in a form, it
  keeps the entries, shows running stats, and exports this exact CSV.
- **`journal_template.csv`** — the same columns for a spreadsheet.
  Keep your working copy as `journal/journal.csv` (gitignored by default;
  commit it if you want the history in git).

## Columns

Nine of these carry the **same names and meanings** as
`backtest/results/*/trades.csv`, so the two files line up column for column.

| Column | Meaning | Matches backtester |
|---|---|---|
| `signal_time_utc` | when the setup triggered (1H BOS close) | ~ `bos_time_utc` |
| `entry_date_utc` | fill time, `YYYY-MM-DD HH:MM` UTC — blank if skipped | ✓ |
| `exit_date_utc` | close time — blank while open | ✓ |
| `pair` | AUDCHF / AUDUSD / EURCHF / EURUSD | ✓ |
| `direction` | `long` / `short` | ✓ |
| `taken` | `yes` / `no` — **journal only**, the decision under test | — |
| `reason` | why you took it or passed | journal only |
| `entry` / `sl` / `tp` | planned or actual levels | ✓ |
| `result` | `win` / `loss` / `partial` / `scratch` / `open`, or `skipped-would-*` | ✓ |
| `r_multiple` | realised R (`+2`, `-1`, `+0.5`, `0`) | ✓ |
| `notes` | anything else — news, emotion, execution quality | journal only |

Use `skipped-would-win` / `skipped-would-loss` / `skipped-would-scratch`
on rows you passed on, once you know how they resolved. That is what makes
the skip decisions measurable rather than invisible.

## Comparing against the system

```bash
python3 journal/journal_compare.py \
    --journal journal/journal.csv \
    --trades backtest/results/origin-swing-partial/trades.csv
```

It matches journal rows to backtest trades on pair + entry time (within a
tolerance window) and reports:

- **Both traded** — you and the system took it; agreement on direction and R.
- **You only** — discretionary trades the system never signalled.
- **System only** — signals you passed on, with the R you left on the table.
- Realised R for your taken trades vs the system over the same period,
  and what your skipped-would-* rows would have added.
