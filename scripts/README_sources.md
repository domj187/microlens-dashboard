# Where the price data comes from

Two fetchers write the same output format into `data/`
(`{PAIR}_{60,240,480,1D}.csv`, `time,open,high,low,close`):

| Script | Source | Account needed | Status |
|---|---|---|---|
| `fetch_oanda.py` | OANDA v20 REST API | free practice account | **preferred** |
| `fetch_dukascopy.py` | Dukascopy public datafeed | none | original; rate-limited |

## Why OANDA was chosen for the additional pairs

Dukascopy's public feed returns 503s under sustained use — it serves one
gzip file per pair per **hour** of history, so three years of one pair is
~26,000 requests, and their throttling starts refusing long before that.
That is a structural mismatch with what we need, not bad luck.

OANDA's v20 API solves the same problem in four requests per pair:

- **Volume.** `count=5000` candles per request at `granularity=H1`, so 3
  years (~18,700 hourly bars) is 4 calls. The published budget is ~120
  requests/minute — three orders of magnitude more headroom than we use.
- **Same provenance as the reference data.** The repo's original exports
  (`OANDA_*.csv` in the root) came from OANDA, and `data/README.md`
  records that the 4H/8H/1D bucketing convention was validated against
  them. Fetching from OANDA keeps new pairs on the same pricing source as
  the files everything was checked against — no cross-provider quote
  differences to reason about.
- **Bid and ask in one pass.** `price=MBA` returns mid, bid and ask
  together, which is what `backtest/pair_character.py` needs to measure
  spread. Dukascopy needs a separate full download per side.
- **JPY pairs work without special casing.** Prices arrive as decimal
  strings; `fetch_dukascopy.py` hardcodes `PRICE_SCALE = 1e5`, which is
  wrong for JPY pairs (they need 1e3) — one more reason it could not have
  served GBPJPY/USDJPY as written.
- **A permanent free token.** A practice account is free, needs no
  funding, and its token does not expire. Practice and live share the same
  historical candle data.

### The alternatives, and why not

- **HistData.com** — genuinely free and no account, but it serves M1 data
  as monthly ZIPs behind a form POST with a referer check, timestamped in
  EST-without-DST, which has to be converted before bucketing. Workable as
  a fallback; more moving parts to get subtly wrong, and no bid/ask.
- **Stooq** — clean CSV endpoints, but only daily bars for FX. We need 1H
  to build the 4H/8H/1D set, so it cannot serve this pipeline at all.
- **Polygon.io / Alpha Vantage / Twelve Data free tiers** — all cap either
  history depth (often 2 years, short of the 3 we want) or request rate
  (5/min) tightly enough that they are worse than what we have.
- **TrueFX** — free tick archives, but registration plus tick-to-bar
  aggregation for volumes far beyond what this needs.

## Using it

```bash
# one-time: free practice account at oanda.com -> Manage API Access -> token
export OANDA_API_TOKEN=xxxxxxxx-yyyyyyyyyyyy

python3 scripts/fetch_oanda.py --pairs GBPUSD USDJPY GBPJPY EURGBP
python3 scripts/fetch_oanda.py --pairs GBPUSD --price all   # + data_bid/ data_ask/
python3 scripts/fetch_oanda.py --offline                    # rebuild from cache
```

Raw API pages are cached under `data/raw_oanda/` (gitignored), so re-runs
and `--offline` rebuilds cost nothing and a interrupted fetch resumes.

## What has and has not been verified

The candle pipeline is verified end to end: OANDA-shaped JSON fed through
`--offline` reproduces `data/EURUSD_{60,240,480,1D}.csv` **byte for byte**
against the committed files, including dropping `complete: false` candles,
RFC3339 nanosecond timestamps, the 17:00 NY bucketing and the price
formatting. JPY 3-decimal prices and the bid/ask/spread path were checked
the same way.

The **live HTTP call itself is unverified** — `api-fxpractice.oanda.com` is
blocked by this development environment's network policy, so the request
URL, headers and pagination were written against the v20 specification but
never executed against the real service. If a parameter is off, the script
fails loudly with the API's own error text rather than writing bad data.
