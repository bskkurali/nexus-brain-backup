"""
AiTrader Strategy Brain
────────────────────────
Permanent strategy knowledge base.
- Stores strategies discovered from web searches
- Learns from every trade (win/loss)
- Builds pattern library over time
- Claude reads this before every trade decision
- Gets smarter with every cycle
"""

import json
import os
from datetime import datetime
from loguru import logger

BRAIN_FILE = "data/strategy_brain.json"

# ── Default knowledge base ─────────────────────────────
DEFAULT_BRAIN = {
    "version": 1,
    "created": datetime.now().isoformat(),
    "last_updated": datetime.now().isoformat(),

    # Discovered strategies from web research
    "strategies": {
        "sniper_pullback": {
            "name": "EMA Pullback Sniper",
            "source": "built-in",
            "description": "Price pulls back to EMA9 in bull trend, closes above with volume",
            "conditions": {
                "trend": "bull",
                "rsi_min": 40, "rsi_max": 75,
                "volume_min": 0.65,
                "ema_alignment": "9>21>50",
                "trigger": "pullback to EMA9 + bullish close"
            },
            "performance": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0},
            "best_session": "London/NY overlap",
            "notes": "Core strategy — highest confidence"
        },
        "rsi_oversold_bounce": {
            "name": "RSI Oversold Bounce",
            "source": "built-in",
            "description": "RSI below 30, price at support, momentum reversal",
            "conditions": {
                "rsi_max": 30,
                "near_support": True,
                "volume_min": 0.5,
                "trigger": "RSI cross above 30"
            },
            "performance": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0},
            "best_session": "Any",
            "notes": "High win rate at key support levels"
        },
        "london_breakout": {
            "name": "London Open Breakout",
            "source": "built-in",
            "description": "First 30min of London session breakout with volume",
            "conditions": {
                "session": "LONDON",
                "volume_min": 1.2,
                "ema_spread_min": 0.20,
                "trigger": "Break of Asian range high/low"
            },
            "performance": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0},
            "best_session": "London open 13:30 IST",
            "notes": "Strong institutional flow at London open"
        },
        "vwap_reclaim": {
            "name": "VWAP Reclaim",
            "source": "built-in",
            "description": "Price reclaims VWAP from below with momentum",
            "conditions": {
                "trigger": "close crosses above VWAP",
                "volume_min": 0.8,
                "rsi_min": 40
            },
            "performance": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0},
            "best_session": "London/NY",
            "notes": "Institutional benchmark — strong signal"
        }
    },

    # Trade history with pattern analysis
    "trade_history": [],

    # Market patterns observed
    "patterns": {
        "asian_session": "Low volume, range-bound, avoid trading",
        "london_open": "Volume spike, breakout likely, watch direction",
        "ny_open": "High volatility, follow trend",
        "overlap": "Best session — highest volume and movement",
        "rsi_30": "Strong bounce zone historically",
        "rsi_70": "Distribution zone — watch for reversal",
        "dxy_inverse": "Gold moves opposite to DXY 85% of time"
    },

    # What web research has found
    "research_notes": [],

    # Session performance stats
    "session_stats": {
        "LONDON": {"trades": 0, "wins": 0},
        "LONDON_NY_OVERLAP": {"trades": 0, "wins": 0},
        "NEW_YORK": {"trades": 0, "wins": 0},
        "ASIAN": {"trades": 0, "wins": 0},
    },

    # Key price levels learned
    "key_levels": {
        "major_support": [],
        "major_resistance": [],
        "weekly_high": None,
        "weekly_low": None,
    },

    # Risk insights
    "risk_insights": [
        "Never trade in Asian session unless RSI extreme (<25 or >75)",
        "London open first 15min most volatile — wait for direction",
        "Always check DXY before gold BUY — should be falling",
        "Low volume (<0.5x) = trap setup — avoid",
        "News events create 30min blackout before and after",
    ]
}


# ── Storage ────────────────────────────────────────────
_brain: dict = {}


def load_brain() -> dict:
    global _brain
    os.makedirs("data", exist_ok=True)
    if os.path.exists(BRAIN_FILE):
        try:
            with open(BRAIN_FILE) as f:
                _brain = json.load(f)
            logger.info(f"Strategy brain loaded — {len(_brain.get('strategies',{}))} strategies, "
                       f"{len(_brain.get('trade_history',[]))} trades")
            return _brain
        except Exception as e:
            logger.warning(f"Brain load failed: {e} — using defaults")
    _brain = DEFAULT_BRAIN.copy()
    save_brain()
    return _brain


def save_brain():
    os.makedirs("data", exist_ok=True)
    _brain["last_updated"] = datetime.now().isoformat()
    with open(BRAIN_FILE, "w") as f:
        json.dump(_brain, f, indent=2)


def get_brain() -> dict:
    if not _brain:
        load_brain()
    return _brain


