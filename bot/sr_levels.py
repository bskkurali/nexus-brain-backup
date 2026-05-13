"""
Multi-Timeframe Support & Resistance
─────────────────────────────────────
Detects key S/R levels from H1, H4, D1 using pivot swing points.
Clusters nearby levels so noise is removed.
Caches per timeframe to avoid hammering yfinance every 5 min.

Usage (in any strategy):
    from bot.sr_levels import get_sr_levels, sr_score
    sr = await get_sr_levels(current_price, atr)
    adj, note = sr_score(sr, "BUY", current_price, atr)
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np
from loguru import logger


# ── Cache ──────────────────────────────────────────────
_cache: dict = {}          # key → (timestamp, data)
_CACHE_TTL = {
    "1h":  3600,           # H1 levels refresh every 1h
    "1d":  86400,          # D1 levels refresh every 24h
}


# ── Data structures ────────────────────────────────────

@dataclass
class SRLevel:
    price:     float
    strength:  int          # how many times touched (1=weak, 3+=strong)
    timeframe: str          # "H1", "H4", "D1"
    level_type: str         # "support" or "resistance"


@dataclass
class SRData:
    supports:    List[SRLevel] = field(default_factory=list)
    resistances: List[SRLevel] = field(default_factory=list)
    nearest_sup: float = 0.0    # closest support below price
    nearest_res: float = 0.0    # closest resistance above price
    at_support:  bool  = False  # price within 1 ATR of support
    at_resistance: bool = False # price within 1 ATR of resistance
    sup_distance_atr: float = 99.0   # distance to nearest support in ATR units
    res_distance_atr: float = 99.0   # distance to nearest resistance in ATR units
    levels_h1:   List[float] = field(default_factory=list)
    levels_d1:   List[float] = field(default_factory=list)


# ── Fetch helpers ──────────────────────────────────────

async def _fetch_tf(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """Fetch OHLCV with caching."""
    cache_key = f"{symbol}_{interval}"
    now = time.time()
    ttl = _CACHE_TTL.get(interval, 3600)

    if cache_key in _cache:
        ts, df = _cache[cache_key]
        if now - ts < ttl:
            return df

    try:
        import yfinance as yf
        import warnings
        warnings.filterwarnings("ignore")

        def _dl():
            df = yf.download(symbol, period=period, interval=interval,
                             progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                              for c in df.columns]
                return df[["open","high","low","close"]].dropna()
            return pd.DataFrame()

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, _dl)
        if not df.empty:
            _cache[cache_key] = (now, df)
            return df
    except Exception as e:
        logger.debug(f"SR fetch {interval}: {e}")

    return pd.DataFrame()


# ── Pivot detection ────────────────────────────────────

def _find_pivots(df: pd.DataFrame, left: int = 5, right: int = 5):
    """Find swing highs and lows using left/right bar lookback."""
    highs, lows = [], []
    n = len(df)
    for i in range(left, n - right):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        if (df["high"].iloc[i-left:i].max() < h and
                df["high"].iloc[i+1:i+right+1].max() < h):
            highs.append(h)
        if (df["low"].iloc[i-left:i].min() > l and
                df["low"].iloc[i+1:i+right+1].min() > l):
            lows.append(l)
    return highs, lows


# ── Clustering ─────────────────────────────────────────

def _cluster(levels: List[float], threshold_pct: float = 0.25) -> List[tuple]:
    """
    Merge nearby levels within threshold_pct%.
    Returns list of (price, strength) tuples.
    """
    if not levels:
        return []
    levels = sorted(levels)
    clusters = []
    group = [levels[0]]
    for lvl in levels[1:]:
        if (lvl - group[-1]) / group[-1] * 100 < threshold_pct:
            group.append(lvl)
        else:
            clusters.append((sum(group)/len(group), len(group)))
            group = [lvl]
    clusters.append((sum(group)/len(group), len(group)))
    return clusters


# ── Main async function ────────────────────────────────

async def get_sr_levels(current_price: float, atr: float) -> SRData:
    """
    Calculate S/R levels from H1 and D1 timeframes.
    Returns SRData with nearest support/resistance and proximity flags.
    """
    sr = SRData()
    all_highs, all_lows = [], []

    # ── H1 levels (medium-term, 30 days) ──────────────
    df_h1 = await _fetch_tf("GC=F", "1h", "30d")
    if not df_h1.empty and len(df_h1) >= 15:
        h1_highs, h1_lows = _find_pivots(df_h1, left=4, right=4)
        all_highs.extend(h1_highs)
        all_lows.extend(h1_lows)
        sr.levels_h1 = sorted(set(round(x, 1) for x in h1_highs + h1_lows))
        logger.debug(f"SR H1: {len(h1_highs)} highs, {len(h1_lows)} lows")

    # ── D1 levels (major, 180 days) ────────────────────
    df_d1 = await _fetch_tf("GC=F", "1d", "180d")
    if not df_d1.empty and len(df_d1) >= 10:
        d1_highs, d1_lows = _find_pivots(df_d1, left=3, right=3)
        # D1 levels count triple — they're the most important
        all_highs.extend(d1_highs * 3)
        all_lows.extend(d1_lows * 3)
        sr.levels_d1 = sorted(set(round(x, 1) for x in d1_highs + d1_lows))
        logger.debug(f"SR D1: {len(d1_highs)} highs, {len(d1_lows)} lows")

    if not all_highs and not all_lows:
        logger.debug("SR: no levels found — skipping")
        return sr

    # ── Cluster and classify ───────────────────────────
    resistance_clusters = _cluster(
        [h for h in all_highs if h > current_price], threshold_pct=0.3
    )
    support_clusters = _cluster(
        [l for l in all_lows if l < current_price], threshold_pct=0.3
    )

    for price, strength in resistance_clusters:
        sr.resistances.append(SRLevel(
            price=round(price, 2),
            strength=strength,
            timeframe="MTF",
            level_type="resistance"
        ))

    for price, strength in support_clusters:
        sr.supports.append(SRLevel(
            price=round(price, 2),
            strength=strength,
            timeframe="MTF",
            level_type="support"
        ))

    # Sort: supports descending (closest below first), resistance ascending
    sr.supports    = sorted(sr.supports,    key=lambda x: x.price, reverse=True)
    sr.resistances = sorted(sr.resistances, key=lambda x: x.price)

    # ── Proximity calculation ──────────────────────────
    atr_zone = atr * 1.2    # within 1.2 ATR = "at the level"

    if sr.supports:
        sr.nearest_sup = sr.supports[0].price
        dist = current_price - sr.nearest_sup
        sr.sup_distance_atr = dist / max(atr, 0.1)
        sr.at_support = dist <= atr_zone

    if sr.resistances:
        sr.nearest_res = sr.resistances[0].price
        dist = sr.nearest_res - current_price
        sr.res_distance_atr = dist / max(atr, 0.1)
        sr.at_resistance = dist <= atr_zone

    logger.debug(
        f"SR: sup=${sr.nearest_sup:.1f}({sr.sup_distance_atr:.1f}ATR) "
        f"res=${sr.nearest_res:.1f}({sr.res_distance_atr:.1f}ATR) "
        f"at_sup={sr.at_support} at_res={sr.at_resistance}"
    )

    return sr


# ── Score adjustment for strategies ───────────────────

def sr_score(sr: SRData, direction: str, price: float, atr: float) -> tuple:
    """
    Returns (score_adjustment, description_note).
    Positive = good setup, negative = bad setup.

    BUY logic:
      +15  entry near support (bounce zone) — high-probability long entry
      +8   support below within 2 ATR (cushion exists)
      -20  entry near resistance (wall above) — likely to get rejected
      -10  no support nearby and no resistance info

    SELL logic:
      +15  entry near resistance (rejection zone) — high-probability short
      +8   resistance above within 2 ATR
      -20  entry near support (floor below) — likely to bounce up
    """
    if not sr.supports and not sr.resistances:
        return 0, ""

    adj  = 0
    notes = []

    if direction == "BUY":
        if sr.at_support:
            adj += 15
            notes.append(f"✅ AT SUPPORT ${sr.nearest_sup:.0f}")
        elif sr.sup_distance_atr < 2.0:
            adj += 8
            notes.append(f"near support ${sr.nearest_sup:.0f}")
        elif sr.sup_distance_atr > 5.0 and sr.nearest_sup > 0:
            adj -= 5
            notes.append("support far away")

        if sr.at_resistance:
            adj -= 20
            notes.append(f"⛔ AT RESISTANCE ${sr.nearest_res:.0f}")
        elif sr.res_distance_atr < 1.5:
            adj -= 12
            notes.append(f"resistance close ${sr.nearest_res:.0f}")

    elif direction == "SELL":
        if sr.at_resistance:
            adj += 15
            notes.append(f"✅ AT RESISTANCE ${sr.nearest_res:.0f}")
        elif sr.res_distance_atr < 2.0:
            adj += 8
            notes.append(f"near resistance ${sr.nearest_res:.0f}")
        elif sr.res_distance_atr > 5.0 and sr.nearest_res > 0:
            adj -= 5
            notes.append("resistance far away")

        if sr.at_support:
            adj -= 20
            notes.append(f"⛔ AT SUPPORT ${sr.nearest_sup:.0f}")
        elif sr.sup_distance_atr < 1.5:
            adj -= 12
            notes.append(f"support close ${sr.nearest_sup:.0f}")

    return adj, " | ".join(notes)


# ── Quick summary for dashboard / logs ────────────────

def sr_summary(sr: SRData) -> str:
    parts = []
    if sr.nearest_res > 0:
        parts.append(f"RES ${sr.nearest_res:.0f} ({sr.res_distance_atr:.1f}ATR)")
    if sr.nearest_sup > 0:
        parts.append(f"SUP ${sr.nearest_sup:.0f} ({sr.sup_distance_atr:.1f}ATR)")
    return " | ".join(parts) if parts else "no S/R data"


# ── Standalone test ────────────────────────────────────
if __name__ == "__main__":
    async def test():
        print("\n=== MULTI-TIMEFRAME S/R TEST ===\n")
        price = 3280.0
        atr   = 8.0
        sr = await get_sr_levels(price, atr)
        print(f"Current price: ${price}")
        print(f"ATR: ${atr}")
        print(f"\nNearest Support:    ${sr.nearest_sup:.2f}  ({sr.sup_distance_atr:.1f} ATR away)  at_support={sr.at_support}")
        print(f"Nearest Resistance: ${sr.nearest_res:.2f}  ({sr.res_distance_atr:.1f} ATR away)  at_resistance={sr.at_resistance}")
        print(f"\nAll Supports:    {[s.price for s in sr.supports[:5]]}")
        print(f"All Resistances: {[r.price for r in sr.resistances[:5]]}")
        print(f"\nH1 key levels: {sr.levels_h1[:8]}")
        print(f"D1 key levels: {sr.levels_d1[:8]}")

        buy_adj,  buy_note  = sr_score(sr, "BUY",  price, atr)
        sell_adj, sell_note = sr_score(sr, "SELL", price, atr)
        print(f"\nBUY  score adj: {buy_adj:+d}  {buy_note}")
        print(f"SELL score adj: {sell_adj:+d}  {sell_note}")
        print(f"\nSummary: {sr_summary(sr)}")
        print("\n✅ S/R module operational!")

    asyncio.run(test())
