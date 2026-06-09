# Data Schema

The dashboard expects a single JSON object keyed by ticker symbol. Each value is an array of OHLC bars in chronological order (oldest first).

## Shape

```json
{
  "SPY": [
    { "d": "2020-01-02", "o": 322.13, "h": 322.79, "l": 320.39, "c": 321.55 },
    { "d": "2020-01-03", "o": 320.50, "h": 322.13, "l": 320.04, "c": 321.51 },
    ...
  ],
  "LQD": [
    { "d": "2020-01-02", "o": 128.54, "h": 128.79, "l": 128.41, "c": 128.66 },
    ...
  ],
  ...
}
```

## Field reference

| Field | Type | Meaning |
|---|---|---|
| `d` | string `YYYY-MM-DD` | Trading date |
| `o` | number | Open |
| `h` | number | High |
| `l` | number | Low |
| `c` | number | Close (adjusted preferred — Alvaro uses `auto_adjust=False` then ffill in his Python) |

## Requirements

- **Minimum history per symbol:** 1500 bars (~6 years). The RS rebase point is at index `length - 1500`, so anything shorter breaks the relative line.
- **Recommended:** ~1700+ bars (~7 years) so the 226D (12M) Donchian view also has clean signals.
- **SPY required.** It's the benchmark for the relative-strength calculation. If SPY is missing, the dashboard cannot compute RS.
- **Frequency:** daily, business days only. Weekends/holidays simply don't appear as bars (the dashboard uses `bar.date` directly, not array index, for the chart x-axis).
- **Bar gaps:** OK. Missing bars are skipped, not interpolated.

## Producing this from Alvaro's Python

His script already builds the right dataframes. Add this block after the `data = yf.download(...)` call:

```python
import json

snapshot = {}
for sym in Dict:
    rows = []
    for ts in data.index:
        try:
            o = data['Open'][sym].loc[ts]
            h = data['High'][sym].loc[ts]
            l = data['Low'][sym].loc[ts]
            c = data['Close'][sym].loc[ts]
            if pd.notna(c):
                rows.append({
                    "d": ts.strftime("%Y-%m-%d"),
                    "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                })
        except KeyError:
            continue
    snapshot[sym] = rows

with open("daily-snapshot.json", "w") as f:
    json.dump(snapshot, f, separators=(",", ":"))  # compact

print(f"Wrote {sum(len(v) for v in snapshot.values())} bars across {len(snapshot)} symbols")
```

Then upload `daily-snapshot.json` to wherever the dashboard reads from.

## Size estimate

- 145 symbols × 1700 bars × ~80 bytes/bar = **~20 MB uncompressed**
- gzip drops this to ~3-4 MB
- Cache aggressively — daily data only changes once per day after market close

## Where to host

| Option | Pros | Cons |
|---|---|---|
| **Vercel Blob** | Same platform as the site, fast edge | Storage costs |
| **GitHub release asset** | Free, versioned | Manual upload step |
| **AWS S3** | Cheap, reliable | Extra account/CORS setup |
| **Vercel serverless that proxies yfinance** | Always fresh, no upload step | Rate limits, slower first hit |

Recommendation: Vercel Blob for production. GitHub release for a quick demo.
