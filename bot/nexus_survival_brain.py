"""
NEXUS Survival Brain  v2
─────────────────────────
The bot's #1 rule: SURVIVE.

Philosophy:
  → A trade not taken loses $0.
  → A bad trade can lose everything.
  → Grow the account slowly and it compounds.
  → Lose the account and it's game over.

Modes (auto-switch based on balance + streak):
  AGGRESSIVE  → Normal trading, score≥3
  CAUTIOUS    → 3+ losses, score≥4
  SURVIVAL    → Balance < $60, score≥6 only
  DAILY_TARGET_HIT → Protect profits, score≥5
  STOPPED     → Daily loss limit, NO TRADES
"""

import json, os
from datetime import datetime, date, timedelta
from loguru import logger

SURVIVAL_FILE  = "data/survival_brain.json"
STARTING_CAP   = 100.0
DAILY_TARGET   = 0.10    # 10% daily target
MAX_DAILY_LOSS = 0.05    # 5% max daily loss
DANGER_LEVEL   = 0.60    # Survival mode below $60
STOP_LEVEL     = 0.40    # Hard stop below $40


def load() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(SURVIVAL_FILE):
        try: return json.load(open(SURVIVAL_FILE))
        except: pass
    return {
        "starting_capital":   STARTING_CAP,
        "peak_balance":       STARTING_CAP,
        "daily_start":        STARTING_CAP,
        "daily_target":       STARTING_CAP * (1 + DAILY_TARGET),
        "daily_pnl":          0.0,
        "today":              str(date.today()),
        "days_profitable":    0,
        "days_losing":        0,
        "consecutive_losses": 0,
        "total_wins":         0,
        "total_losses":       0,
        "mode":               "AGGRESSIVE",
        "xauusd_pnl":         0.0,
        "btc_pnl":            0.0,
    }


def save(d): json.dump(d, open(SURVIVAL_FILE,"w"), indent=2)


def update_after_trade(outcome: str, pnl: float, symbol: str = "XAUUSD"):
    """Call after every trade closes."""
    d = load()
    d["daily_pnl"] += pnl
    if "BTC" in symbol.upper(): d["btc_pnl"] += pnl
    else:                        d["xauusd_pnl"] += pnl

    if outcome == "WIN":
        d["consecutive_losses"] = 0
        d["total_wins"] += 1
    else:
        d["consecutive_losses"] += 1
        d["total_losses"] += 1
    save(d)
    logger.info(f"💰 Trade {outcome} | Daily PnL: ${d['daily_pnl']:+.2f} | "
                f"Consecutive losses: {d['consecutive_losses']}")


def get_survival_prompt(equity: float) -> str:
    d    = load()
    today= str(date.today())

    # New day reset
    if d["today"] != today:
        if d["daily_pnl"] > 0:  d["days_profitable"] += 1
        elif d["daily_pnl"] < 0: d["days_losing"] += 1
        d["daily_start"] = equity
        d["daily_target"] = equity * (1 + DAILY_TARGET)
        d["daily_pnl"]   = 0.0
        d["xauusd_pnl"]  = 0.0
        d["btc_pnl"]     = 0.0
        d["today"]       = today
        logger.info(f"📅 New day | Target: ${d['daily_target']:.2f}")

    d["daily_pnl"] = equity - d["daily_start"]
    if equity > d["peak_balance"]: d["peak_balance"] = equity

    loss_pct = abs(d["daily_pnl"]) / max(d["daily_start"], 1) if d["daily_pnl"] < 0 else 0
    prog_pct = (equity - d["daily_start"]) / max(d["daily_start"], 1) * 100

    # Mode selection
    if equity <= d["starting_capital"] * STOP_LEVEL:
        d["mode"] = "STOPPED"
    elif loss_pct >= MAX_DAILY_LOSS:
        d["mode"] = "STOPPED"
    elif equity < d["starting_capital"] * DANGER_LEVEL:
        d["mode"] = "SURVIVAL"
    elif d["consecutive_losses"] >= 3:
        d["mode"] = "CAUTIOUS"
    elif prog_pct >= DAILY_TARGET * 100:
        d["mode"] = "TARGET_HIT"
    else:
        d["mode"] = "AGGRESSIVE"

    save(d)

    total = d["total_wins"] + d["total_losses"]
    overall_wr = d["total_wins"] / total * 100 if total > 0 else 0
    dd_pct = (d["peak_balance"] - equity) / d["peak_balance"] * 100 if d["peak_balance"] > 0 else 0
    remaining = max(0, d["daily_target"] - equity)

    if d["mode"] == "STOPPED":
        return (f"⛔ STOPPED — Balance ${equity:.2f} | Daily loss ${abs(d['daily_pnl']):.2f} "
                f"exceeded limit OR balance below ${d['starting_capital']*STOP_LEVEL:.0f}. "
                f"DO NOT TRADE. Wait for next day.")

    if d["mode"] == "SURVIVAL":
        return (f"🚨 SURVIVAL MODE — ${equity:.2f} left! "
                f"ONLY take score≥6 setups (both RSI7 extreme AND MACD cross). "
                f"TP tight. SL wider. Protect every dollar.")

    if d["mode"] == "CAUTIOUS":
        return (f"⚠️ CAUTIOUS — {d['consecutive_losses']} losses in a row. "
                f"Raise score threshold to ≥4. Only A+ setups. "
                f"Skip B quality trades. Wallet: ${equity:.2f}")

    if d["mode"] == "TARGET_HIT":
        return (f"🎯 DAILY TARGET HIT! +${d['daily_pnl']:.2f} (+{prog_pct:.1f}%). "
                f"Protect profits now. Only score≥5 setups. Don't give back gains.")

    return f"""━━━ SURVIVAL BRAIN ━━━
Mode:    {d['mode']} | Balance: ${equity:.2f}
Daily:   ${d['daily_pnl']:+.2f} ({prog_pct:+.1f}%) | Target: +${remaining:.2f} more
Peak:    ${d['peak_balance']:.2f} | Drawdown: {dd_pct:.1f}%
Streak:  {d['consecutive_losses']} losses in a row | All-time WR: {overall_wr:.0f}% ({total} trades)
XAUUSD:  ${d.get('xauusd_pnl',0):+.2f} today | BTC: ${d.get('btc_pnl',0):+.2f} today
Days:    {d['days_profitable']}P / {d['days_losing']}L

SURVIVAL MINDSET:
  → Never risk more than 1% per trade
  → A WAIT is not a loss — it's discipline
  → Compound wins. Protect losses.
  → Bad day? Stop early. Come back tomorrow fresh.
━━━━━━━━━━━━━━━━━━━━━"""
