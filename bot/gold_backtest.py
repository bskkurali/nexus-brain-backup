"""
NEXUS Gold Strategy Backtester
────────────────────────────────
Tests 3 strategies on real XAUUSD historical data (GC=F via yfinance).
Designed for $100 account with 0.01 minimum lot.

Run standalone:
    python -m bot.gold_backtest

Reality of $100 account on XAUUSD with 0.01 lot:
  - Every $1 price move = $1 P&L  (0.01 lot × 100 oz)
  - ATR on M5 ≈ $4–8
  - Practical SL range: $5–10 = 5–10% risk per trade
  → Strategy MUST have 65%+ win rate to stay profitable
"""

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional
from loguru import logger


# ── Constants ──────────────────────────────────────────
LOT_SIZE    = 0.01          # Minimum lot at Exness
PNL_FACTOR  = 100           # 0.01 lot × 100 = $1 per $1 price move
ACCOUNT     = 100.0
COMMISSION  = 0.07          # ~$0.07 per trade round-trip on 0.01 lot
SESSION_UTC = (6, 20)       # London open to NY close (UTC hours)


@dataclass
class BacktestTrade:
    entry:     float
    sl:        float
    tp:        float
    direction: str
    bar_idx:   int
    pnl:       float = 0.0
    outcome:   str   = "OPEN"   # WIN / LOSS / OPEN


@dataclass
class BacktestResult:
    strategy:       str
    trades:         int   = 0
    wins:           int   = 0
    losses:         int   = 0
    total_pnl:      float = 0.0
    max_drawdown:   float = 0.0
    win_rate:       float = 0.0
    profit_factor:  float = 0.0
    avg_win:        float = 0.0
    avg_loss:       float = 0.0
    expectancy:     float = 0.0
    final_balance:  float = ACCOUNT


# ── Data fetching ───────────────────────────────────────

