"""
app.py — Vercel Python entrypoint for the Intermarket Action Sheet.

Vercel's current Python runtime auto-detects an ASGI app named `app` in one of
the default entrypoints (app.py / index.py / server.py / main.py). A static,
frameworkless project doesn't get the old per-file `api/*.py` model, so the live
OHLC feed is served from this single FastAPI app instead:

    GET /api/daily_snapshot   → real OHLC JSON pulled from Yahoo Finance (yfinance)
    GET /                     → the dashboard (index.html)

Same spirit as Beta_Pf: a Python serverless function that pulls from yfinance,
caches the result, and feeds the frontend. Snapshot schema (see DATA_SCHEMA.md):

    { "SPY": [ {"d":"YYYY-MM-DD","o":..,"h":..,"l":..,"c":..}, ... ], "LQD": [...], ... }
"""

import os
import math
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse

app = FastAPI()
_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Universe — Alvaro's ETF_PreselectedList (145 ETFs + SPY benchmark) ─────────
# Kept byte-for-byte in sync with the UNIVERSE array in index.html.
UNIVERSE = [
    "SPY",  # benchmark — required for the relative-strength line
    # Bonds
    "LQD", "IEF", "AGG", "EMB", "TLT", "SHY", "MUB", "HYG", "IAGG",
    # China
    "CNYA", "KWEB", "MCHI", "2822.HK", "3110.HK", "CQQQ",
    # Commodities
    "CANE", "MOO", "PICK", "GNR", "LIT", "ICLN", "URA", "XME", "GDX",
    "AIGL.L", "TINM.L", "COFF.L", "COCO.L", "COPX", "CPER", "NICK.L",
    "PALL", "CORN", "DBA", "PPLT", "USO", "GSG", "SLV", "GLTR", "UGA",
    "UNG", "WEAT", "DBC", "GLD",
    # Cripto
    "ETH-USD", "BTC-USD",
    # DM
    "IXJ", "EWS", "IEFA", "VPL", "EWD", "EWN", "EWL", "EWP", "IGF",
    "EFV", "IEUR", "EWA", "EWJ", "EWQ", "ACWI", "ACWX", "EWG", "VEA",
    "EWC", "EWI", "EWU", "FEZ", "IQLT",
    # EM
    "ECH", "GREK", "UAE", "ARGT", "THD", "EPHE", "AIA", "INDA", "EZA",
    "TUR", "EWW", "EWZ", "EMXC", "EWT", "EEM", "EWY", "AAXJ", "ILF",
    "EPOL", "EIDO", "KSA", "EWM",
    # India
    "EPI", "SMIN",
    # Tech
    "SMH", "IGM", "AIQ", "TAN", "SKYY", "IGV", "ARKK", "CIBR",
    # US
    "JETS", "IWN", "IBB", "VBK", "IAT", "ITA", "IYT", "XTL", "PRN",
    "AIRR", "RSP", "OEF", "XLK", "XLY", "VTV", "RWR", "ITB", "IHI",
    "XRT", "MGK", "QQQ", "DIA", "IWM", "XLI", "XLV", "XLU", "VTI",
    "XLP", "XLC", "XLB", "XLE", "IJR", "XBI", "IWD", "XOP", "IJH",
    "XLF", "KRE", "IWF", "XLRE", "IAK", "KBE", "QUAL", "UFO",
]

YEARS_HISTORY = 8        # ~2000 bars; schema wants 1500 min / 1700 recommended
CHUNK = 25               # download in batches so one bad ticker can't sink it all
AUTO_ADJUST = False      # match Alvaro's yf.download(auto_adjust=False); set True for adjusted closes

# in-memory cache — persists across warm invocations of the same instance
_cache = {"data": None, "ts": None}
CACHE_SECONDS = 21600    # 6 hours — daily data only changes once after the close


def _cache_valid() -> bool:
    if _cache["data"] is None or _cache["ts"] is None:
        return False
    return (datetime.utcnow() - _cache["ts"]).total_seconds() < CACHE_SECONDS


