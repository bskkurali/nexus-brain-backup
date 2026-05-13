"""
NEXUS Strategy Brain — Backtester
────────────────────────────────────
Backtests 6 built-in strategies on 6 months of XAUUSD H1 data.
Ranks by win rate. Bot picks best strategy each session.

Strategies tested:
  1. Fibonacci Bounce     (what the bot currently trades)
  2. EMA Ribbon Pullback  (what the bot currently trades)
  3. MACD Cross           (classic trend)
  4. RSI Reversal         (mean reversion)
  5. EMA Pullback         (trend pullback)
  6. Breakout             (momentum)

Runs on startup, then every 24 hours.
"""

import asyncio
import json
import os
from datetime import datetime
from loguru import logger

STRATEGY_FILE    = "data/strategy_library.json"
MIN_PROFIT_FACTOR = 1.10  # strategy is "usable" if PF ≥ 1.10 with ≥ 30 trades
BACKTEST_BARS     = 1000  # kept for reference


# ══════════════════════════════════════════════════════
# CORE BACKTESTER
# ══════════════════════════════════════════════════════

def _get_data():
    """Download and prepare XAUUSD M5 data (how the bot actually trades)."""
    import yfinance as yf
    import pandas as pd
    import warnings
    warnings.filterwarnings("ignore")

    # M5 for entries (last 60 days = ~4800 bars)
    df = yf.download("GC=F", period="60d", interval="5m",
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None

    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    df = df.dropna()

    cl = df["close"]; hi = df["high"]; lo = df["low"]; vl = df["volume"]

    # EMAs
    df["e8"]  = cl.ewm(8,  adjust=False).mean()
    df["e9"]  = cl.ewm(9,  adjust=False).mean()
    df["e13"] = cl.ewm(13, adjust=False).mean()
    df["e21"] = cl.ewm(21, adjust=False).mean()
    df["e34"] = cl.ewm(34, adjust=False).mean()
    df["e50"] = cl.ewm(50, adjust=False).mean()
    df["e55"] = cl.ewm(55, adjust=False).mean()
    df["e89"] = cl.ewm(89, adjust=False).mean()
    df["e200"]= cl.ewm(200,adjust=False).mean()

    # RSI
    d  = cl.diff()
    g  = d.clip(lower=0).ewm(14, adjust=False).mean()
    l  = (-d.clip(upper=0)).ewm(14, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + g / (l + 1e-9)))

    # MACD
    macd_line  = cl.ewm(12, adjust=False).mean() - cl.ewm(26, adjust=False).mean()
    signal     = macd_line.ewm(9, adjust=False).mean()
    df["macd"] = macd_line
    df["macd_sig"] = signal
    df["macd_bull"]= macd_line > signal           # MACD above signal (bullish)
    df["macd_bear"]= macd_line < signal

    # ATR
    import pandas as pd
    tr = pd.concat([hi - lo,
                    (hi - cl.shift()).abs(),
                    (lo - cl.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(14, adjust=False).mean()

    # ADX
    up  = hi.diff(); dn = -lo.diff()
    pdm = up.where((up > dn) & (up > 0), 0)
    mdm = dn.where((dn > up) & (dn > 0), 0)
    tr_s = tr.ewm(14, adjust=False).mean()
    pdi  = 100 * pdm.ewm(14, adjust=False).mean() / (tr_s + 1e-9)
    mdi  = 100 * mdm.ewm(14, adjust=False).mean() / (tr_s + 1e-9)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-9)
    df["adx"] = dx.ewm(14, adjust=False).mean()

    # Volume ratio
    df["vol_ratio"] = vl / (vl.rolling(20).mean() + 1e-9)

    # Trend states
    df["bull"] = (df["e9"] > df["e21"]) & (df["e21"] > df["e50"])
    df["bear"] = (df["e9"] < df["e21"]) & (df["e21"] < df["e50"])

    # Ribbon states
    df["ribbon_bull"] = ((df["e8"]  > df["e13"]) & (df["e13"] > df["e21"]) &
                         (df["e21"] > df["e34"]) & (df["e34"] > df["e55"]) &
                         (df["e55"] > df["e89"]))
    df["ribbon_bear"] = ((df["e8"]  < df["e13"]) & (df["e13"] < df["e21"]) &
                         (df["e21"] < df["e34"]) & (df["e34"] < df["e55"]) &
                         (df["e55"] < df["e89"]))

    # Hour of day (for session filtering)
    df["hour"] = df.index.hour

    # ── Derived bool columns — AI strategy generator can use these ──
    df["close_gt_e21"]  = cl > df["e21"]
    df["close_gt_e50"]  = cl > df["e50"]
    df["close_gt_e200"] = cl > df["e200"]
    df["strong_trend"]  = df["adx"] > 25
    df["high_vol"]      = df["vol_ratio"] > 1.5
    df["rsi_rising"]    = df["rsi"] > df["rsi"].shift(1)
    df["e21_rising"]    = df["e21"] > df["e21"].shift(3)

    return df


def _simulate(df, entries_fn, sl_atr_mult: float, tp_atr_mult: float) -> dict:
    """
    Generic trade simulator.
    entries_fn(i, df) → "BUY" | "SELL" | None
    Returns {wins, losses, trades, win_rate, avg_rr, profit_factor}
    """
    wins = 0; losses = 0; trades = []
    # Active trade state stored in a dict so linter can trace cross-iteration usage
    t: dict = {}   # keys: direction, entry, sl, tp

    cl = df["close"]; hi = df["high"]; lo = df["low"]

    for i in range(100, len(df) - 2):
        if t:
            lo_next = float(lo.iloc[i + 1])
            hi_next = float(hi.iloc[i + 1])
            if t["direction"] == "BUY":
                if lo_next <= t["sl"]:
                    losses += 1
                    trades.append({"won": False, "pnl": round(t["sl"] - t["entry"], 2)})
                    t = {}
                elif hi_next >= t["tp"]:
                    wins += 1
                    trades.append({"won": True, "pnl": round(t["tp"] - t["entry"], 2)})
                    t = {}
            else:  # SELL
                if hi_next >= t["sl"]:
                    losses += 1
                    trades.append({"won": False, "pnl": round(t["entry"] - t["sl"], 2)})
                    t = {}
                elif lo_next <= t["tp"]:
                    wins += 1
                    trades.append({"won": True, "pnl": round(t["entry"] - t["tp"], 2)})
                    t = {}
            continue

        sig = entries_fn(i, df)
        if not sig:
            continue

        price   = float(cl.iloc[i])
        cur_atr = float(df["atr"].iloc[i])
        if cur_atr <= 0:
            continue

        if sig == "BUY":
            t = {"direction": "BUY",  "entry": price,
                 "sl": price - cur_atr * sl_atr_mult,
                 "tp": price + cur_atr * tp_atr_mult}
        else:
            t = {"direction": "SELL", "entry": price,
                 "sl": price + cur_atr * sl_atr_mult,
                 "tp": price - cur_atr * tp_atr_mult}

    total = wins + losses
    if total == 0:
        return {"wins": 0, "losses": 0, "trades": 0, "win_rate": 0,
                "win_rate_pct": 0, "avg_rr": 0, "profit_factor": 0,
                "usable": False}

    win_rate = wins / total
    gross_win  = sum(t["pnl"] for t in trades if t["won"])
    gross_loss = abs(sum(t["pnl"] for t in trades if not t["won"]))
    pf = round(gross_win / (gross_loss + 1e-9), 2)
    avg_pnl = round(sum(t["pnl"] for t in trades) / total, 2)

    return {
        "wins":          wins,
        "losses":        losses,
        "trades":        total,
        "win_rate":      round(win_rate, 3),
        "win_rate_pct":  round(win_rate * 100, 1),
        "avg_rr":        round(tp_atr_mult / sl_atr_mult, 2),
        "profit_factor": pf,
        "avg_pnl":       avg_pnl,
        "usable":        pf >= MIN_PROFIT_FACTOR and total >= 30,
    }


# ══════════════════════════════════════════════════════
# STRATEGY ENTRY FUNCTIONS
# ══════════════════════════════════════════════════════

def _fib_bounce(i, df):
    """Fibonacci 61.8% bounce — primary bot strategy."""
    bull = bool(df["bull"].iloc[i])
    bear = bool(df["bear"].iloc[i])
    rsi  = float(df["rsi"].iloc[i])
    macd_b = bool(df["macd_bull"].iloc[i])

    # Compute Fibonacci levels from 50-bar swing
    hi50 = float(df["high"].iloc[max(0, i-50):i].max())
    lo50 = float(df["low"].iloc[max(0, i-50):i].min())
    diff = hi50 - lo50
    if diff < 1:
        return None

    fib618 = lo50 + diff * 0.618
    fib50  = lo50 + diff * 0.500
    fib382 = lo50 + diff * 0.382
    price  = float(df["close"].iloc[i])
    atr    = float(df["atr"].iloc[i])

    near_fib = (abs(price - fib618) < atr * 1.5 or
                abs(price - fib50)  < atr * 1.5 or
                abs(price - fib382) < atr * 1.5)

    if bull and near_fib and rsi < 52 and macd_b:
        return "BUY"
    if bear and near_fib and rsi > 48 and not macd_b:
        return "SELL"
    return None


def _ema_ribbon(i, df):
    """EMA Ribbon pullback — secondary bot strategy."""
    ribbon_bull = bool(df["ribbon_bull"].iloc[i])
    ribbon_bear = bool(df["ribbon_bear"].iloc[i])
    rsi  = float(df["rsi"].iloc[i])
    macd_b = bool(df["macd_bull"].iloc[i])
    adx    = float(df["adx"].iloc[i])

    if adx < 15:
        return None

    price = float(df["close"].iloc[i])
    e21   = float(df["e21"].iloc[i])
    e34   = float(df["e34"].iloc[i])
    atr   = float(df["atr"].iloc[i])

    # Price pulled back into ribbon zone
    in_ribbon_buy  = e34 - atr * 1.5 <= price <= e21 + atr * 1.5
    in_ribbon_sell = e21 - atr * 1.5 <= price <= e34 + atr * 1.5

    if ribbon_bull and in_ribbon_buy and 30 < rsi < 58 and macd_b:
        return "BUY"
    if ribbon_bear and in_ribbon_sell and 42 < rsi < 70 and not macd_b:
        return "SELL"
    return None


def _macd_trend(i, df):
    """MACD cross in trend direction — classic."""
    bull = bool(df["bull"].iloc[i])
    bear = bool(df["bear"].iloc[i])
    rsi  = float(df["rsi"].iloc[i])
    macd_now  = float(df["macd"].iloc[i])
    macd_prev = float(df["macd"].iloc[i - 1])
    sig_now   = float(df["macd_sig"].iloc[i])
    sig_prev  = float(df["macd_sig"].iloc[i - 1])

    # Actual crossover this bar
    bull_cross = (macd_now > sig_now) and (macd_prev <= sig_prev)
    bear_cross = (macd_now < sig_now) and (macd_prev >= sig_prev)

    if bull and bull_cross and 35 < rsi < 65:
        return "BUY"
    if bear and bear_cross and 35 < rsi < 65:
        return "SELL"
    return None


def _rsi_reversal(i, df):
    """RSI extreme reversal — oversold/overbought bounce with MACD confirmation."""
    rsi      = float(df["rsi"].iloc[i])
    rsi_prev = float(df["rsi"].iloc[i - 1])
    macd_b   = bool(df["macd_bull"].iloc[i])
    price    = float(df["close"].iloc[i])
    e200     = float(df["e200"].iloc[i])

    # RSI crosses back through oversold/overbought threshold
    rsi_cross_up = rsi_prev < 38 and rsi >= 38   # Oversold → recovering
    rsi_cross_dn = rsi_prev > 62 and rsi <= 62   # Overbought → rejecting

    # Macro filter: only trade in direction of long-term trend (e200)
    above_e200 = price > e200
    below_e200 = price < e200

    if above_e200 and rsi_cross_up and macd_b:
        return "BUY"
    if below_e200 and rsi_cross_dn and not macd_b:
        return "SELL"
    return None


def _ema_pullback(i, df):
    """EMA21 pullback in trend direction."""
    bull = bool(df["bull"].iloc[i])
    bear = bool(df["bear"].iloc[i])
    rsi  = float(df["rsi"].iloc[i])
    price = float(df["close"].iloc[i])
    e21   = float(df["e21"].iloc[i])
    e50   = float(df["e50"].iloc[i])
    atr   = float(df["atr"].iloc[i])
    macd_b = bool(df["macd_bull"].iloc[i])

    near_e21 = abs(price - e21) < atr * 1.0
    near_e50 = abs(price - e50) < atr * 1.0

    if bull and (near_e21 or near_e50) and 30 < rsi < 56 and macd_b:
        return "BUY"
    if bear and (near_e21 or near_e50) and 44 < rsi < 70 and not macd_b:
        return "SELL"
    return None


def _breakout(i, df):
    """Breakout of 20-bar high/low with volume."""
    bull = bool(df["bull"].iloc[i])
    bear = bool(df["bear"].iloc[i])
    price     = float(df["close"].iloc[i])
    price_prev= float(df["close"].iloc[i - 1])
    hi20      = float(df["high"].iloc[max(0, i-20):i].max())
    lo20      = float(df["low"].iloc[max(0, i-20):i].min())
    vol_ratio = float(df["vol_ratio"].iloc[i])
    adx       = float(df["adx"].iloc[i])

    if adx < 18 or vol_ratio < 1.1:
        return None

    if bull and price > hi20 and price_prev <= hi20:
        return "BUY"
    if bear and price < lo20 and price_prev >= lo20:
        return "SELL"
    return None


# ══════════════════════════════════════════════════════
# STRATEGY REGISTRY
# ══════════════════════════════════════════════════════

STRATEGIES = {
    "fib_bounce": {
        "name":        "Fibonacci Bounce",
        "description": "Entry at 38.2/50/61.8% fib retracement in trend direction",
        "entry_fn":    _fib_bounce,
        "sl_mult":     1.2,
        "tp_mult":     2.0,
    },
    "ema_ribbon": {
        "name":        "EMA Ribbon Pullback",
        "description": "Pullback to Fibonacci EMA ribbon (8·13·21·34·55·89)",
        "entry_fn":    _ema_ribbon,
        "sl_mult":     1.2,
        "tp_mult":     2.0,
    },
    "macd_cross": {
        "name":        "MACD Trend Cross",
        "description": "MACD crossover with EMA trend confirmation",
        "entry_fn":    _macd_trend,
        "sl_mult":     1.2,
        "tp_mult":     2.0,
    },
    "rsi_reversal": {
        "name":        "RSI Reversal",
        "description": "RSI extreme bounce at EMA50 in trend direction",
        "entry_fn":    _rsi_reversal,
        "sl_mult":     1.2,
        "tp_mult":     2.0,
    },
    "ema_pullback": {
        "name":        "EMA21 Pullback",
        "description": "Price pulls back to EMA21/50 in trending market",
        "entry_fn":    _ema_pullback,
        "sl_mult":     1.0,
        "tp_mult":     1.8,
    },
    "breakout": {
        "name":        "Volume Breakout",
        "description": "20-bar high/low breakout with volume confirmation",
        "entry_fn":    _breakout,
        "sl_mult":     1.0,
        "tp_mult":     2.0,
    },
}


# ══════════════════════════════════════════════════════
# LIBRARY MANAGER
# ══════════════════════════════════════════════════════

def load_library() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(STRATEGY_FILE):
        try:
            return json.load(open(STRATEGY_FILE))
        except Exception:
            pass
    return {"strategies": {}, "last_backtest": "", "best_strategy": ""}


def save_library(lib: dict):
    json.dump(lib, open(STRATEGY_FILE, "w"), indent=2)


# ══════════════════════════════════════════════════════
# MAIN RUNNER
# ══════════════════════════════════════════════════════

async def run_strategy_brain() -> dict:
    """Run full backtest on all strategies. Call on startup + every 24h."""
    logger.info("📊 Strategy Brain — backtesting 6 strategies on 60d XAUUSD M5...")

    loop = asyncio.get_event_loop()

    # Download data once
    df = await loop.run_in_executor(None, _get_data)
    if df is None or df.empty:
        logger.error("Strategy Brain: no market data")
        return load_library()

    lib     = load_library()
    results = []

    for sid, strat in STRATEGIES.items():
        try:
            entry_fn = strat["entry_fn"]
            sl_mult  = strat["sl_mult"]
            tp_mult  = strat["tp_mult"]

            result = await loop.run_in_executor(
                None, _simulate, df, entry_fn, sl_mult, tp_mult
            )
            result["strategy_id"]   = sid
            result["name"]          = strat["name"]
            result["description"]   = strat["description"]
            result["backtested_at"] = datetime.now().isoformat()

            lib["strategies"][sid] = result
            emoji = "✅" if result["usable"] else "❌"
            logger.info(
                f"  {emoji} {strat['name']:25s} "
                f"{result['win_rate_pct']:5.1f}% WR | "
                f"{result['trades']:3d} trades | "
                f"PF={result['profit_factor']:.2f}"
            )
            results.append(result)
        except Exception as e:
            logger.warning(f"  Backtest failed {sid}: {e}")

    # Pick best by win rate among usable strategies
    usable = [r for r in results if r.get("usable")]
    if usable:
        best = max(usable, key=lambda x: (x["win_rate"], x["profit_factor"]))
        lib["best_strategy"] = best["strategy_id"]
        logger.success(
            f"🏆 Best: {best['name']} — "
            f"{best['win_rate_pct']:.1f}% WR | "
            f"PF={best['profit_factor']:.2f} | "
            f"{best['trades']} trades"
        )
    else:
        # Default to fib_bounce (what the bot is tuned for)
        lib["best_strategy"] = "fib_bounce"
        logger.warning("No strategy above threshold — defaulting to fib_bounce")

    lib["last_backtest"] = datetime.now().isoformat()
    save_library(lib)

    # ── Auto-optimize SL/TP parameters
    try:
        from bot.strategy_optimizer import run_optimization
        opt_results = await run_optimization(df, STRATEGIES, _simulate)
        for sid, opt in opt_results.items():
            if sid in STRATEGIES and opt.get("optimized_params"):
                p = opt["optimized_params"]
                STRATEGIES[sid]["sl_mult"] = p["sl_mult"]
                STRATEGIES[sid]["tp_mult"] = p["tp_mult"]
    except Exception as e:
        logger.warning(f"Optimizer skipped: {e}")

    # ── AI Strategy Search — keep generating until 80% WR ──────────
    best_existing_pf = max(
        (r.get("profit_factor", 0) for r in results), default=0
    )
    # Skip search if already found a good strategy previously
    from bot.ai_strategy_generator import load_search_progress
    prior = load_search_progress()
    already_found = prior.get("best_wr", 0) >= 75

    if best_existing_pf < 1.5 and not already_found:
        # Fixed strategies aren't exceptional — let AI try to do better
        logger.info("🤖 Starting AI strategy search (target: 80% WR)...")
        try:
            from bot.ai_strategy_generator import run_ai_search_loop
            ai_best = await run_ai_search_loop(target_wr=0.80, max_iter=30)
            if ai_best:
                r      = ai_best["result"]
                spec   = ai_best["spec"]
                sid    = "ai_best"
                lib["strategies"][sid] = {
                    **r,
                    "strategy_id":   sid,
                    "name":          spec["name"],
                    "description":   spec.get("idea", "AI-generated strategy"),
                    "backtested_at": datetime.now().isoformat(),
                    "ai_generated":  True,
                }
                # Promote AI strategy if it's the best we have
                current_best = lib["strategies"].get(lib.get("best_strategy", ""), {})
                if r.get("profit_factor", 0) > current_best.get("profit_factor", 0):
                    lib["best_strategy"] = sid
                    logger.success(
                        f"🏆 AI strategy promoted to active: "
                        f"{spec['name']} — "
                        f"{r.get('win_rate_pct',0):.1f}% WR | "
                        f"PF={r.get('profit_factor',0):.2f}"
                    )
                save_library(lib)
        except Exception as e:
            logger.error(f"AI search error: {e}")
    elif already_found:
        logger.info(f"🤖 AI search skipped — already found {prior['best_name']} @ {prior['best_wr']}% WR")
    else:
        logger.info("Fixed strategies strong enough — skipping AI search")

    return lib


def get_best_strategy() -> str:
    return load_library().get("best_strategy", "fib_bounce")


def get_strategy_summary() -> str:
    lib        = load_library()
    strategies = lib.get("strategies", {})

    # AI search progress
    ai_status = ""
    try:
        from bot.ai_strategy_generator import load_search_progress
        p = load_search_progress()
        if p.get("status") not in ("idle", ""):
            ai_status = (
                f"\n🤖 AI Search: {p.get('status','')} | "
                f"Attempts: {p.get('iterations',0)} | "
                f"Best: {p.get('best_name','—')} {p.get('best_wr',0):.0f}% WR"
            )
    except Exception:
        pass

    if not strategies:
        return "Backtesting in progress..." + ai_status

    lines = ["Backtested strategies (60d XAUUSD M5):"]
    for sid, r in sorted(strategies.items(),
                         key=lambda x: x[1].get("win_rate", 0), reverse=True):
        if r.get("trades", 0) == 0:
            continue
        ai_tag = " [AI]" if r.get("ai_generated") else ""
        emoji  = "✅" if r.get("usable") else "❌"
        lines.append(
            f"{emoji} {r.get('name', sid):25s}{ai_tag} "
            f"{r.get('win_rate_pct', 0):.0f}% WR  "
            f"{r.get('trades', 0)} trades  "
            f"PF={r.get('profit_factor', 0):.2f}"
        )

    best = lib.get("best_strategy", "")
    if best and best in strategies:
        b = strategies[best]
        lines.append(
            f"\n🏆 ACTIVE: {b.get('name', best)} "
            f"({b.get('win_rate_pct', 0):.0f}% WR)"
        )

    return "\n".join(lines) + ai_status
