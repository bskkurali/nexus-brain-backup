"""
NEXUS Learning Brain
─────────────────────
The bot gets smarter after every trade.

After each trade closes:
  → Records what conditions were active
  → Tracks which conditions WIN vs LOSE
  → Adjusts signal confidence weights daily
  → Reports learned patterns to Claude

The bot reads its own history → improves itself.
"""

import json, os
from datetime import datetime, date, timedelta
from loguru import logger

LEARN_FILE  = "data/brain_learning.json"
TRADE_LOG   = "data/trade_outcomes.json"
MAX_HISTORY = 200  # keep last 200 trades

CONDITION_KEYS = [
    "rsi7_extreme",      # RSI7 dipped below 30 / above 70
    "full_ema_stack",    # EMA8>21>50>200 aligned
    "macd_cross",        # fresh MACD cross
    "h4_trending",       # H4 EMA8 sloping in direction
    "stoch_bounce",      # stochastic cross in right zone
    "active_session",    # London or NY session
    "atr_normal",        # no news spike
    "bull_candle",       # last candle confirms direction
]


def load_learning() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(LEARN_FILE):
        try: return json.load(open(LEARN_FILE))
        except: pass
    return {
        "condition_stats": {k: {"wins":0,"losses":0} for k in CONDITION_KEYS},
        "symbol_stats":    {"XAUUSD":{"wins":0,"losses":0},
                            "BTC":   {"wins":0,"losses":0}},
        "session_stats":   {"london":{"wins":0,"losses":0},
                            "ny":    {"wins":0,"losses":0},
                            "overlap":{"wins":0,"losses":0}},
        "hour_stats":      {},
        "day_stats":       {},
        "total_trades":    0,
        "last_updated":    str(date.today()),
        "best_conditions": [],
        "worst_conditions": [],
        "weekly_wr":       0.0,
    }


def load_trades() -> list:
    if os.path.exists(TRADE_LOG):
        try: return json.load(open(TRADE_LOG))
        except: pass
    return []


def save_learning(d): json.dump(d, open(LEARN_FILE,"w"), indent=2)
def save_trades(t):   json.dump(t[-MAX_HISTORY:], open(TRADE_LOG,"w"), indent=2)


def record_trade_outcome(symbol: str, direction: str, outcome: str,
                          pnl: float, conditions: dict, session: str, hour: int):
    """
    Call this after EVERY trade closes.
    outcome: 'WIN' or 'LOSS'
    conditions: dict of {condition_key: True/False} that were active on entry
    """
    trades = load_trades()
    learn  = load_learning()
    won    = outcome == "WIN"

    trade = {
        "date":       str(date.today()),
        "time":       datetime.now().strftime("%H:%M"),
        "symbol":     symbol,
        "direction":  direction,
        "outcome":    outcome,
        "pnl":        round(pnl, 4),
        "conditions": conditions,
        "session":    session,
        "hour":       hour,
    }
    trades.append(trade)
    save_trades(trades)

    # Update condition stats
    for k, active in conditions.items():
        if active and k in learn["condition_stats"]:
            if won: learn["condition_stats"][k]["wins"] += 1
            else:   learn["condition_stats"][k]["losses"] += 1

    # Symbol stats
    sym_key = "BTC" if "BTC" in symbol.upper() else "XAUUSD"
    if won: learn["symbol_stats"][sym_key]["wins"] += 1
    else:   learn["symbol_stats"][sym_key]["losses"] += 1

    # Session stats
    if session in learn["session_stats"]:
        if won: learn["session_stats"][session]["wins"] += 1
        else:   learn["session_stats"][session]["losses"] += 1

    # Hour stats
    hk = str(hour)
    if hk not in learn["hour_stats"]:
        learn["hour_stats"][hk] = {"wins":0,"losses":0}
    if won: learn["hour_stats"][hk]["wins"] += 1
    else:   learn["hour_stats"][hk]["losses"] += 1

    learn["total_trades"] += 1

    # Recalculate weekly WR
    week_ago = str(date.today() - timedelta(days=7))
    recent   = [t for t in trades if t["date"] >= week_ago]
    if recent:
        learn["weekly_wr"] = round(
            sum(1 for t in recent if t["outcome"]=="WIN") / len(recent) * 100, 1)

    # Best/worst conditions (WR > 70% with 5+ trades)
    best = []
    worst = []
    for k, s in learn["condition_stats"].items():
        tot = s["wins"] + s["losses"]
        if tot >= 5:
            wr = s["wins"]/tot*100
            if wr >= 70: best.append(f"{k}={wr:.0f}%WR({tot}T)")
            if wr < 50:  worst.append(f"{k}={wr:.0f}%WR({tot}T)")
    learn["best_conditions"]  = best
    learn["worst_conditions"] = worst
    learn["last_updated"] = str(date.today())

    save_learning(learn)
    logger.info(f"🧠 Learning updated | {outcome} | Weekly WR: {learn['weekly_wr']}%")


