"""
AiTrader Unified Memory System
────────────────────────────────
ONE place that stores EVERYTHING.
All modules read/write here.

Structure:
  data/memory.json
  ├── research_cache      → cached web searches (30 day TTL)
  ├── news_archive        → daily macro log
  ├── sniper_log          → every Commander grade A+/A/B/C
  ├── team_decisions      → what each agent said per trade
  ├── price_levels        → learned support/resistance
  ├── strategy_stats      → which strategies work in which sessions
  ├── agent_performance   → each agent's accuracy over time
  ├── market_patterns     → recurring patterns observed
  └── audit_trail         → full log of every decision
"""

import json
import os
import hashlib
from datetime import datetime, timedelta
from loguru import logger

MEMORY_FILE = "data/memory.json"
CACHE_TTL_HOURS = 6   # Research cache valid for 6 hours
MAX_AUDIT_ENTRIES = 500


def _now() -> str:
    return datetime.now().isoformat()


def _load() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(MEMORY_FILE):
        try:
            return json.load(open(MEMORY_FILE))
        except Exception:
            pass
    return _default()


def _save(mem: dict):
    os.makedirs("data", exist_ok=True)
    mem["last_updated"] = _now()
    json.dump(mem, open(MEMORY_FILE, "w"), indent=2)


def _default() -> dict:
    return {
        "created":          _now(),
        "last_updated":     _now(),
        "version":          2,

        # Web research cache — avoid repeating same searches
        "research_cache":   {},   # key=hash(query) val={result, expires_at}

        # Daily macro news log
        "news_archive":     [],   # [{date, sentiment, bias, driver, score}]

        # Sniper Commander decision log
        "sniper_log":       [],   # [{timestamp, grade, direction, conf, session, deployed}]

        # Team agent decisions per trade
        "team_decisions":   [],   # [{team_id, ticket, entry_says, guard_says, scalper_says}]

        # Learned price levels
        "price_levels": {
            "strong_support":    [],   # levels that held multiple times
            "strong_resistance": [],   # levels that rejected multiple times
            "weekly_high":       None,
            "weekly_low":        None,
            "daily_high":        None,
            "daily_low":         None,
        },

        # Strategy performance by session
        "strategy_stats": {
            "LONDON":            {"trades": 0, "wins": 0, "strategies": {}},
            "LONDON_NY_OVERLAP": {"trades": 0, "wins": 0, "strategies": {}},
            "NEW_YORK":          {"trades": 0, "wins": 0, "strategies": {}},
            "ASIAN":             {"trades": 0, "wins": 0, "strategies": {}},
        },

        # Agent accuracy tracking
        "agent_performance": {
            "sniper_commander":  {"signals": 0, "correct": 0, "grades": {}},
            "news_agent":        {"calls": 0, "accurate": 0},
            "technical_agent":   {"calls": 0, "accurate": 0},
            "entry_agent":       {"calls": 0, "good_entries": 0},
            "guard_agent":       {"calls": 0, "correct_blocks": 0},
        },

        # Market patterns observed
        "market_patterns":  [],   # [{pattern, conditions, win_rate, sample_size}]

        # Full audit trail
        "audit_trail":      [],   # [{timestamp, event, agent, data}]
    }


# ── Research Cache ─────────────────────────────────────

def get_cached_research(query: str) -> str | None:
    """Return cached research if still valid."""
    mem = _load()
    key = hashlib.md5(query.encode()).hexdigest()[:12]
    cached = mem["research_cache"].get(key)
    if not cached:
        return None
    expires = datetime.fromisoformat(cached["expires_at"])
    if datetime.now() > expires:
        del mem["research_cache"][key]
        _save(mem)
        return None
    logger.debug(f"Cache hit: {query[:50]}")
    return cached["result"]


