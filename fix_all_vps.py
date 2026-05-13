"""
NEXUS AI — Master VPS Patch Script
Applies ALL pending fixes in one run.
Run from: C:\\Users\\Administrator\\Desktop\\AIBOT\\
"""

import os, sys

OK  = []
ERR = []

def patch(path, old, new, label):
    try:
        with open(path, "r", encoding="utf-8") as f:
            c = f.read()
        if new.strip().split("\n")[0].strip() in c or old not in c:
            OK.append(f"  OK {label}: already applied")
            return
        c = c.replace(old, new)
        with open(path, "w", encoding="utf-8") as f:
            f.write(c)
        OK.append(f"  OK {label}: FIXED")
    except Exception as e:
        ERR.append(f"  ERR {label}: {e}")


patch(
    "bot/account_sync.py",
    "    # 3. Use .env STARTING_EQUITY\n    balance = get_starting_equity_from_env()\n    update_equity(balance)\n    logger.info(f\"Using STARTING_EQUITY from .env: ${balance:.2f}\")\n    return balance",
    "    # 3. Bridge unavailable — keep current equity, don't reset paper gains\n    current = get_state().equity\n    if current > 1.0:\n        logger.debug(f\"Bridge unavailable — keeping current equity ${current:.2f}\")\n        return current\n\n    # 4. First startup only — nothing set yet, use .env\n    balance = get_starting_equity_from_env()\n    update_equity(balance)\n    logger.info(f\"Using STARTING_EQUITY from .env: ${balance:.2f}\")\n    return balance",
    "account_sync: keep equity on bridge-down"
)

patch(
    "bot/trade_journal.py",
    '    won   = result in ("TP_HIT", "WIN")',
    '    won   = result in ("TP_HIT", "WIN", "TRAIL_HIT") or pnl > 0',
    "trade_journal: TRAIL_HIT = WIN"
)

patch(
    "bot/gemma_agent.py",
    '                    "temperature":     0.15,\n                    "maxOutputTokens": max_tokens,\n                    "thinkingConfig":  {"thinkingBudget": 0},',
    '                    "temperature":     0.15,\n                    "maxOutputTokens": max_tokens,',
    "gemma_agent: remove thinkingConfig"
)

with open("bot/execution.py", "r", encoding="utf-8") as f:
    ex = f.read()

if "_validate_xauusd" not in ex:
    old4 = 'async def execute_trade(params: TradeParams) -> OrderResult:\n    """\n    Main entry point — routes to paper/bridge/native based on config.\n    """\n    if settings.is_paper:'
    new4 = '''def _validate_xauusd(params):
    p, sl, tp = params.entry, params.stop_loss, params.take_profit_1
    for label, val in [("entry", p), ("SL", sl), ("TP", tp)]:
        if not (1000 < val < 15000):
            return f"Invalid {label}=${val:.2f} outside XAUUSD range"
    if params.direction == "BUY" and sl >= p:
        return f"BUY SL={sl:.2f} >= entry={p:.2f}"
    if params.direction == "SELL" and sl <= p:
        return f"SELL SL={sl:.2f} <= entry={p:.2f}"
    if params.direction == "BUY" and tp <= p:
        return f"BUY TP={tp:.2f} <= entry={p:.2f}"
    if params.direction == "SELL" and tp >= p:
        return f"SELL TP={tp:.2f} >= entry={p:.2f}"
    if abs(p - sl) < 0.5:
        return f"SL too close: {abs(p-sl):.2f} pts"
    return ""


def _is_gold_market_open():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    wd, h = now.weekday(), now.hour
    if wd == 5: return False
    if wd == 6 and h < 22: return False
    if wd == 4 and h >= 21: return False
    return True


async def execute_trade(params: TradeParams) -> OrderResult:
    """Main entry point — routes to paper/bridge/native based on config."""
    err = _validate_xauusd(params)
    if err:
        logger.error(f"Trade REJECTED — {err}")
        return OrderResult(success=False, ticket="", symbol=params.symbol,
            direction=params.direction, entry=params.entry,
            stop_loss=params.stop_loss, take_profit=params.take_profit_1,
            lot_size=params.lot_size, mode="rejected", message=err)
    if not settings.is_paper and not _is_gold_market_open():
        logger.warning("Trade REJECTED — gold market closed (weekend/after-hours)")
        return OrderResult(success=False, ticket="", symbol=params.symbol,
            direction=params.direction, entry=params.entry,
            stop_loss=params.stop_loss, take_profit=params.take_profit_1,
            lot_size=params.lot_size, mode="rejected", message="Market closed")
    if settings.is_paper:'''
    if old4 in ex:
        ex = ex.replace(old4, new4)
        with open("bot/execution.py", "w", encoding="utf-8") as f:
            f.write(ex)
        OK.append("  OK execution.py: price validation + market hours FIXED")
    else:
        ERR.append("  ERR execution.py: pattern not found")