def _fetch_data(period: str = "6mo", interval: str = "5m"):
    import yfinance as yf
    import pandas as pd
    import warnings
    warnings.filterwarnings("ignore")

    logger.info(f"Downloading GC=F ({interval}, {period})...")
    df = yf.download("GC=F", period=period, interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError("No data returned from yfinance")

    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    df = df[["open","high","low","close","volume"]].dropna()

    # ── Indicators ──────────────────────────────────────
    cl = df["close"]; hi = df["high"]; lo = df["low"]; vl = df["volume"]

    # EMAs
    df["e8"]   = cl.ewm(8,   adjust=False).mean()
    df["e21"]  = cl.ewm(21,  adjust=False).mean()
    df["e50"]  = cl.ewm(50,  adjust=False).mean()
    df["e200"] = cl.ewm(200, adjust=False).mean()

    # RSI 14 + RSI 7
    d   = cl.diff()
    g14 = d.clip(lower=0).ewm(14, adjust=False).mean()
    l14 = (-d.clip(upper=0)).ewm(14, adjust=False).mean()
    df["rsi14"] = 100 - (100 / (1 + g14 / (l14 + 1e-9)))

    g7  = d.clip(lower=0).ewm(7, adjust=False).mean()
    l7  = (-d.clip(upper=0)).ewm(7, adjust=False).mean()
    r7  = 100 - (100 / (1 + g7 / (l7 + 1e-9)))
    df["rsi7"]  = r7
    df["rsi7p"] = r7.shift(1)
    df["rsi7p2"]= r7.shift(2)

    # ATR
    tr  = __import__("pandas").concat([
        hi - lo,
        (hi - cl.shift()).abs(),
        (lo - cl.shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"]     = tr.ewm(14, adjust=False).mean()
    df["atr_avg"] = df["atr"].rolling(60).mean()

    # MACD
    ml       = cl.ewm(12, adjust=False).mean() - cl.ewm(26, adjust=False).mean()
    sig      = ml.ewm(9,  adjust=False).mean()
    df["macd"]     = ml
    df["macd_sig"] = sig
    df["macd_prev"]= ml.shift(1)
    df["msig_prev"]= sig.shift(1)

    # Volume ratio
    df["vol_ratio"] = vl / vl.rolling(20).mean()

    # Stochastic
    l14s = lo.rolling(14).min()
    h14s = hi.rolling(14).max()
    df["stoch"] = (cl - l14s) / (h14s - l14s + 1e-9) * 100

    # Swing high/low (rolling 50-bar)
    df["swing_h"] = hi.rolling(50).max()
    df["swing_l"] = lo.rolling(50).min()

    # Fibonacci levels (50-bar swing)
    diff = df["swing_h"] - df["swing_l"]
    df["fib618"] = df["swing_l"] + diff * 0.618
    df["fib50"]  = df["swing_l"] + diff * 0.500
    df["fib382"] = df["swing_l"] + diff * 0.382

    # Session filter (UTC hour)
    df["hour"] = df.index.hour if hasattr(df.index, "hour") else 12
    try:
        df["hour"] = df.index.tz_convert("UTC").hour
    except Exception:
        try:
            df["hour"] = df.index.hour
        except Exception:
            df["hour"] = 12

    # Trend flags
    df["bull_trend"] = (df["e8"] > df["e21"]) & (df["e21"] > df["e50"]) & (cl > df["e200"])
    df["bear_trend"] = (df["e8"] < df["e21"]) & (df["e21"] < df["e50"]) & (cl < df["e200"])

    logger.info(f"Data ready: {len(df)} bars, "
                f"{df.index[0].date()} → {df.index[-1].date()}")
    return df.dropna()


# ── Trade simulation helper ─────────────────────────────

def _simulate_trades(df, signals: list) -> BacktestResult:
    """
    For each signal, scan forward bars until TP or SL is hit.
    Returns BacktestResult.
    """
    trades: List[BacktestTrade] = []
    balance = ACCOUNT

    for sig in signals:
        idx      = sig["idx"]
        entry    = sig["entry"]
        sl       = sig["sl"]
        tp       = sig["tp"]
        direction= sig["direction"]

        trade = BacktestTrade(entry=entry, sl=sl, tp=tp,
                               direction=direction, bar_idx=idx)

        # Scan forward max 200 bars
        hit = False
        for j in range(idx + 1, min(idx + 201, len(df))):
            bar = df.iloc[j]
            if direction == "BUY":
                if bar["high"] >= tp:
                    trade.pnl     = round((tp - entry) * LOT_SIZE * PNL_FACTOR - COMMISSION, 2)
                    trade.outcome = "WIN"
                    hit = True
                    break
                if bar["low"] <= sl:
                    trade.pnl     = round((sl - entry) * LOT_SIZE * PNL_FACTOR - COMMISSION, 2)
                    trade.outcome = "LOSS"
                    hit = True
                    break
            else:  # SELL
                if bar["low"] <= tp:
                    trade.pnl     = round((entry - tp) * LOT_SIZE * PNL_FACTOR - COMMISSION, 2)
                    trade.outcome = "WIN"
                    hit = True
                    break
                if bar["high"] >= sl:
                    trade.pnl     = round((entry - sl) * LOT_SIZE * PNL_FACTOR - COMMISSION, 2)
                    trade.outcome = "LOSS"
                    hit = True
                    break

        if not hit:
            trade.outcome = "OPEN"
            trade.pnl = 0.0

        trades.append(trade)

    # ── Compute statistics ──────────────────────────────
    closed = [t for t in trades if t.outcome in ("WIN","LOSS")]
    wins   = [t for t in closed if t.outcome == "WIN"]
    losses = [t for t in closed if t.outcome == "LOSS"]

    win_pnl  = sum(t.pnl for t in wins)
    loss_pnl = abs(sum(t.pnl for t in losses))

    result = BacktestResult(strategy="")
    result.trades       = len(closed)
    result.wins         = len(wins)
    result.losses       = len(losses)
    result.total_pnl    = round(sum(t.pnl for t in closed), 2)
    result.win_rate     = round(len(wins) / len(closed) * 100, 1) if closed else 0
    result.profit_factor= round(win_pnl / loss_pnl, 2) if loss_pnl > 0 else 0
    result.avg_win      = round(win_pnl / len(wins), 2)    if wins   else 0
    result.avg_loss     = round(loss_pnl / len(losses), 2) if losses else 0
    result.expectancy   = round((result.win_rate/100 * result.avg_win) -
                                ((1-result.win_rate/100) * result.avg_loss), 2)

    # Drawdown
    balance = ACCOUNT
    peak = ACCOUNT
    max_dd = 0.0
    for t in closed:
        balance += t.pnl
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown = round(max_dd, 1)
    result.final_balance= round(ACCOUNT + result.total_pnl, 2)
    return result


# ══════════════════════════════════════════════════════
# STRATEGY A — RSI7 Extreme Bounce (current system)
# SL: 2×ATR, TP: 4×ATR
# ══════════════════════════════════════════════════════

def strategy_a_rsi7_bounce(df) -> BacktestResult:
    signals = []
    in_trade = False

    for i in range(60, len(df)):
        if in_trade:
            # simple: one trade at a time
            last_sig = signals[-1] if signals else None
            if last_sig:
                row = df.iloc[i]
                if last_sig["direction"] == "BUY":
                    if row["high"] >= last_sig["tp"] or row["low"] <= last_sig["sl"]:
                        in_trade = False
                else:
                    if row["low"] <= last_sig["tp"] or row["high"] >= last_sig["sl"]:
                        in_trade = False
            continue

        row  = df.iloc[i]
        prev = df.iloc[i-1]
        hour = int(row.get("hour", 12))

        if not (SESSION_UTC[0] <= hour < SESSION_UTC[1]):
            continue
        if not (row["atr"] < row["atr_avg"] * 2.5):
            continue

        # RSI7 bounce detection
        rsi7_was_os = row["rsi7p"] < 32 or row["rsi7p2"] < 32
        rsi7_up     = row["rsi7"] > row["rsi7p"]
        rsi7_was_ob = row["rsi7p"] > 68 or row["rsi7p2"] > 68
        rsi7_dn     = row["rsi7"] < row["rsi7p"]

        atr = row["atr"]

        if row["bull_trend"] and rsi7_was_os and rsi7_up and row["rsi14"] < 60:
            sl = round(row["close"] - atr * 2.0, 2)
            tp = round(row["close"] + atr * 4.0, 2)
            signals.append({"idx": i, "entry": row["close"],
                            "sl": sl, "tp": tp, "direction": "BUY"})
            in_trade = True

        elif row["bear_trend"] and rsi7_was_ob and rsi7_dn and row["rsi14"] > 40:
            sl = round(row["close"] + atr * 2.0, 2)
            tp = round(row["close"] - atr * 4.0, 2)
            signals.append({"idx": i, "entry": row["close"],
                            "sl": sl, "tp": tp, "direction": "SELL"})
            in_trade = True

    result = _simulate_trades(df, signals)
    result.strategy = "A — RSI7 Bounce (2×ATR SL / 4×ATR TP)"
    return result


# ══════════════════════════════════════════════════════
# STRATEGY B — Fibonacci Key Level Bounce  ← NEW
# Enter at Fibonacci level with RSI7 extreme
# SL: 1.0×ATR (behind fib level), TP: 3.0×ATR (3:1 RR)
# ══════════════════════════════════════════════════════

def strategy_b_fib_bounce(df) -> BacktestResult:
    signals = []
    in_trade = False

    for i in range(100, len(df)):
        if in_trade:
            last_sig = signals[-1] if signals else None
            if last_sig:
                row = df.iloc[i]
                if last_sig["direction"] == "BUY":
                    if row["high"] >= last_sig["tp"] or row["low"] <= last_sig["sl"]:
                        in_trade = False
                else:
                    if row["low"] <= last_sig["tp"] or row["high"] >= last_sig["sl"]:
                        in_trade = False
            continue

        row  = df.iloc[i]
        hour = int(row.get("hour", 12))
        atr  = row["atr"]
        price= row["close"]

        if not (SESSION_UTC[0] <= hour < SESSION_UTC[1]):
            continue
        if not (row["atr"] < row["atr_avg"] * 2.5):
            continue

        # Check price proximity to a Fibonacci level (within 0.5×ATR)
        fib_levels = [row["fib618"], row["fib50"], row["fib382"]]
        near_fib   = any(abs(price - fib) < atr * 0.5 for fib in fib_levels)

        if not near_fib:
            continue

        rsi7_was_os = row["rsi7p"] < 35 or row["rsi7p2"] < 35
        rsi7_up     = row["rsi7"] > row["rsi7p"]
        rsi7_was_ob = row["rsi7p"] > 65 or row["rsi7p2"] > 65
        rsi7_dn     = row["rsi7"] < row["rsi7p"]

        # MACD cross confirmation
        macd_bull = row["macd"] > row["macd_sig"] and row["macd_prev"] <= row["msig_prev"]
        macd_bear = row["macd"] < row["macd_sig"] and row["macd_prev"] >= row["msig_prev"]

        if row["bull_trend"] and rsi7_was_os and rsi7_up and row["rsi14"] < 58:
            sl = round(price - atr * 1.0, 2)   # tight SL behind fib level
            tp = round(price + atr * 3.0, 2)   # 3:1 RR
            signals.append({"idx": i, "entry": price,
                            "sl": sl, "tp": tp, "direction": "BUY"})
            in_trade = True

        elif row["bear_trend"] and rsi7_was_ob and rsi7_dn and row["rsi14"] > 42:
            sl = round(price + atr * 1.0, 2)
            tp = round(price - atr * 3.0, 2)
            signals.append({"idx": i, "entry": price,
                            "sl": sl, "tp": tp, "direction": "SELL"})
            in_trade = True

    result = _simulate_trades(df, signals)
    result.strategy = "B — Fib Key Level Bounce (1×ATR SL / 3×ATR TP)"
    return result


# ══════════════════════════════════════════════════════
# STRATEGY C — EMA21 Pullback + MACD
# H1-grade trend on M5 (EMA21 > EMA50), entry at EMA21 touch
# SL: 1.5×ATR, TP: 3×ATR (2:1 RR)
# ══════════════════════════════════════════════════════

def strategy_c_ema_pullback(df) -> BacktestResult:
    signals = []
    in_trade = False

    for i in range(60, len(df)):
        if in_trade:
            last_sig = signals[-1] if signals else None
            if last_sig:
                row = df.iloc[i]
                if last_sig["direction"] == "BUY":
                    if row["high"] >= last_sig["tp"] or row["low"] <= last_sig["sl"]:
                        in_trade = False
                else:
                    if row["low"] <= last_sig["tp"] or row["high"] >= last_sig["sl"]:
                        in_trade = False
            continue

        row  = df.iloc[i]
        prev = df.iloc[i-1]
        hour = int(row.get("hour", 12))
        atr  = row["atr"]
        price= row["close"]

        if not (SESSION_UTC[0] <= hour < SESSION_UTC[1]):
            continue
        if not (row["atr"] < row["atr_avg"] * 2.5):
            continue

        e21  = row["e21"]
        e50  = row["e50"]
        e200 = row["e200"]

        # Trend: EMA21 > EMA50 > EMA200
        trend_up   = e21 > e50 and price > e200
        trend_down = e21 < e50 and price < e200

        # Pullback: price touched EMA21 from above (BUY) or below (SELL)
        touched_e21_bull = (row["low"] <= e21 * 1.002 and price > e21 and
                            row["close"] > row["open"])   # bullish candle
        touched_e21_bear = (row["high"] >= e21 * 0.998 and price < e21 and
                            row["close"] < row["open"])   # bearish candle

        # RSI not extreme
        rsi_ok_buy  = 35 < row["rsi14"] < 58
        rsi_ok_sell = 42 < row["rsi14"] < 65

        # MACD trend-aligned
        macd_bull = row["macd"] > row["macd_sig"]
        macd_bear = row["macd"] < row["macd_sig"]

        if trend_up and touched_e21_bull and rsi_ok_buy and macd_bull:
            sl = round(price - atr * 1.5, 2)
            tp = round(price + atr * 3.0, 2)
            signals.append({"idx": i, "entry": price,
                            "sl": sl, "tp": tp, "direction": "BUY"})
            in_trade = True

        elif trend_down and touched_e21_bear and rsi_ok_sell and macd_bear:
            sl = round(price + atr * 1.5, 2)
            tp = round(price - atr * 3.0, 2)
            signals.append({"idx": i, "entry": price,
                            "sl": sl, "tp": tp, "direction": "SELL"})
            in_trade = True

    result = _simulate_trades(df, signals)
    result.strategy = "C — EMA21 Pullback + MACD (1.5×ATR SL / 3×ATR TP)"
    return result


# ══════════════════════════════════════════════════════
# MAIN — run all backtests + report
# ══════════════════════════════════════════════════════

def run_backtest() -> dict:
    import pandas as pd

    try:
        df = _fetch_data(period="6mo", interval="5m")
    except Exception as e:
        logger.error(f"Data download failed: {e}")
        return {}

    results = []
    for fn, name in [
        (strategy_a_rsi7_bounce,  "A"),
        (strategy_b_fib_bounce,   "B"),
        (strategy_c_ema_pullback, "C"),
    ]:
        try:
            r = fn(df)
            results.append(r)
            logger.info(
                f"Strategy {name}: {r.trades} trades | WR={r.win_rate}% | "
                f"PF={r.profit_factor} | PnL=${r.total_pnl:+.2f} | "
                f"DD={r.max_drawdown}%"
            )
        except Exception as e:
            logger.error(f"Strategy {name} failed: {e}")

    if not results:
        return {}

    best = max(results, key=lambda r: r.profit_factor)

    _print_report(results, best)
    return {
        "results":  [r.__dict__ for r in results],
        "best":     best.strategy,
        "best_pf":  best.profit_factor,
        "best_wr":  best.win_rate,
    }


def _print_report(results, best):
    print("\n" + "═"*65)
    print("  NEXUS GOLD BACKTEST — 6 Months XAUUSD M5")
    print(f"  Account: ${ACCOUNT} | Lot: {LOT_SIZE} | Commission: ${COMMISSION}/trade")
    print("═"*65)
    print(f"  {'Strategy':<42} {'Trades':>6} {'WR%':>6} {'PF':>5} {'PnL':>8} {'DD%':>6}")
    print("─"*65)
    for r in results:
        star = " ⭐" if r is best else ""
        print(f"  {r.strategy:<42} {r.trades:>6} {r.win_rate:>5.1f}% "
              f"{r.profit_factor:>5.2f} ${r.total_pnl:>+7.2f} {r.max_drawdown:>5.1f}%{star}")
    print("═"*65)
    print(f"\n  BEST: {best.strategy}")
    print(f"  Win rate:      {best.win_rate}%")
    print(f"  Profit factor: {best.profit_factor}")
    print(f"  Avg win:       ${best.avg_win}")
    print(f"  Avg loss:      ${best.avg_loss}")
    print(f"  Expectancy:    ${best.expectancy} per trade")
    print(f"  Final balance: ${best.final_balance} (started ${ACCOUNT})")
    print(f"  Max drawdown:  {best.max_drawdown}%")
    print("═"*65 + "\n")


if __name__ == "__main__":
    run_backtest()