def cache_research(query: str, result: str,
                   ttl_hours: int = CACHE_TTL_HOURS):
    """Cache research result."""
    if not result or len(result) < 20:
        return
    mem = _load()
    key = hashlib.md5(query.encode()).hexdigest()[:12]
    mem["research_cache"][key] = {
        "query":      query,
        "result":     result,
        "cached_at":  _now(),
        "expires_at": (datetime.now() + timedelta(hours=ttl_hours)).isoformat(),
    }
    # Keep cache size reasonable
    if len(mem["research_cache"]) > 100:
        oldest = sorted(mem["research_cache"].items(),
                       key=lambda x: x[1]["cached_at"])[:20]
        for k, _ in oldest:
            del mem["research_cache"][k]
    _save(mem)
    logger.debug(f"Cached: {query[:50]} (TTL={ttl_hours}h)")


# ── News Archive ───────────────────────────────────────

def log_news(sentiment: str, bias: str, score: int,
             driver: str, confidence: int):
    """Log daily news sentiment."""
    mem = _load()
    entry = {
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "time":       datetime.now().strftime("%H:%M"),
        "sentiment":  sentiment,
        "bias":       bias,
        "score":      score,
        "driver":     driver[:100],
        "confidence": confidence,
    }
    # Don't duplicate same hour
    recent = mem["news_archive"][-1] if mem["news_archive"] else {}
    if recent.get("time","")[:2] != entry["time"][:2]:
        mem["news_archive"].append(entry)
        mem["news_archive"] = mem["news_archive"][-200:]
        _save(mem)


def get_news_trend() -> dict:
    """Get recent news trend (last 24h)."""
    mem = _load()
    recent = mem["news_archive"][-24:]
    if not recent:
        return {"trend": "NEUTRAL", "dominant_bias": "WAIT"}
    bulls  = sum(1 for n in recent if n["bias"] == "BUY")
    bears  = sum(1 for n in recent if n["bias"] == "SELL")
    return {
        "trend":        "BULLISH" if bulls > bears else "BEARISH" if bears > bulls else "NEUTRAL",
        "dominant_bias":"BUY" if bulls > bears else "SELL" if bears > bulls else "WAIT",
        "bull_count":   bulls,
        "bear_count":   bears,
        "latest":       recent[-1] if recent else {},
    }


# ── Sniper Log ─────────────────────────────────────────

def log_sniper_grade(grade: str, direction: str, confidence: int,
                     session: str, deployed: bool,
                     conditions_met: list, conditions_failed: list):
    """Log every Commander signal grade."""
    mem = _load()
    entry = {
        "timestamp":        _now(),
        "grade":            grade,
        "direction":        direction,
        "confidence":       confidence,
        "session":          session,
        "deployed":         deployed,
        "conditions_met":   conditions_met,
        "conditions_failed": conditions_failed,
    }
    mem["sniper_log"].append(entry)
    mem["sniper_log"] = mem["sniper_log"][-500:]

    # Update agent performance
    perf = mem["agent_performance"]["sniper_commander"]
    perf["signals"] += 1
    perf["grades"][grade] = perf["grades"].get(grade, 0) + 1
    _save(mem)


def get_sniper_stats() -> dict:
    """Get Commander performance stats."""
    mem = _load()
    log = mem["sniper_log"]
    if not log:
        return {"total": 0, "deployed": 0, "grade_breakdown": {}}
    return {
        "total":           len(log),
        "deployed":        sum(1 for s in log if s["deployed"]),
        "grade_breakdown": mem["agent_performance"]["sniper_commander"]["grades"],
        "session_breakdown": {
            s: sum(1 for x in log if x["session"] == s) for s in
            ["LONDON", "LONDON_NY_OVERLAP", "NEW_YORK", "ASIAN"]
        },
        "best_session": max(
            ["LONDON", "LONDON_NY_OVERLAP", "NEW_YORK"],
            key=lambda s: sum(1 for x in log
                            if x["session"] == s and x["deployed"])
        ),
    }


# ── Team Decisions ─────────────────────────────────────

def log_team_decision(team_id: str, ticket: str,
                       entry_agent_says: str, guard_agent_says: str,
                       scalper_agent_says: str, result: str):
    """Log what each team agent decided."""
    mem = _load()
    mem["team_decisions"].append({
        "timestamp":         _now(),
        "team_id":           team_id,
        "ticket":            ticket,
        "entry_agent":       entry_agent_says[:100],
        "guard_agent":       guard_agent_says[:100],
        "scalper_agent":     scalper_agent_says[:100],
        "result":            result,
    })
    mem["team_decisions"] = mem["team_decisions"][-200:]
    _save(mem)


