"""
DXY Brain — Dollar Index & Macro Data for Gold Trading
────────────────────────────────────────────────────────
Gold and DXY have ~80% inverse correlation.
DXY UP   → Dollar stronger → Gold usually FALLS
DXY DOWN → Dollar weaker   → Gold usually RISES

Data Sources (in priority order):
  1. FRED API  — if FRED_API_KEY set in .env (most accurate)
  2. yfinance  — DX-Y.NYB ticker (free, no key needed, works now)

FRED Series used:
  DTWEXBGS — Trade-Weighted Dollar Index (DXY equivalent)
  FEDFUNDS  — Fed Funds Rate
  DGS10     — 10-Year Treasury Yield
  DFII10    — 10-Year Real Interest Rate (best gold predictor)
  CPIAUCSL  — CPI Inflation

Get free FRED key: https://fred.stlouisfed.org → My Account → API Keys
"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# ── FRED series we care about ──────────────────────────
FRED_SERIES = {
    "dxy":           "DTWEXBGS",   # Trade-weighted dollar index
    "fed_rate":      "FEDFUNDS",   # Fed funds rate
    "yield_10y":     "DGS10",      # 10-year treasury yield
    "real_rate":     "DFII10",     # Real interest rate (best gold predictor)
    "cpi":           "CPIAUCSL",   # Consumer price index
}


# ══════════════════════════════════════════════════════
# FRED API (when key is set)
# ══════════════════════════════════════════════════════

async def _fetch_fred_series(series_id: str, api_key: str,
                              limit: int = 5) -> list[dict]:
    """Fetch latest N observations for a FRED series."""
    import httpx
    try:
        params = {
            "series_id":   series_id,
            "api_key":     api_key,
            "file_type":   "json",
            "sort_order":  "desc",
            "limit":       limit,
        }
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(FRED_BASE, params=params)
            if r.status_code == 200:
                obs = r.json().get("observations", [])
                return [
                    {"date": o["date"], "value": float(o["value"])}
                    for o in obs
                    if o.get("value") not in (".", "", None)
                ]
    except Exception as e:
        logger.debug(f"FRED {series_id}: {e}")
    return []


async def get_fred_data(api_key: str) -> dict:
    """Fetch all macro series from FRED in parallel."""
    results = await asyncio.gather(
        *[_fetch_fred_series(sid, api_key) for sid in FRED_SERIES.values()],
        return_exceptions=True
    )
    out = {}
    for key, data in zip(FRED_SERIES.keys(), results):
        if isinstance(data, list) and data:
            out[key] = data[0]["value"]           # most recent
            if len(data) >= 2:
                out[f"{key}_prev"] = data[1]["value"]  # previous reading
    return out


# ══════════════════════════════════════════════════════
# YFINANCE FALLBACK (no API key needed)
# ══════════════════════════════════════════════════════

async def get_dxy_yfinance() -> dict:
    """
    Get DXY from yfinance — free, no key, works immediately.
    Returns current, previous, change, trend, and 20-day MA.
    """
    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")

    def _fetch():
        try:
            ticker = yf.Ticker("DX-Y.NYB")
            h = ticker.history(period="30d", interval="1d")
            if h is None or h.empty:
                return None
            closes = h["Close"].dropna()
            current  = float(closes.iloc[-1])
            prev     = float(closes.iloc[-2]) if len(closes) >= 2 else current
            ma20     = float(closes.tail(20).mean())
            change   = round(current - prev, 3)
            change_pct = round((current - prev) / prev * 100, 2)
            # 5-day trend
            week_ago = float(closes.iloc[-5]) if len(closes) >= 5 else prev
            trend_5d = round(current - week_ago, 3)
            return {
                "current":    round(current, 3),
                "prev":       round(prev, 3),
                "change":     change,
                "change_pct": change_pct,
                "ma20":       round(ma20, 3),
                "above_ma20": current > ma20,
                "trend_5d":   trend_5d,
                "source":     "yfinance",
            }
        except Exception as e:
            logger.debug(f"DXY yfinance: {e}")
            return None

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch) or {}


# ══════════════════════════════════════════════════════
# MAIN: GET ALL MACRO DATA
# ══════════════════════════════════════════════════════

async def get_macro_context() -> dict:
    """
    Returns full macro context for gold trading decisions.
    Uses FRED if key set, otherwise yfinance for DXY.
    """
    from config.settings import settings
    fred_key = getattr(settings, "fred_api_key", "") or ""

    result = {
        "dxy":            None,
        "dxy_change":     None,
        "dxy_trend":      "UNKNOWN",
        "fed_rate":       None,
        "yield_10y":      None,
        "real_rate":      None,
        "cpi":            None,
        "gold_bias":      "NEUTRAL",   # BULLISH / BEARISH / NEUTRAL
        "impact":         "neutral",
        "summary":        "",
        "source":         "none",
    }

    # ── Try FRED first ────────────────────────────────
    if fred_key:
        try:
            fred = await get_fred_data(fred_key)
            if fred.get("dxy"):
                result.update({
                    "dxy":        fred.get("dxy"),
                    "dxy_change": round(fred.get("dxy", 0) - fred.get("dxy_prev", fred.get("dxy", 0)), 3),
                    "fed_rate":   fred.get("fed_rate"),
                    "yield_10y":  fred.get("yield_10y"),
                    "real_rate":  fred.get("real_rate"),
                    "cpi":        fred.get("cpi"),
                    "source":     "FRED",
                })
                logger.debug("Macro data from FRED ✅")
        except Exception as e:
            logger.debug(f"FRED failed: {e}")

    # ── Fallback to yfinance for DXY ──────────────────
    if result["dxy"] is None:
        dxy = await get_dxy_yfinance()
        if dxy.get("current"):
            result.update({
                "dxy":        dxy["current"],
                "dxy_change": dxy["change"],
                "dxy_ma20":   dxy.get("ma20"),
                "dxy_above_ma20": dxy.get("above_ma20"),
                "dxy_trend_5d":   dxy.get("trend_5d"),
                "source":     "yfinance",
            })
            logger.debug(f"DXY from yfinance: {dxy['current']:.3f} ({dxy['change']:+.3f})")

    # ── Interpret DXY for gold bias ───────────────────
    if result["dxy"] is not None:
        dxy     = result["dxy"]
        change  = result["dxy_change"] or 0

        # DXY level context (historical: 100+ = strong dollar)
        if dxy > 104:
            level_note = "very strong dollar"
        elif dxy > 101:
            level_note = "strong dollar"
        elif dxy > 98:
            level_note = "neutral dollar"
        elif dxy > 95:
            level_note = "weak dollar"
        else:
            level_note = "very weak dollar"

        # Direction-based gold impact
        if change <= -0.3:
            result["dxy_trend"]  = "FALLING"
            result["gold_bias"]  = "BULLISH"
            result["impact"]     = "bullish"
            result["summary"]    = (
                f"DXY {dxy:.2f} falling ({change:+.2f}) — {level_note}. "
                f"Weak dollar = BULLISH for gold."
            )
        elif change >= 0.3:
            result["dxy_trend"]  = "RISING"
            result["gold_bias"]  = "BEARISH"
            result["impact"]     = "bearish"
            result["summary"]    = (
                f"DXY {dxy:.2f} rising ({change:+.2f}) — {level_note}. "
                f"Strong dollar = BEARISH for gold."
            )
        else:
            result["dxy_trend"]  = "FLAT"
            result["gold_bias"]  = "NEUTRAL"
            result["impact"]     = "neutral"
            result["summary"]    = (
                f"DXY {dxy:.2f} flat ({change:+.2f}) — {level_note}. "
                f"Dollar neutral — no macro bias."
            )

        # Add real rate context if available
        if result.get("real_rate") is not None:
            rr = result["real_rate"]
            if rr > 2.0:
                result["summary"] += f" Real rate {rr:.1f}% (high) = headwind for gold."
                result["gold_bias"] = "BEARISH" if result["gold_bias"] == "NEUTRAL" else result["gold_bias"]
            elif rr < 0.5:
                result["summary"] += f" Real rate {rr:.1f}% (low) = tailwind for gold."
                result["gold_bias"] = "BULLISH" if result["gold_bias"] == "NEUTRAL" else result["gold_bias"]

    return result


# ══════════════════════════════════════════════════════
# PROMPT HELPER — for AI confirmation
# ══════════════════════════════════════════════════════

async def get_macro_prompt() -> str:
    """
    Returns a one-line macro context string to inject into AI prompts.
    Example: "DXY=98.23 FALLING (+bearish for gold) | Rate=5.33% | CPI=3.1%"
    """
    macro = await get_macro_context()
    if not macro.get("dxy"):
        return "Macro: DXY unavailable"

    parts = [f"DXY={macro['dxy']:.2f} {macro['dxy_trend']} ({macro['impact'].upper()} for gold)"]
    if macro.get("fed_rate"):
        parts.append(f"FedRate={macro['fed_rate']:.2f}%")
    if macro.get("yield_10y"):
        parts.append(f"10Y={macro['yield_10y']:.2f}%")
    if macro.get("cpi"):
        parts.append(f"CPI={macro['cpi']:.1f}")
    return " | ".join(parts)


if __name__ == "__main__":
    async def test():
        print("\n=== DXY BRAIN TEST ===\n")
        macro = await get_macro_context()
        print(f"DXY:       {macro.get('dxy', 'N/A')}")
        print(f"Change:    {macro.get('dxy_change', 'N/A'):+.3f}" if macro.get('dxy_change') else "Change:    N/A")
        print(f"Trend:     {macro.get('dxy_trend')}")
        print(f"Gold Bias: {macro.get('gold_bias')}")
        print(f"Summary:   {macro.get('summary')}")
        print(f"Source:    {macro.get('source')}")
        print()
        prompt_line = await get_macro_prompt()
        print(f"Prompt:    {prompt_line}")
        print("\n✅ DXY Brain operational!")

    asyncio.run(test())
