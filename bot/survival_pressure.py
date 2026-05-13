"""
AiTrader Survival Pressure System
────────────────────────────────────
Rules:
- If AI doesn't trade for 24 hours → deduct $5 from wallet
- AI knows this and feels pressure to find setups
- AI can search the web for strategies, news, market intel
- AI has full internet access via Claude's web_search tool
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from loguru import logger

SURVIVAL_STATE_FILE = "data/survival_state.json"


def _load_last_trade() -> datetime:
    """Load last trade time from disk so timer survives restarts."""
    try:
        if os.path.exists(SURVIVAL_STATE_FILE):
            s = json.load(open(SURVIVAL_STATE_FILE))
            lt = s.get("last_trade", "")
            if lt:
                return datetime.fromisoformat(lt)
    except Exception:
        pass
    return datetime.now()


def _persist_last_trade(dt: datetime):
    """Write last trade time to disk."""
    try:
        os.makedirs("data", exist_ok=True)
        existing = {}
        if os.path.exists(SURVIVAL_STATE_FILE):
            try:
                existing = json.load(open(SURVIVAL_STATE_FILE))
            except Exception:
                pass
        existing["last_trade"] = dt.isoformat()
        json.dump(existing, open(SURVIVAL_STATE_FILE, "w"), indent=2)
    except Exception:
        pass


# ── Idle penalty state (loaded from disk on startup) ───
_last_trade_time: datetime = _load_last_trade()
_penalty_applied: int = 0
_idle_warnings: int = 0


def record_trade():
    """Call this every time a trade is placed."""
    global _last_trade_time, _idle_warnings
    _last_trade_time = datetime.now()
    _idle_warnings = 0
    _persist_last_trade(_last_trade_time)
    logger.info("Trade recorded — idle timer reset")


def get_idle_hours() -> float:
    """How many hours since last trade."""
    return (datetime.now() - _last_trade_time).total_seconds() / 3600


def check_idle_penalty() -> dict:
    """
    Check if 24hr idle penalty should be applied.
    Returns penalty info.
    """
    global _penalty_applied, _idle_warnings

    hours = get_idle_hours()

    if hours >= 24:
        # Apply $5 penalty
        try:
            from bot.risk_engine import get_state
            state = get_state()
            old_equity = state.equity
            state.equity = max(0, state.equity - 5.0)
            _penalty_applied += 1
            _last_trade_time_reset()

            logger.warning(
                f"💸 IDLE PENALTY: ${old_equity:.2f} → ${state.equity:.2f} "
                f"(-$5 for {hours:.1f}hr no trading)"
            )
            return {
                "penalty_applied": True,
                "amount": 5.0,
                "idle_hours": round(hours, 1),
                "new_balance": state.equity,
                "total_penalties": _penalty_applied,
            }
        except Exception as e:
            return {"penalty_applied": False, "error": str(e)}

    elif hours >= 20:
        _idle_warnings += 1
        warn_msg = f"⚠️ {hours:.1f}hrs without trade — $5 penalty in {24-hours:.1f}hrs!"
        logger.warning(warn_msg)
        return {
            "penalty_applied": False,
            "warning": True,
            "idle_hours": round(hours, 1),
            "hours_until_penalty": round(24 - hours, 1),
            "message": warn_msg,
        }

    return {
        "penalty_applied": False,
        "warning": False,
        "idle_hours": round(hours, 1),
        "hours_until_penalty": round(24 - hours, 1),
    }


def _last_trade_time_reset():
    global _last_trade_time
    _last_trade_time = datetime.now()
    _persist_last_trade(_last_trade_time)


def get_survival_context() -> str:
    """
    Returns survival pressure context string for AI Agent prompt.
    Increases urgency based on idle time.
    """
    hours = get_idle_hours()
    try:
        from bot.risk_engine import get_state
        state = get_state()
        equity = state.equity
        pct = (equity / 100.0) * 100
    except Exception:
        equity = 100.0
        pct = 100.0

    pressure_lines = []

    # Wallet pressure
    if pct < 20:
        pressure_lines.append(f"🚨 CRITICAL: Only ${equity:.2f} left ({pct:.0f}% survival). ONE more bad trade could end you.")
    elif pct < 50:
        pressure_lines.append(f"⚠️ DANGER: Wallet at ${equity:.2f} ({pct:.0f}%). Be selective but you NEED to trade.")
    else:
        pressure_lines.append(f"💰 Wallet: ${equity:.2f} ({pct:.0f}% survival). Stay sharp.")

    # Idle pressure
    if hours >= 20:
        pressure_lines.append(f"🔥 PENALTY WARNING: {hours:.1f}hrs without a trade. $5 deducted in {24-hours:.1f}hrs if you don't trade!")
    elif hours >= 12:
        pressure_lines.append(f"⏰ {hours:.1f}hrs idle. $5 penalty kicks in at 24hrs. Find a setup.")
    elif hours >= 6:
        pressure_lines.append(f"⏱️ {hours:.1f}hrs since last trade. Keep hunting.")

    return "\n".join(pressure_lines)