# ── Price Levels ───────────────────────────────────────

def update_price_levels(price: float, level_type: str,
                         times_tested: int = 1):
    """Learn and store key price levels."""
    mem = _load()
    levels = mem["price_levels"]
    target = "strong_support" if level_type == "support" else "strong_resistance"
    rounded = round(price / 5) * 5  # Round to nearest $5

    existing = [l for l in levels[target] if abs(l["price"] - rounded) < 3]
    if existing:
        existing[0]["tests"] = existing[0].get("tests", 1) + times_tested
        existing[0]["last_seen"] = _now()
    else:
        levels[target].append({
            "price":     rounded,
            "tests":     times_tested,
            "first_seen": _now(),
            "last_seen": _now(),
        })
    levels[target] = sorted(
        levels[target], key=lambda x: x["tests"], reverse=True
    )[:20]
    _save(mem)


def get_key_levels() -> dict:
    """Get strongest learned price levels."""
    mem = _load()
    levels = mem["price_levels"]
    return {
        "strong_support":    [l["price"] for l in levels["strong_support"][:5]],
        "strong_resistance": [l["price"] for l in levels["strong_resistance"][:5]],
        "weekly_high":       levels.get("weekly_high"),
        "weekly_low":        levels.get("weekly_low"),
    }


# ── Strategy Stats ─────────────────────────────────────

def log_trade_result(session: str, strategy: str,
                      won: bool, pnl: float):
    """Track which strategies work in which sessions."""
    mem = _load()
    stats = mem["strategy_stats"]
    if session not in stats:
        stats[session] = {"trades": 0, "wins": 0, "strategies": {}}
    stats[session]["trades"] += 1
    if won:
        stats[session]["wins"] += 1
    if strategy not in stats[session]["strategies"]:
        stats[session]["strategies"][strategy] = {"trades": 0, "wins": 0}
    stats[session]["strategies"][strategy]["trades"] += 1
    if won:
        stats[session]["strategies"][strategy]["wins"] += 1
    _save(mem)


def get_best_strategy_for_session(session: str) -> str:
    """Get highest win rate strategy for a session."""
    mem = _load()
    strats = mem["strategy_stats"].get(session, {}).get("strategies", {})
    if not strats:
        return "sniper_pullback"
    best = max(strats.items(),
              key=lambda x: x[1]["wins"]/max(x[1]["trades"],1))
    return best[0]


# ── Market Patterns ────────────────────────────────────

def add_pattern(name: str, conditions: dict, won: bool):
    """Record observed market patterns."""
    mem = _load()
    existing = [p for p in mem["market_patterns"] if p["name"] == name]
    if existing:
        p = existing[0]
        p["sample_size"] = p.get("sample_size", 0) + 1
        wins = round(p["win_rate"] * (p["sample_size"]-1)/100) + (1 if won else 0)
        p["win_rate"] = round(wins / p["sample_size"] * 100, 1)
    else:
        mem["market_patterns"].append({
            "name":        name,
            "conditions":  conditions,
            "win_rate":    100.0 if won else 0.0,
            "sample_size": 1,
            "added":       _now(),
        })
    mem["market_patterns"] = sorted(
        mem["market_patterns"],
        key=lambda x: x["win_rate"], reverse=True
    )[:50]
    _save(mem)


def get_top_patterns() -> list:
    """Get highest win rate patterns."""
    mem = _load()
    return [p for p in mem["market_patterns"]
            if p.get("sample_size", 0) >= 3][:5]


# ── Audit Trail ────────────────────────────────────────

def audit(event: str, agent: str, data: dict = None):
    """Log every important decision for full audit."""
    mem = _load()
    mem["audit_trail"].append({
        "timestamp": _now(),
        "event":     event,
        "agent":     agent,
        "data":      data or {},
    })
    mem["audit_trail"] = mem["audit_trail"][-MAX_AUDIT_ENTRIES:]
    _save(mem)


