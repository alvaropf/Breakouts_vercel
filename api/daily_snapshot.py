"""
/api/daily_snapshot  —  live OHLC feed for the Intermarket Action Sheet.

Same pattern as Beta_Pf's api/strategy.py:
  * Vercel Python serverless function (BaseHTTPRequestHandler)
  * pulls from Yahoo Finance with yfinance
  * caches the computed snapshot in-memory (warm instances) + on the CDN edge
  * returns the exact JSON the dashboard's loadDataFromAPI() expects:

      { "SPY": [ {"d":"YYYY-MM-DD","o":..,"h":..,"l":..,"c":..}, ... ],
        "LQD": [ ... ], ... }

Alvaro-sync note: download uses auto_adjust=False then forward-fill,
matching the Breakouts Report Python pipeline. Flip AUTO_ADJUST to True
if you'd rather feed dividend-adjusted closes.
"""

from http.server import BaseHTTPRequestHandler
import json
import math
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

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
AUTO_ADJUST = False      # match Alvaro's yf.download(auto_adjust=False)

# in-memory cache (survives across warm invocations of the same instance)
_cache = {"data": None, "ts": None}
CACHE_SECONDS = 21600    # 6 hours — daily data only changes once after the close


def _cache_valid() -> bool:
    if _cache["data"] is None or _cache["ts"] is None:
        return False
    return (datetime.utcnow() - _cache["ts"]).total_seconds() < CACHE_SECONDS


def _frame_to_bars(df: pd.DataFrame) -> list:
    """Turn a single-ticker OHLC frame into the dashboard's bar list."""
    if df is None or df.empty:
        return []
    cols = {c for c in df.columns}
    if not {"Open", "High", "Low", "Close"}.issubset(cols):
        return []
    df = df[["Open", "High", "Low", "Close"]].copy()
    df = df.ffill().dropna(subset=["Close"])
    bars = []
    for ts, row in df.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        # skip any row that still has a NaN after ffill
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


def _download_chunk(symbols: list, start: str) -> pd.DataFrame:
    return yf.download(
        symbols,
        start=start,
        interval="1d",
        auto_adjust=AUTO_ADJUST,
        group_by="ticker",
        threads=True,
        progress=False,
    )


def build_snapshot() -> dict:
    start = (datetime.utcnow() - timedelta(days=int(YEARS_HISTORY * 365.25))
             ).strftime("%Y-%m-%d")
    out: dict = {}

    for i in range(0, len(UNIVERSE), CHUNK):
        batch = UNIVERSE[i:i + CHUNK]
        try:
            raw = _download_chunk(batch, start)
        except Exception as e:
            print(f"chunk {batch[0]}.. failed: {e}")
            continue

        # With group_by='ticker' a multi-ticker pull has a MultiIndex on columns;
        # a single surviving ticker collapses to a flat frame.
        if isinstance(raw.columns, pd.MultiIndex):
            present = list(raw.columns.get_level_values(0).unique())
            for sym in batch:
                if sym not in present:
                    continue
                bars = _frame_to_bars(raw[sym])
                if bars:
                    out[sym] = bars
        else:
            # only one ticker came back as a flat frame
            for sym in batch:
                bars = _frame_to_bars(raw)
                if bars:
                    out[sym] = bars
                break

    if "SPY" not in out:
        raise RuntimeError("SPY missing — cannot compute relative strength")

    return out


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if not _cache_valid():
                _cache["data"] = build_snapshot()
                _cache["ts"] = datetime.utcnow()
            body = json.dumps(_cache["data"], separators=(",", ":"))
            status = 200
        except Exception as e:
            import traceback
            body = json.dumps({"error": str(e), "traceback": traceback.format_exc()})
            status = 500

        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        # edge-cache for 6h, serve stale for a day while revalidating
        self.send_header("Cache-Control", "public, s-maxage=21600, stale-while-revalidate=86400")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, *args):
        pass