else:
    OK.append("  OK execution.py: already fixed")

with open("mt5_bridge_server.py", "r", encoding="utf-8") as f:
    br = f.read()

if "Always probe" not in br:
    old5 = "def _ensure():\n    global _mt5_ok\n    if not _mt5_ok:\n        _mt5_ok = mt5.initialize()\n        if not _mt5_ok:\n            _mt5_ok = mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)\n    return _mt5_ok"
    new5 = """def _ensure():
    global _mt5_ok
    # Always probe — account_info() returns None when MT5 disconnects
    try:
        info = mt5.account_info()
        if info is not None:
            _mt5_ok = True
            return True
    except Exception:
        pass
    _mt5_ok = False
    print("MT5 disconnected — re-initializing...")
    _mt5_ok = mt5.initialize()
    if not _mt5_ok:
        _mt5_ok = mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if _mt5_ok:
        info = mt5.account_info()
        print(f"MT5 reconnected: Balance=${info.balance:.2f}" if info else "MT5 reconnected")
    else:
        print(f"MT5 re-init FAILED: {mt5.last_error()}")
    return _mt5_ok"""
    if old5 in br:
        br = br.replace(old5, new5)
        with open("mt5_bridge_server.py", "w", encoding="utf-8") as f:
            f.write(br)
        OK.append("  OK mt5_bridge_server.py: auto-reconnect FIXED")
    else:
        ERR.append("  ERR mt5_bridge_server.py: pattern not found")
else:
    OK.append("  OK mt5_bridge_server.py: already fixed")