def _frame_to_bars(df: pd.DataFrame) -> list:
    """Turn a single-ticker OHLC frame (already on the shared business-day
    index) into the dashboard's bar list. Drops only leading rows where the
    ticker has no close yet (pre-inception); the rest is dense."""
    if df is None or df.empty:
        return []
    if not {"Open", "High", "Low", "Close"}.issubset(set(df.columns)):
        return []
    df = df[["Open", "High", "Low", "Close"]].dropna(subset=["Close"])
    bars = []
    for ts, row in df.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (o, h, l, c)):
            continue
        bars.append({
            "d": ts.strftime("%Y-%m-%d"),
            "o": round(float(o), 4),
            "h": round(float(h), 4),
            "l": round(float(l), 4),
            "c": round(float(c), 4),
        })
    return bars


def build_snapshot() -> dict:
    """Download the universe, then apply Alvaro's exact calendar alignment:

        data = yf.download(...).ffill().resample('B').last().ffill()

    This puts EVERY ticker on one shared business-day index (Mon–Fri, holidays
    forward-filled). It's what makes the relative-strength line line up with SPY
    bar-for-bar — without it, crypto (BTC/ETH, which trade weekends) and the
    .HK names (different holiday calendar) drift out of position against SPY and
    produce wrong relative signals.
    """
    start = (datetime.utcnow() - timedelta(days=int(YEARS_HISTORY * 365.25))
             ).strftime("%Y-%m-%d")

    frames = []  # one per chunk, columns = MultiIndex (ticker, field)
    for i in range(0, len(UNIVERSE), CHUNK):
        batch = UNIVERSE[i:i + CHUNK]
        try:
            raw = yf.download(
                batch, start=start, interval="1d",
                auto_adjust=AUTO_ADJUST, group_by="ticker",
                threads=True, progress=False,
            )
        except Exception as e:
            print(f"chunk {batch[0]}.. failed: {e}")
            continue
        if raw is None or raw.empty:
            continue
        # Normalise a single-ticker (flat-column) result into MultiIndex form
        if not isinstance(raw.columns, pd.MultiIndex):
            only = [s for s in batch if s]  # single requested symbol
            raw.columns = pd.MultiIndex.from_product([[only[0]], raw.columns])
        frames.append(raw)

    if not frames:
        raise RuntimeError("no data returned from Yahoo")

    # Combine all tickers into one frame, then Alvaro's alignment, verbatim:
    data = pd.concat(frames, axis=1, sort=False)
    data = data[~data.index.duplicated(keep="last")].sort_index()
    data = data.ffill().resample("B").last().ffill()
    data.index = pd.DatetimeIndex(data.index).normalize()

    present = list(data.columns.get_level_values(0).unique())
    out: dict = {}
    for sym in UNIVERSE:
        if sym not in present:
            continue
        bars = _frame_to_bars(data[sym])
        if bars:
            out[sym] = bars

    if "SPY" not in out:
        raise RuntimeError("SPY missing — cannot compute relative strength")
    return out


def cached_snapshot() -> dict:
    if not _cache_valid():
        _cache["data"] = build_snapshot()
        _cache["ts"] = datetime.utcnow()
    return _cache["data"]


@app.get("/api/daily_snapshot")
def daily_snapshot():
    try:
        data = cached_snapshot()
    except Exception as e:
        import traceback
        return JSONResponse(
            {"error": str(e), "traceback": traceback.format_exc()},
            status_code=500,
        )
    return JSONResponse(
        data,
        headers={
            "Cache-Control": "public, s-maxage=21600, stale-while-revalidate=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/", response_class=HTMLResponse)
def index():
    """Fallback HTML serve — Vercel's edge usually serves the static index.html
    directly, but this keeps the app self-sufficient if it ever catches '/'."""
    path = os.path.join(_HERE, "index.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return PlainTextResponse("index.html not found", status_code=404)