def get_audit_trail(limit: int = 50) -> list:
    """Get recent audit entries."""
    mem = _load()
    return mem["audit_trail"][-limit:]


# ── Full Memory Summary for AI ─────────────────────────

def get_memory_summary() -> str:
    """
    Returns complete memory summary for AI agents.
    Injected into every agent prompt so they remember everything.
    """
    mem = _load()

    # News trend
    news = get_news_trend()
    # Sniper stats
    sniper = get_sniper_stats()
    # Key levels
    levels = get_key_levels()
    # Top patterns
    patterns = get_top_patterns()
    # Best strategies per session
    session_strategies = {
        s: get_best_strategy_for_session(s)
        for s in ["LONDON", "LONDON_NY_OVERLAP", "NEW_YORK"]
    }

    lines = [
        "═══ UNIFIED MEMORY ═══",
        f"Last updated: {mem['last_updated'][:16]}",
        f"Research cache: {len(mem['research_cache'])} items",
        f"Audit entries: {len(mem['audit_trail'])}",
        "",
        "NEWS TREND (last 24h):",
        f"  Bias: {news['dominant_bias']} | "
        f"Bull:{news['bull_count']} Bear:{news['bear_count']}",
        f"  Latest: {news['latest'].get('driver','-')[:60]}",
        "",
        "SNIPER COMMANDER:",
        f"  Total signals: {sniper.get('total',0)} | "
        f"Deployed: {sniper.get('deployed',0)}",
        f"  Grades: {sniper.get('grade_breakdown',{})}",
        f"  Best session: {sniper.get('best_session','LONDON')}",
        "",
        "KEY PRICE LEVELS:",
        f"  Support:    {levels['strong_support']}",
        f"  Resistance: {levels['strong_resistance']}",
        "",
        "TOP PATTERNS:",
    ]
    for p in patterns:
        lines.append(
            f"  • {p['name']}: {p['win_rate']}% "
            f"({p['sample_size']} trades)"
        )

    lines += [
        "",
        "BEST STRATEGIES:",
    ]
    for sess, strat in session_strategies.items():
        lines.append(f"  {sess}: {strat}")

    return "\n".join(lines)


# ── Wire into NVIDIA call with cache ───────────────────

async def cached_research(query: str, api_key: str) -> str:
    """
    Research with caching — never repeat same query.
    """
    cached = get_cached_research(query)
    if cached:
        return cached

    from bot.nvidia_agent import nvidia_call
    result = await nvidia_call(
        f"Research this for gold trading: {query}\n"
        f"Be specific and concise. Max 150 words.",
        api_key, max_tokens=250
    )
    if result:
        cache_research(query, result)
        audit("research", "system", {"query": query[:80]})
    return result


if __name__ == "__main__":
    print("\n=== UNIFIED MEMORY TEST ===\n")

    # Test caching
    cache_research("XAUUSD support levels May 2026",
                   "Strong support at $4,620 and $4,580. Resistance at $4,680.")
    r = get_cached_research("XAUUSD support levels May 2026")
    print(f"Cache test: {'✅' if r else '❌'}")

    # Test news log
    log_news("BULLISH", "BUY", 70, "Fed dovish — gold positive", 80)
    trend = get_news_trend()
    print(f"News trend: {trend['dominant_bias']}")

    # Test sniper log
    log_sniper_grade("A", "BUY", 85, "LONDON", True,
                     ["EMA aligned", "RSI ok", "Volume ok"],
                     ["Asian session"])
    stats = get_sniper_stats()
    print(f"Sniper signals: {stats['total']}")

    # Test price levels
    update_price_levels(4620, "support", 3)
    update_price_levels(4680, "resistance", 2)
    levels = get_key_levels()
    print(f"Key levels: sup={levels['strong_support']} res={levels['strong_resistance']}")

    # Test audit
    audit("test_event", "system", {"note": "memory test"})
    trail = get_audit_trail(5)
    print(f"Audit entries: {len(trail)}")

    # Full summary
    print("\n" + get_memory_summary())
    print("\n✅ Unified Memory operational!")
