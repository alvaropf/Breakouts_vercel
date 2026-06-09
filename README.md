# Intermarket Action Sheet

CIO-focused breakout/breakdown scanner across ~144 ETFs, grouped by asset class. Built for DeLorean Partners, synced to Alvaro Pascual's Python pipeline.

**Live (Vercel):** `https://dl-intermarket.vercel.app` *(after deploy)*

---

## What it shows

A two-column, two-row-per-asset-class grid:

```
                  ↑ Up                          ↓ Down
─────────────────────────────────────────────────────────────────
Bonds   REL  ★[IEF] [LQD]                       [HYG]
9 ETFs  ABS   [IEF]                              [HYG] [EMB]
─────────────────────────────────────────────────────────────────
China   REL   [KWEB]                             [CNYA]
6 ETFs  ABS   [KWEB] [3110]
```

**REL row (top, prominent):** RS-vs-SPY broke its 121D/226D Donchian band
**ABS row (bottom, muted):** Price broke its own 121D/226D Donchian band

**Chip encodings:**
- **Outlined** = single signal (REL only or ABS only)
- **Solid filled** = confluence (REL + ABS same direction — high conviction)
- **★ Gold ring** = life-high in that signal (no overhead supply — strongest signal)
- **☠ Gold ring** = life-low in that signal (no support — strongest breakdown)

The eye reads REL first (relative leadership matters more), then ABS underneath for confirmation. Life-high names sort to the top-left of every sub-row.

---

## Formula sync with Alvaro's Python