def get_learned_prompt() -> str:
    """Returns learning summary for Claude's prompt."""
    learn  = load_learning()
    trades = load_trades()

    if learn["total_trades"] < 5:
        return "Learning: <5 trades yet — standard rules apply"

    # Last 10 trades
    recent_10 = trades[-10:]
    recent_wr = sum(1 for t in recent_10 if t["outcome"]=="WIN") / len(recent_10) * 100 if recent_10 else 0

    # Best hours
    best_hours = sorted(
        [(h, s["wins"]/(s["wins"]+s["losses"]+1e-9)*100)
         for h, s in learn["hour_stats"].items()
         if s["wins"]+s["losses"] >= 3],
        key=lambda x: -x[1]
    )[:3]

    # Symbol performance
    xau = learn["symbol_stats"]["XAUUSD"]
    btc = learn["symbol_stats"]["BTC"]
    xau_wr = xau["wins"]/(xau["wins"]+xau["losses"]+1e-9)*100
    btc_wr = btc["wins"]/(btc["wins"]+btc["losses"]+1e-9)*100

    lines = [
        "━━━ WHAT I'VE LEARNED (self-improvement) ━━━",
        f"Total trades:  {learn['total_trades']} | Weekly WR: {learn['weekly_wr']}%",
        f"Last 10 trades WR: {recent_wr:.0f}%",
        f"XAUUSD WR: {xau_wr:.0f}% ({xau['wins']}W/{xau['losses']}L) | BTC WR: {btc_wr:.0f}% ({btc['wins']}W/{btc['losses']}L)",
    ]

    if learn["best_conditions"]:
        lines.append(f"✅ BEST conditions: {', '.join(learn['best_conditions'][:3])}")
    if learn["worst_conditions"]:
        lines.append(f"❌ AVOID conditions: {', '.join(learn['worst_conditions'][:3])}")
    if best_hours:
        lines.append(f"⏰ Best hours (UTC): {', '.join(f'{h}:00={w:.0f}%' for h,w in best_hours)}")

    # Adaptive advice based on recent performance
    if recent_wr >= 80:
        lines.append("📈 HOT STREAK: Recent 80%+ WR → maintain current approach")
    elif recent_wr < 50:
        lines.append("⚠️ COLD STREAK: Recent <50% WR → be MORE selective, raise score threshold")
    elif learn["weekly_wr"] < 60:
        lines.append("🔄 ADJUSTING: Weekly WR low → only take RSI7 extreme signals (score≥4)")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def should_block_entry(conditions: dict, session: str = "", hour: int = 0) -> tuple:
    """
    Returns (block: bool, reason: str).
    Called BEFORE every trade — prevents repeating known losing patterns.
    """
    learn  = load_learning()
    trades = load_trades()

    if learn["total_trades"] < 8:
        return False, ""

    # Block on 3+ consecutive losses
    consec = 0
    for t in reversed(trades[-10:]):
        if t["outcome"] == "LOSS": consec += 1
        else: break
    if consec >= 3:
        return True, f"{consec} consecutive losses — resting until next session"

    # Block on known bad session (< 35% WR, 10+ trades)
    if session:
        sk = session.lower()
        if sk in learn["session_stats"]:
            s = learn["session_stats"][sk]
            tot = s["wins"] + s["losses"]
            if tot >= 10 and s["wins"] / tot < 0.35:
                return True, f"Session {session} WR={s['wins']/tot*100:.0f}% — learned to skip"

    # Block on known bad hour (< 30% WR, 8+ trades)
    hk = str(hour)
    if hk in learn["hour_stats"]:
        s = learn["hour_stats"][hk]
        tot = s["wins"] + s["losses"]
        if tot >= 8 and s["wins"] / tot < 0.30:
            return True, f"Hour {hour}:00 UTC WR={s['wins']/tot*100:.0f}% — learned to skip"

    # Block if 2+ currently-active conditions are known losers (< 35% WR, 8+ trades)
    losing_conditions = [
        k for k, s in learn["condition_stats"].items()
        if (s["wins"] + s["losses"]) >= 8 and
           s["wins"] / (s["wins"] + s["losses"]) < 0.35
    ]
    active_losers = [k for k in losing_conditions if conditions.get(k)]
    if len(active_losers) >= 2:
        return True, f"Known losing conditions active: {', '.join(active_losers)}"

    return False, ""


def get_adaptive_threshold() -> int:
    """
    Dynamically adjusts signal score threshold based on recent performance.
    Good run → score≥3 (more trades)
    Bad run  → score≥5 (more selective)
    """
    learn  = load_learning()
    trades = load_trades()
    recent = trades[-20:] if len(trades) >= 20 else trades
    if len(recent) < 5:
        return 3  # default

    wr = sum(1 for t in recent if t["outcome"]=="WIN") / len(recent) * 100
    consec_loss = 0
    for t in reversed(recent):
        if t["outcome"] == "LOSS": consec_loss += 1
        else: break

    if consec_loss >= 3:  return 5  # 3 losses in a row → very selective
    if wr >= 80:          return 3  # hot streak → take more trades
    if wr >= 70:          return 3  # good → normal
    if wr >= 60:          return 4  # average → tighten
    return 5                         # bad → only best setups