SR_CODE = '''"""
Multi-Timeframe Support & Resistance
"""
import asyncio, time
from dataclasses import dataclass, field
from typing import List
import pandas as pd
import numpy as np
from loguru import logger

_cache: dict = {}
_CACHE_TTL = {"1h": 3600, "1d": 86400}

@dataclass
class SRLevel:
    price: float
    strength: int
    timeframe: str
    level_type: str

@dataclass
class SRData:
    supports:         List[SRLevel] = field(default_factory=list)
    resistances:      List[SRLevel] = field(default_factory=list)
    nearest_sup:      float = 0.0
    nearest_res:      float = 0.0
    at_support:       bool  = False
    at_resistance:    bool  = False
    sup_distance_atr: float = 99.0
    res_distance_atr: float = 99.0
    levels_h1:        List[float] = field(default_factory=list)
    levels_d1:        List[float] = field(default_factory=list)

async def _fetch_tf(symbol, interval, period):
    cache_key = f"{symbol}_{interval}"
    now = time.time()
    if cache_key in _cache:
        ts, df = _cache[cache_key]
        if now - ts < _CACHE_TTL.get(interval, 3600):
            return df
    try:
        import yfinance as yf
        import warnings; warnings.filterwarnings("ignore")
        def _dl():
            df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
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

def _find_pivots(df, left=5, right=5):
    highs, lows = [], []
    n = len(df)
    for i in range(left, n - right):
        h = df["high"].iloc[i]; l = df["low"].iloc[i]
        if df["high"].iloc[i-left:i].max() < h and df["high"].iloc[i+1:i+right+1].max() < h:
            highs.append(h)
        if df["low"].iloc[i-left:i].min() > l and df["low"].iloc[i+1:i+right+1].min() > l:
            lows.append(l)
    return highs, lows

def _cluster(levels, threshold_pct=0.25):
    if not levels: return []
    levels = sorted(levels)
    clusters, group = [], [levels[0]]
    for lvl in levels[1:]:
        if (lvl - group[-1]) / group[-1] * 100 < threshold_pct:
            group.append(lvl)
        else:
            clusters.append((sum(group)/len(group), len(group)))
            group = [lvl]
    clusters.append((sum(group)/len(group), len(group)))
    return clusters

async def get_sr_levels(current_price, atr):
    sr = SRData()
    all_highs, all_lows = [], []
    df_h1 = await _fetch_tf("GC=F", "1h", "30d")
    if not df_h1.empty and len(df_h1) >= 15:
        h1_highs, h1_lows = _find_pivots(df_h1, left=4, right=4)
        all_highs.extend(h1_highs); all_lows.extend(h1_lows)
        sr.levels_h1 = sorted(set(round(x,1) for x in h1_highs+h1_lows))
    df_d1 = await _fetch_tf("GC=F", "1d", "180d")
    if not df_d1.empty and len(df_d1) >= 10:
        d1_highs, d1_lows = _find_pivots(df_d1, left=3, right=3)
        all_highs.extend(d1_highs * 3); all_lows.extend(d1_lows * 3)
        sr.levels_d1 = sorted(set(round(x,1) for x in d1_highs+d1_lows))
    if not all_highs and not all_lows:
        return sr
    res_clusters = _cluster([h for h in all_highs if h > current_price], 0.3)
    sup_clusters = _cluster([l for l in all_lows  if l < current_price], 0.3)
    sr.resistances = sorted([SRLevel(round(p,2), s, "MTF", "resistance") for p,s in res_clusters], key=lambda x: x.price)
    sr.supports    = sorted([SRLevel(round(p,2), s, "MTF", "support") for p,s in sup_clusters], key=lambda x: x.price, reverse=True)
    atr_zone = atr * 1.2
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
    logger.debug(f"SR: sup=${sr.nearest_sup:.1f}({sr.sup_distance_atr:.1f}ATR) res=${sr.nearest_res:.1f}({sr.res_distance_atr:.1f}ATR)")
    return sr

def sr_score(sr, direction, price, atr):
    if not sr.supports and not sr.resistances: return 0, ""
    adj, notes = 0, []
    if direction == "BUY":
        if sr.at_support: adj += 15; notes.append(f"AT SUPPORT ${sr.nearest_sup:.0f}")
        elif sr.sup_distance_atr < 2.0: adj += 8; notes.append(f"near support ${sr.nearest_sup:.0f}")
        elif sr.sup_distance_atr > 5.0 and sr.nearest_sup > 0: adj -= 5
        if sr.at_resistance: adj -= 20; notes.append(f"AT RESISTANCE ${sr.nearest_res:.0f}")
        elif sr.res_distance_atr < 1.5: adj -= 12; notes.append(f"resistance close ${sr.nearest_res:.0f}")
    elif direction == "SELL":
        if sr.at_resistance: adj += 15; notes.append(f"AT RESISTANCE ${sr.nearest_res:.0f}")
        elif sr.res_distance_atr < 2.0: adj += 8; notes.append(f"near resistance ${sr.nearest_res:.0f}")
        elif sr.res_distance_atr > 5.0 and sr.nearest_res > 0: adj -= 5
        if sr.at_support: adj -= 20; notes.append(f"AT SUPPORT ${sr.nearest_sup:.0f}")
        elif sr.sup_distance_atr < 1.5: adj -= 12; notes.append(f"support close ${sr.nearest_sup:.0f}")
    return adj, " | ".join(notes)

def sr_summary(sr):
    parts = []
    if sr.nearest_res > 0: parts.append(f"RES ${sr.nearest_res:.0f} ({sr.res_distance_atr:.1f}ATR)")
    if sr.nearest_sup > 0: parts.append(f"SUP ${sr.nearest_sup:.0f} ({sr.sup_distance_atr:.1f}ATR)")
    return " | ".join(parts) if parts else "no S/R data"
'''

os.makedirs("bot", exist_ok=True)
if not os.path.exists("bot/sr_levels.py"):
    with open("bot/sr_levels.py", "w", encoding="utf-8") as f:
        f.write(SR_CODE)
    OK.append("  OK bot/sr_levels.py: CREATED")
else:
    OK.append("  OK bot/sr_levels.py: already exists")

print("\n" + "="*50)
print("  NEXUS VPS PATCH RESULTS")
print("="*50)
for line in OK:  print(line)
for line in ERR: print(line)
print("="*50)
if ERR:
    print(f"\nWARNING: {len(ERR)} fix(es) need manual attention.")
else:
    print(f"\nAll {len(OK)} fixes applied!")
print("\nNEXT: python mt5_bridge_server.py  (Window 1)")
print("      python main.py --mode live    (Window 2)")