# ── Learn from trade result ────────────────────────────
def record_trade_result(ticket: str, direction: str, entry: float,
                        close_price: float, pnl: float, strategy_used: str,
                        session: str, rsi: float, notes: str = ""):
    brain = get_brain()

    won = pnl > 0
    record = {
        "ticket":       ticket,
        "direction":    direction,
        "entry":        entry,
        "close":        close_price,
        "pnl":          round(pnl, 2),
        "won":          won,
        "strategy":     strategy_used,
        "session":      session,
        "rsi_at_entry": rsi,
        "notes":        notes,
        "timestamp":    datetime.now().isoformat(),
    }
    brain["trade_history"].append(record)

    # Update strategy performance
    if strategy_used in brain["strategies"]:
        s = brain["strategies"][strategy_used]["performance"]
        s["trades"] += 1
        if won: s["wins"] += 1
        else:   s["losses"] += 1
        s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0

    # Update session stats
    if session in brain["session_stats"]:
        brain["session_stats"][session]["trades"] += 1
        if won: brain["session_stats"][session]["wins"] += 1

    # Auto-learn: add insight if pattern found
    if pnl < -2.0:
        insight = f"LOSS PATTERN: {direction} in {session} at RSI={rsi:.1f} → ${pnl:.2f}"
        if insight not in brain["risk_insights"]:
            brain["risk_insights"].append(insight)
            logger.warning(f"Brain learned: {insight}")

    if pnl > 2.0:
        insight = f"WIN PATTERN: {direction} in {session} at RSI={rsi:.1f} → +${pnl:.2f}"
        if insight not in brain["risk_insights"]:
            brain["risk_insights"].append(insight)
            logger.success(f"Brain learned: {insight}")

    save_brain()
    logger.info(f"Trade recorded in brain: {ticket} PnL=${pnl:.2f} ({'WIN' if won else 'LOSS'})")


# ── Store web research ─────────────────────────────────
def store_research(query: str, findings: str, source: str = "web"):
    brain = get_brain()
    # Only store if meaningful
    if len(findings) < 20:
        return
    note = {
        "query":    query,
        "findings": findings[:500],
        "source":   source,
        "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    brain["research_notes"].append(note)
    # Keep last 50 research notes
    if len(brain["research_notes"]) > 50:
        brain["research_notes"] = brain["research_notes"][-50:]
    save_brain()
    logger.info(f"Research stored: {query[:50]}")


# ── Add new strategy discovered from web ──────────────
def add_strategy(name: str, description: str, conditions: dict,
                 source: str = "web_research"):
    brain = get_brain()
    key = name.lower().replace(" ", "_")
    if key not in brain["strategies"]:
        brain["strategies"][key] = {
            "name":        name,
            "source":      source,
            "description": description,
            "conditions":  conditions,
            "performance": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0},
            "added":       datetime.now().isoformat(),
        }
        save_brain()
        logger.success(f"New strategy added to brain: {name}")
        return True
    return False


# ── Update key price levels ────────────────────────────
def update_levels(support: float = None, resistance: float = None,
                  weekly_high: float = None, weekly_low: float = None):
    brain = get_brain()
    levels = brain["key_levels"]
    if support:
        if support not in levels["major_support"]:
            levels["major_support"].append(round(support, 2))
            levels["major_support"] = sorted(levels["major_support"])[-10:]
    if resistance:
        if resistance not in levels["major_resistance"]:
            levels["major_resistance"].append(round(resistance, 2))
            levels["major_resistance"] = sorted(levels["major_resistance"])[-10:]
    if weekly_high: levels["weekly_high"] = round(weekly_high, 2)
    if weekly_low:  levels["weekly_low"]  = round(weekly_low, 2)
    save_brain()


# ── Get brain summary for AI Agent ────────────────────
def get_brain_summary() -> str:
    brain = get_brain()

    # Best performing strategies
    strats = brain.get("strategies", {})
    top_strats = sorted(
        [(k, v) for k, v in strats.items() if v["performance"]["trades"] > 0],
        key=lambda x: x[1]["performance"]["win_rate"], reverse=True
    )[:3]

    # Recent research
    recent = brain.get("research_notes", [])[-3:]

    # Key levels
    levels = brain.get("key_levels", {})
    sup = levels.get("major_support", [])[-3:]
    res = levels.get("major_resistance", [])[-3:]

    # Session stats
    sess = brain.get("session_stats", {})
    best_sess = max(sess.items(),
                   key=lambda x: x[1]["wins"]/x[1]["trades"] if x[1]["trades"]>0 else 0,
                   default=("LONDON_NY_OVERLAP", {}))[0] if any(v["trades"]>0 for v in sess.values()) else "LONDON_NY_OVERLAP"

    # Risk insights
    insights = brain.get("risk_insights", [])[-5:]

    lines = [
        "═══ STRATEGY BRAIN ═══",
        f"Total strategies: {len(strats)} | Trades recorded: {len(brain.get('trade_history',[]))}",
    ]

    if top_strats:
        lines.append("\nTop performing strategies:")
        for k, v in top_strats:
            p = v["performance"]
            lines.append(f"  • {v['name']}: {p['win_rate']}% win rate ({p['wins']}W/{p['losses']}L)")

    if sup or res:
        lines.append(f"\nKey levels: Support={sup} | Resistance={res}")

    lines.append(f"\nBest session: {best_sess}")

    if recent:
        lines.append("\nRecent research:")
        for r in recent:
            lines.append(f"  [{r['date']}] {r['query'][:40]}: {r['findings'][:100]}")

    if insights:
        lines.append("\nLearned insights:")
        for i in insights:
            lines.append(f"  ⚡ {i}")

    return "\n".join(lines)


# Load on import
load_brain()


if __name__ == "__main__":
    brain = get_brain()
    print("\n=== STRATEGY BRAIN ===")
    print(f"Strategies: {len(brain['strategies'])}")
    print(f"Trade history: {len(brain['trade_history'])}")
    print(f"Research notes: {len(brain['research_notes'])}")
    print(f"\n{get_brain_summary()}")
