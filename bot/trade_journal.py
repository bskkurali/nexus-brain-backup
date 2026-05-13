"""
Trade Journal — Human-Like Memory
───────────────────────────────────
The bot remembers:
  - What happened at each Fibonacci level (level memory)
  - Which setups win vs lose by regime + score (pattern learning)
  - Recent trade streak and performance
  - Everything persisted to data/trade_journal.json

This is what an experienced trader does instinctively.
The bot builds this knowledge through every trade it takes.
"""

import json
import os
from datetime import datetime
from loguru import logger

JOURNAL_FILE = "data/trade_journal.json"


# ── Storage ────────────────────────────────────────────

def _load() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(JOURNAL_FILE):
        try:
            return json.load(open(JOURNAL_FILE))
        except Exception:
            pass
    return {
        "created":       datetime.now().isoformat(),
        "trades":        [],        # Full trade records
        "level_memory":  {},        # price_zone → {tests, wins, losses, ...}
        "setup_stats":   {},        # quality_regime_score → {trades, wins, losses}
        "total_trades":  0,
        "total_wins":    0,
        "total_losses":  0,
    }


def _save(data: dict):
    try:
        json.dump(data, open(JOURNAL_FILE, "w"), indent=2)
    except Exception:
        pass


def _price_zone(price: float, zone_size: float = 5.0) -> str:
    """Round price to nearest $5 zone for level grouping."""
    return str(round(price / zone_size) * zone_size)


# ── Trade Recording ────────────────────────────────────

def record_entry(trade_id: str, conditions: dict):
    """
    Called when a trade opens.
    conditions should include:
      direction, entry_price, fib_level, score, quality, regime,
      rsi7, atr, h1_bias, session
    """
    data = _load()
    trade = {
        "id":         trade_id,
        "entry_time": datetime.now().isoformat(),
        "close_time": None,
        "result":     None,   # "win" | "loss" | None=open
        "pnl":        0.0,
        **conditions,
    }
    data["trades"].append(trade)
    data["total_trades"] += 1
    _save(data)
    logger.debug(f"Journal: entry {trade_id} recorded")


def record_close(trade_id: str, result: str, pnl: float):
    """
    Called when a trade closes.
    Updates level memory and setup stats — the core of the learning loop.
    result = "TP_HIT" | "SL_HIT" | "WIN" | "LOSS"
    """
    data  = _load()
    won   = result in ("TP_HIT", "WIN", "TRAIL_HIT") or pnl > 0

    for t in data["trades"]:
        if t["id"] != trade_id:
            continue

        t["close_time"] = datetime.now().isoformat()
        t["result"]     = "win" if won else "loss"
        t["pnl"]        = round(pnl, 2)

        # ── Level memory ──────────────────────────────
        fib_level = t.get("fib_level", 0)
        if fib_level and fib_level > 0:
            zone = _price_zone(fib_level)
            lm   = data["level_memory"].setdefault(zone, {
                "tests":       0,
                "wins":        0,
                "losses":      0,
                "last_result": None,
                "last_time":   None,
                "price_zone":  zone,
            })
            lm["tests"]  += 1
            lm["wins" if won else "losses"] += 1
            lm["last_result"] = "win" if won else "loss"
            lm["last_time"]   = datetime.now().isoformat()

        # ── Setup pattern stats ────────────────────────
        quality = t.get("quality", "B")
        regime  = t.get("regime",  "UNKNOWN")
        score   = t.get("score",   0)
        key     = f"{quality}_{regime}_score{score}"
        ss      = data["setup_stats"].setdefault(key, {
            "trades": 0, "wins": 0, "losses": 0
        })
        ss["trades"] += 1
        ss["wins" if won else "losses"] += 1
        break

    data["total_wins"   if won else "total_losses"] += 1
    _save(data)
    logger.info(f"Journal: {trade_id} → {'WIN ✅' if won else 'LOSS ❌'} ${pnl:+.2f}")


# ── Query Functions (used by Claude prompt + scoring) ──

def get_level_history(fib_level: float) -> dict:
    """
    What happened last time we traded this Fibonacci level?
    Returns verdict: strong_level | weak_level | mixed | untested
    """
    data = _load()
    zone = _price_zone(fib_level)
    lm   = data["level_memory"].get(zone, {})

    if not lm or lm.get("tests", 0) == 0:
        return {"tests": 0, "wins": 0, "losses": 0, "win_rate": 0, "verdict": "untested"}

    tests    = lm["tests"]
    wins     = lm.get("wins", 0)
    losses   = lm.get("losses", 0)
    win_rate = round(wins / tests * 100) if tests else 0

    if tests < 2:
        verdict = "first_test"
    elif win_rate >= 65:
        verdict = "strong_level"
    elif win_rate <= 35:
        verdict = "weak_level"
    else:
        verdict = "mixed"

    return {
        "tests":       tests,
        "wins":        wins,
        "losses":      losses,
        "win_rate":    win_rate,
        "last_result": lm.get("last_result"),
        "last_time":   lm.get("last_time"),
        "verdict":     verdict,
        "price_zone":  zone,
    }


def get_similar_setup_stats(quality: str, regime: str, score: int) -> dict:
    """Win rate for this exact quality+regime+score combination."""
    data = _load()

    # Exact match first
    key = f"{quality}_{regime}_score{score}"
    ss  = data["setup_stats"].get(key, {})
    if ss.get("trades", 0) >= 2:
        trades = ss["trades"]
        wins   = ss.get("wins", 0)
        return {
            "trades":   trades,
            "wins":     wins,
            "win_rate": round(wins / trades * 100),
            "verdict":  "exact_match",
        }

    # Broad match — same quality + regime, any score
    total, total_wins = 0, 0
    for k, v in data["setup_stats"].items():
        if quality in k and regime in k:
            total      += v.get("trades", 0)
            total_wins += v.get("wins",   0)

    if total == 0:
        return {"trades": 0, "win_rate": 0, "verdict": "no_data"}

    return {
        "trades":   total,
        "wins":     total_wins,
        "win_rate": round(total_wins / total * 100),
        "verdict":  "broad_match",
    }


def get_recent_performance(n: int = 10) -> dict:
    """Summary of the last N closed trades — streak, win rate, total P&L."""
    data   = _load()
    closed = [t for t in data["trades"] if t.get("result") in ("win", "loss")]
    recent = closed[-n:]

    if not recent:
        return {"trades": 0, "win_rate": 0, "streak": "none", "total_pnl": 0.0}

    wins = sum(1 for t in recent if t["result"] == "win")
    pnl  = sum(t.get("pnl", 0) for t in recent)

    # Current streak
    streak_count = 0
    last_result  = recent[-1]["result"]
    for t in reversed(recent):
        if t["result"] == last_result:
            streak_count += 1
        else:
            break

    streak_str = f"{'+' if last_result == 'win' else '-'}{streak_count}"

    return {
        "trades":      len(recent),
        "wins":        wins,
        "losses":      len(recent) - wins,
        "win_rate":    round(wins / len(recent) * 100),
        "total_pnl":   round(pnl, 2),
        "streak":      streak_str,
        "last_result": last_result,
    }


def get_journal_summary() -> str:
    """One-line summary for log output."""
    perf = get_recent_performance()
    data = _load()
    t    = data.get("total_trades", 0)
    w    = data.get("total_wins",   0)
    wr   = round(w / t * 100) if t else 0
    return (
        f"Journal: {t} trades | WR={wr}% | "
        f"last10: {perf['win_rate']}% streak={perf['streak']} "
        f"P&L=${perf['total_pnl']:+.2f}"
    )