| Concept | Implementation |
|---|---|
| Absolute Donchian | `UCL = Close.rolling(N).max().shift(1)`, `LCL = .min().shift(1)`, N=121 (6M) / 226 (12M) |
| Relative line | `RS = (A / A.iloc[-1500]) / (B / B.iloc[-1500]) × 100` |
| Relative Donchian | Same Donchian on RS line, N=121 |
| ATR | True Range → `EMA(span=14, adjust=False)`, α=2/15 |
| RSI(14) | Wilder smoothing |
| Stop | `Px − 3 × ATR` |
| Signal read | `Signal_Donchian.iloc[-2]` (T-1, yesterday's close) |

**⚠ One thing to confirm with Alvaro:** his script uses `Signal_Donchian.iloc[-loockback]` for the relative signal extraction, but `loockback` is undefined in the file he sent (probably set in an earlier notebook cell). This dashboard assumes `-2` for consistency with the absolute signal. If he confirms it's `-1`, change line `i = bars.length - 2` to `i = bars.length - 1` inside the `classify()` function (one place).

---

## Universe

145 ETFs from Alvaro's `ETF_PreselectedList.csv`, across 9 asset classes:

- **Bonds (9):** LQD, IEF, AGG, EMB, TLT, SHY, MUB, HYG, IAGG
- **China (6):** CNYA, KWEB, MCHI, 2822.HK, 3110.HK, CQQQ
- **Commodities (29):** GLD, SLV, GDX, COPX, USO, DBC, …
- **Cripto (2):** BTC-USD, ETH-USD
- **DM (22):** EWJ, EWG, EWA, IEUR, EWC, …
- **EM (22):** EEM, INDA, EWZ, EWY, EWT, …
- **India (2):** EPI, SMIN
- **Tech (8):** SMH, IGM, AIQ, ARKK, IGV, …
- **US (47):** SPY (benchmark), QQQ, sector SPDRs, IWM, …

SPY is excluded from signals (it's the benchmark for the RS calculation).

---

## Live data — wired (Python + yfinance, Beta_Pf style)

**This build is already live.** `index.html` ships with `MODE = 'live'` and
fetches real OHLC from a Python serverless function on Vercel —
the same architecture as the Beta_Pf dashboard (`api/strategy.py`):

```
api/daily_snapshot.py   ← yfinance pull → JSON snapshot, in-memory + edge cached
index.html              ← loadDataFromAPI() fetches /api/daily_snapshot
requirements.txt        ← yfinance, pandas (Vercel auto-installs)
vercel.json             ← routes the .py file, sets function memory/timeout
```

### How it works

`GET /api/daily_snapshot` returns the schema in `DATA_SCHEMA.md`:

```json
{ "SPY": [{ "d": "2024-01-02", "o": 322.1, "h": 322.8, "l": 320.4, "c": 321.6 }, ...],
  "LQD": [ ... ], ... }
```

- Downloads the full 145-ETF universe + SPY with one batched `yf.download`
  per chunk of 25 tickers (`group_by='ticker'`, `threads=True`).
- `auto_adjust=False` then forward-fill — **matches Alvaro's pipeline.**
  Flip `AUTO_ADJUST = True` in `api/daily_snapshot.py` for dividend-adjusted closes.
- ~8 years of history per symbol (schema wants 1500 bars min / 1700 recommended).
- Caches the result in-memory for 6h on warm instances, and sets
  `s-maxage=21600, stale-while-revalidate=86400` so Vercel's edge serves it
  from cache between daily updates.
- Any ticker that fails to load is skipped, never fatal — except **SPY**,
  which is required for the relative-strength line (the function 500s without it).
  A few of Alvaro's tickers (`2822.HK`, `AIGL.L`, `TINM.L`, `COFF.L`, `COCO.L`,
  `NICK.L`) have spottier Yahoo coverage and may drop out on any given day.

### To go back to the synthetic universe

Set `MODE = 'mock'` near the top of the `<script>` in `index.html`.

### Alternative: pre-baked snapshot from Alvaro's own script

If you'd rather not hit Yahoo at request time, have Alvaro's `Breakouts Report`
write `daily-snapshot.json` (the block in `DATA_SCHEMA.md` does exactly this),
host it (Vercel Blob / S3 / GitHub release), and point `ENDPOINT` in
`loadDataFromAPI()` at that URL instead of `/api/daily_snapshot`.

---



## Deployment

### Vercel (one-click)

1. Push this repo to GitHub
2. On Vercel: New Project → Import the repo → keep all defaults
3. Deploy

No build step. Vercel serves `index.html` statically and **auto-detects**
`api/daily_snapshot.py` as a Python serverless function (it installs
`requirements.txt` for you). No `functions` block is needed in `vercel.json` —
the same zero-config Python setup Beta_Pf uses.

**Timeout note:** the cold call fetches 146 tickers from Yahoo, which can take
20–50s. The Hobby plan's default function limit is 60s, which usually covers it;
after the first call the 6-hour edge cache makes every subsequent hit instant.
If a cold pull ever times out, raise the limit in the Vercel dashboard
(Project → Settings → Functions → Max Duration) — Pro allows up to 300s.

### Run locally

```bash
# static UI with mock data — just open the file
open index.html

# full stack (static + Python /api) locally:
npx vercel dev      # needs the Vercel CLI; runs the Python function too
```

To test the endpoint alone once deployed: `curl https://<your-app>.vercel.app/api/daily_snapshot | head -c 400`

---

## What the CIO sees in one screen

- **4 summary tiles** (top): Universe size, Rel↑/Abs↑ counts + life-highs, Rel↓/Abs↓ + life-lows, Net Bias
- **Asset class rotation grid** (main): 9 rows × 2 sub-rows each (REL / ABS), 2 columns (↑/↓)
- **Lookback toggle**: 121D (6-month) or 226D (12-month) Donchian
- **Class filter chips**: All / Bonds / China / Commodities / Cripto / DM / EM / India / Tech / US
- **Detail + Charts view**: full Alvaro-style price + MA + Donchian-flip-marker charts per ETF

---

## Open items

- [ ] Confirm `iloc[-2]` vs `iloc[-1]` with Alvaro
- [x] Wire live data — Python/yfinance serverless function (`api/daily_snapshot.py`)
- [x] `EWS` included in the universe (Bonds/DM section)
- [ ] Optional: ratio panels (Gold/SPY, Copper/Gold, HYG/IEF, EWZ/SPY) for explicit Murphy/Pring intermarket pairs
- [ ] Optional: virtualize the detail view for the full 144-chart render (currently renders all when active — works but heavy)

---

## Credits

Formulas, universe, and signal logic by **Alvaro Pascual** (alvaropascualf@gmail.com).
Dashboard UI by Amit Bhartia / DeLorean Partners.

Built in the spirit of **John Murphy's *Intermarket Technical Analysis*** and **Martin Pring's** action-sheet format — relative strength as the primary signal, absolute as confirmation.

License: MIT.
