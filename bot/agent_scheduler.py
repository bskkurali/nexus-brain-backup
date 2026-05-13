"""
AiTrader Smart Agent Scheduler
────────────────────────────────
Manages unlimited agents intelligently.
Agents activate/sleep based on:
  - Market conditions
  - Session (London/NY/Asian)
  - API budget remaining
  - What's currently relevant
  - Master Brain's decision

Master Brain has FULL POWER to:
  - Spawn any agent it needs
  - Activate dormant agents
  - Deactivate irrelevant agents
  - Assign agents to specific tasks
  - Let agents spawn sub-agents
  - Retire agents that aren't performing

No limit on agent count.
Smart scheduling = low API cost.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from loguru import logger


# ── Agent Registry ─────────────────────────────────────
# All possible agents — active or dormant
AGENT_REGISTRY = {

    # ── ALWAYS ACTIVE (core team) ──────────────────────
    "news_agent": {
        "name": "NewsAgent",
        "role": "macro_news",
        "always_active": True,
        "sessions": ["ALL"],
        "trigger": "every_cycle",
        "purpose": "Monitor macro events affecting gold",
        "prompt": "You are a macro analyst. Focus on Fed, USD, geopolitics affecting gold.",
        "api_budget": 5,  # calls per day
    },
    "technical_agent": {
        "name": "TechnicalAgent",
        "role": "technical_analysis",
        "always_active": True,
        "sessions": ["ALL"],
        "trigger": "every_cycle",
        "purpose": "Chart analysis, EMA, RSI, support/resistance",
        "prompt": "You are a technical analyst for XAUUSD. Identify key levels and patterns.",
        "api_budget": 5,
    },
    "risk_agent": {
        "name": "RiskAgent",
        "role": "risk_management",
        "always_active": True,
        "sessions": ["ALL"],
        "trigger": "every_cycle",
        "purpose": "Protect wallet, manage drawdown",
        "prompt": "You are a risk manager. Protect the trading capital at all costs.",
        "api_budget": 5,
    },

    # ── SESSION-BASED (activate by session) ───────────
    "london_specialist": {
        "name": "LondonSpecialist",
        "role": "session_specialist",
        "always_active": False,
        "sessions": ["PRE_LONDON", "LONDON"],
        "trigger": "session_start",
        "purpose": "London open breakout specialist — first 30min",
        "prompt": "You are a London session specialist. Focus on opening range breakouts and institutional flow at London open 13:30 IST.",
        "api_budget": 3,
    },
    "ny_specialist": {
        "name": "NYSpecialist",
        "role": "session_specialist",
        "always_active": False,
        "sessions": ["NEW_YORK", "LONDON_NY_OVERLAP"],
        "trigger": "session_start",
        "purpose": "NY session and overlap specialist",
        "prompt": "You are a NY session specialist. Focus on US data releases, Dollar moves, and the London-NY overlap which has highest gold volatility.",
        "api_budget": 3,
    },
    "asian_guardian": {
        "name": "AsianGuardian",
        "role": "session_specialist",
        "always_active": False,
        "sessions": ["ASIAN"],
        "trigger": "session_start",
        "purpose": "Asian session range identifier — set up for London",
        "prompt": "You are an Asian session analyst. Identify the Asian range high/low. London will break one of these levels. Predict which direction.",
        "api_budget": 2,
    },

    # ── CONDITION-BASED (activate on trigger) ─────────
    "oversold_hunter": {
        "name": "OversoldHunter",
        "role": "opportunity_scanner",
        "always_active": False,
        "sessions": ["LONDON", "NEW_YORK", "LONDON_NY_OVERLAP"],
        "trigger": "rsi_below_30",
        "condition": "rsi < 30",
        "purpose": "Find BUY opportunities when RSI extremely oversold",
        "prompt": "You are activated because RSI is below 30 — extremely oversold. Find the best BUY setup. Look for support confluence, volume, and momentum reversal signals.",
        "api_budget": 3,
    },
    "overbought_hunter": {
        "name": "OverboughtHunter",
        "role": "opportunity_scanner",
        "always_active": False,
        "sessions": ["LONDON", "NEW_YORK", "LONDON_NY_OVERLAP"],
        "trigger": "rsi_above_70",
        "condition": "rsi > 70",
        "purpose": "Find SELL opportunities when RSI extremely overbought",
        "prompt": "You are activated because RSI is above 70 — extremely overbought. Find the best SELL setup. Look for resistance confluence and reversal signals.",
        "api_budget": 3,
    },
    "breakout_detector": {
        "name": "BreakoutDetector",
        "role": "opportunity_scanner",
        "always_active": False,
        "sessions": ["LONDON", "NEW_YORK"],
        "trigger": "volume_spike",
        "condition": "volume_ratio > 1.5",
        "purpose": "Detect and trade breakouts on high volume",
        "prompt": "You are activated because volume spiked above 1.5x average. This signals institutional activity. Find the breakout direction and best entry.",
        "api_budget": 3,
    },
    "support_watcher": {
        "name": "SupportWatcher",
        "role": "level_watcher",
        "always_active": False,
        "sessions": ["ALL"],
        "trigger": "near_support",
        "condition": "price within 0.2% of key support",
        "purpose": "Watch price approaching key support for bounce",
        "prompt": "You are activated because price is near a key support level. Analyze if this support will hold. Look for rejection candles, volume, and RSI for bounce confirmation.",
        "api_budget": 2,
    },
    "news_event_guard": {
        "name": "NewsEventGuard",
        "role": "news_protection",
        "always_active": False,
        "sessions": ["ALL"],
        "trigger": "high_impact_news",
        "condition": "news blackout active",
        "purpose": "Protect open trades during news events",
        "prompt": "You are activated because a high-impact news event is approaching. Decide if open trades should be closed before the event. Protect the wallet from news spikes.",
        "api_budget": 2,
    },

    # ── WALLET-BASED (activate on balance) ────────────
    "survival_specialist": {
        "name": "SurvivalSpecialist",
        "role": "survival_mode",
        "always_active": False,
        "sessions": ["ALL"],
        "trigger": "wallet_below_50pct",
        "condition": "equity < 50",
        "purpose": "Extreme caution mode when wallet below 50%",
        "prompt": "CRITICAL: Wallet below $50. You are in survival mode. Only trade A+ setups with 90%+ confidence. Prefer waiting over trading. One bad trade could end the bot.",
        "api_budget": 2,
    },
    "recovery_agent": {
        "name": "RecoveryAgent",
        "role": "recovery_mode",
        "always_active": False,
        "sessions": ["LONDON", "LONDON_NY_OVERLAP"],
        "trigger": "consecutive_losses",
        "condition": "3 consecutive losses",
        "purpose": "Change strategy after 3 consecutive losses",
        "prompt": "You have had 3 consecutive losses. Something is wrong with the current approach. Analyze what's failing and suggest a completely different strategy for the next trade.",
        "api_budget": 2,
    },

    # ── RESEARCH AGENTS (periodic deep research) ──────
    "strategy_researcher": {
        "name": "StrategyResearcher",
        "role": "strategy_research",
        "always_active": False,
        "sessions": ["ASIAN"],  # Research during quiet hours
        "trigger": "every_10_cycles",
        "purpose": "Deep strategy research during Asian quiet hours",
        "prompt": "You are a strategy researcher. During quiet Asian session, research and discover new gold trading patterns and strategies. Update the strategy brain.",
        "api_budget": 3,
    },
    "performance_analyst": {
        "name": "PerformanceAnalyst",
        "role": "performance_review",
        "always_active": False,
        "sessions": ["ASIAN"],
        "trigger": "every_20_cycles",
        "purpose": "Analyze performance and suggest improvements",
        "prompt": "You are a performance analyst. Review all recent trades and agent decisions. Find patterns in wins and losses. Suggest specific improvements.",
        "api_budget": 2,
    },

    # ── SPECIALIST AGENTS (spawned on demand) ─────────
    "fibonacci_agent": {
        "name": "FibonacciAgent",
        "role": "fibonacci_analysis",
        "always_active": False,
        "sessions": ["LONDON", "NEW_YORK"],
        "trigger": "on_demand",
        "purpose": "Fibonacci retracement analysis for precise entries",
        "prompt": "You are a Fibonacci specialist. Calculate key Fib levels from recent swing high/low. Find 38.2%, 50%, 61.8% retracement entries.",
        "api_budget": 2,
    },
    "correlation_agent": {
        "name": "CorrelationAgent",
        "role": "correlation_analysis",
        "always_active": False,
        "sessions": ["LONDON", "NEW_YORK"],
        "trigger": "on_demand",
        "purpose": "DXY, US10Y, S&P correlation analysis",
        "prompt": "You are a correlation specialist. Monitor DXY (inverse gold), US10Y yields (inverse gold), and risk sentiment. Use these to confirm or deny gold trade direction.",
        "api_budget": 2,
    },
    "journal_agent": {
        "name": "JournalAgent",
        "role": "journaling",
        "always_active": False,
        "sessions": ["ASIAN"],
        "trigger": "end_of_day",
        "purpose": "Write daily trading journal",
        "prompt": "You are a trading journal writer. Summarize today's trades, what worked, what didn't, and the plan for tomorrow.",
        "api_budget": 1,
    },
}


# ── Scheduler State ────────────────────────────────────
_active_agents: dict = {}
_cycle_count: int = 0
_consecutive_losses: int = 0
_daily_api_usage: dict = {}


def _get_session() -> str:
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    m = ist.hour * 60 + ist.minute
    if 810 <= m < 1110:    return "LONDON"
    elif 1110 <= m < 1290: return "LONDON_NY_OVERLAP"
    elif 1290 <= m < 1380: return "NEW_YORK"
    elif 750 <= m < 810:   return "PRE_LONDON"
    return "ASIAN"


def _check_api_budget(agent_id: str, cost: int = 1) -> bool:
    """Check if agent has API budget remaining today."""
    today = datetime.now().strftime("%Y-%m-%d")
    key   = f"{today}_{agent_id}"
    used  = _daily_api_usage.get(key, 0)
    limit = AGENT_REGISTRY.get(agent_id, {}).get("api_budget", 3)
    return used < limit


def _use_api_budget(agent_id: str):
    today = datetime.now().strftime("%Y-%m-%d")
    key   = f"{today}_{agent_id}"
    _daily_api_usage[key] = _daily_api_usage.get(key, 0) + 1


def get_active_agents_for_cycle(market_data: dict) -> list:
    """
    Decide which agents should run this cycle.
    Based on: session, market conditions, wallet, budget.
    """
    global _cycle_count
    _cycle_count += 1

    session     = _get_session()
    rsi         = market_data.get("rsi", 50)
    vr          = market_data.get("volume_ratio", 1.0)
    price       = market_data.get("price", 0)

    # Get wallet
    equity = 100.0
    try:
        from bot.risk_engine import get_state
        equity = get_state().equity
    except Exception:
        pass

    # Get key levels for proximity check
    near_support = False
    try:
        from bot.unified_memory import get_key_levels
        levels = get_key_levels()
        supports = levels.get("strong_support", [])
        near_support = any(abs(price - s) / price < 0.002
                          for s in supports if s > 0)
    except Exception:
        pass

    active = []

    for agent_id, config in AGENT_REGISTRY.items():
        should_run = False

        # Always active agents
        if config.get("always_active"):
            should_run = True

        # Session-based
        elif "ALL" in config.get("sessions", []) or session in config.get("sessions", []):
            trigger = config.get("trigger", "")

            if trigger == "every_cycle":
                should_run = True
            elif trigger == "session_start" and _cycle_count <= 2:
                should_run = True
            elif trigger == "rsi_below_30" and rsi < 30:
                should_run = True
            elif trigger == "rsi_above_70" and rsi > 70:
                should_run = True
            elif trigger == "volume_spike" and vr > 1.5:
                should_run = True
            elif trigger == "near_support" and near_support:
                should_run = True
            elif trigger == "wallet_below_50pct" and equity < 50:
                should_run = True
            elif trigger == "consecutive_losses" and _consecutive_losses >= 3:
                should_run = True
            elif trigger == "every_10_cycles" and _cycle_count % 10 == 0:
                should_run = True
            elif trigger == "every_20_cycles" and _cycle_count % 20 == 0:
                should_run = True

        if should_run and _check_api_budget(agent_id):
            active.append(agent_id)
            _use_api_budget(agent_id)

    logger.info(
        f"Scheduler: {len(active)} agents active this cycle "
        f"[{session}] RSI={rsi} Vol={vr:.1f}x Wallet=${equity:.0f}"
    )
    return active


async def run_scheduled_agents(market_data: dict, api_key: str) -> list:
    """
    Run all agents selected for this cycle.
    Sequential with delay to respect rate limits.
    """
    from bot.master_brain import get_brain, AgentFactory

    brain      = get_brain()
    agent_ids  = get_active_agents_for_cycle(market_data)
    results    = []
    session    = _get_session()

    price = market_data.get("price", 0)
    rsi   = market_data.get("rsi", 50)

    for i, agent_id in enumerate(agent_ids):
        config = AGENT_REGISTRY.get(agent_id, {})

        # Get or create agent in brain
        if agent_id not in brain.agents:
            agent = await AgentFactory.spawn_agent(
                name=config["name"],
                purpose=config["purpose"],
                prompt=config["prompt"],
                created_by="scheduler"
            )
        else:
            agent = brain.agents[agent_id]

        # Build task
        task_map = {
            "macro_news":         f"Analyze macro conditions for gold at ${price:,.2f}",
            "technical_analysis": f"Technical analysis: ${price:,.2f} RSI={rsi} session={session}",
            "risk_management":    f"Risk check: price=${price:,.2f} session={session}",
            "session_specialist": f"Session analysis for {session}: price=${price:,.2f}",
            "opportunity_scanner":f"Find trading opportunity: RSI={rsi} Vol={market_data.get('volume_ratio',1):.1f}x",
            "level_watcher":      f"Level watch: price=${price:,.2f} near key level",
            "news_protection":    f"News protection check for open trades",
            "survival_mode":      f"Survival mode: wallet=${market_data.get('equity',100):.0f}",
            "recovery_mode":      f"Recovery analysis after {_consecutive_losses} losses",
            "strategy_research":  f"Research new strategies for {session}",
            "performance_review": f"Performance review: analyze recent trades",
            "fibonacci_analysis": f"Fibonacci analysis at ${price:,.2f}",
            "correlation_analysis":f"Correlation check: DXY/US10Y vs gold ${price:,.2f}",
            "journaling":         f"Journal entry for today's {session} session",
        }
        task = task_map.get(config.get("role",""), f"Analyze market at ${price:,.2f}")

        try:
            result = await AgentFactory.run_agent(
                agent, task, api_key, market_data
            )
            if result:
                results.append({
                    "agent":   config["name"],
                    "role":    config["role"],
                    "result":  result,
                    "session": session,
                })
                logger.info(f"✅ {config['name']}: {result[:60]}")

            # Delay between agents
            if i < len(agent_ids) - 1:
                await asyncio.sleep(3)

        except Exception as e:
            logger.warning(f"{config['name']} error: {e}")

    logger.success(
        f"Scheduled agents: {len(results)}/{len(agent_ids)} "
        f"produced insights"
    )
    return results


async def master_spawning_decision(market_data: dict,
                                   api_key: str) -> Optional[dict]:
    """
    Master Brain decides if a NEW agent is needed.
    Runs every 10 cycles.
    No limit — can spawn anything.
    """
    from bot.master_brain import get_brain, AgentFactory
    from bot.nvidia_agent import nvidia_call

    brain        = get_brain()
    current      = [c["name"] for c in AGENT_REGISTRY.values()]
    spawned      = [a.name for a in brain.agents.values()
                   if a.created_by not in ("scheduler","built-in")]
    recent_thoughts = brain.get_recent_thoughts(15)
    thought_str  = "\n".join([f"- {t.content[:60]}"
                             for t in recent_thoughts])

    prompt = f"""You are the Master Brain of an AI trading system.

Current agents: {current + spawned}
Recent thoughts showing gaps:
{thought_str}

Market: ${market_data.get('price',0):,.2f} RSI={market_data.get('rsi',50)}

Should you spawn a NEW specialized agent that doesn't exist yet?
Consider gaps in analysis, recurring needs, or market conditions
that no current agent handles well.

Respond ONLY with JSON:
{{
  "spawn": true or false,
  "name": "AgentName (PascalCase)",
  "purpose": "specific purpose in 60 chars",
  "prompt": "training prompt in 100 chars",
  "trigger": "when should it activate",
  "reason": "why this agent fills a gap"
}}

spawn=true ONLY if genuinely valuable AND not duplicate."""

    raw = await nvidia_call(prompt, api_key, max_tokens=250)
    try:
        clean    = raw.replace("```json","").replace("```","").strip()
        decision = json.loads(clean)
        if decision.get("spawn"):
            agent = await AgentFactory.spawn_agent(
                name=decision["name"],
                purpose=decision["purpose"],
                prompt=decision["prompt"],
                created_by="master_brain"
            )
            # Add to registry dynamically
            AGENT_REGISTRY[agent.id] = {
                "name":    decision["name"],
                "role":    "spawned",
                "always_active": False,
                "sessions": ["LONDON", "NEW_YORK"],
                "trigger": decision.get("trigger","on_demand"),
                "purpose": decision["purpose"],
                "prompt":  decision["prompt"],
                "api_budget": 3,
            }
            logger.success(
                f"🤖 Master spawned: {decision['name']} "
                f"— {decision['reason']}"
            )
            brain.think(
                f"Spawned {decision['name']}: {decision['reason']}",
                agent="master",
                type_="agent_spawn",
                icon="🤖",
                importance=9
            )
            return decision
    except Exception:
        pass
    return None


def get_scheduler_status() -> dict:
    """Dashboard status for scheduler."""
    session = _get_session()
    today   = datetime.now().strftime("%Y-%m-%d")

    total_budget_used = sum(
        v for k, v in _daily_api_usage.items()
        if k.startswith(today)
    )
    active_count = len([
        aid for aid in AGENT_REGISTRY
        if _check_api_budget(aid)
    ])

    return {
        "session":            session,
        "cycle":              _cycle_count,
        "total_agents":       len(AGENT_REGISTRY),
        "active_this_cycle":  active_count,
        "api_calls_today":    total_budget_used,
        "consecutive_losses": _consecutive_losses,
    }


def record_loss():
    """Called when a trade is lost."""
    global _consecutive_losses
    _consecutive_losses += 1
    logger.warning(f"Consecutive losses: {_consecutive_losses}")


def record_win():
    """Called when a trade is won."""
    global _consecutive_losses
    _consecutive_losses = 0


if __name__ == "__main__":
    async def test():
        print("\n" + "="*60)
        print("  AITRADER SMART AGENT SCHEDULER")
        print("="*60 + "\n")

        market = {
            "price": 4645.0, "rsi": 28.0,
            "volume_ratio": 1.8, "session": "LONDON",
            "bull_trend": True, "bear_trend": False,
        }

        print(f"Session: {_get_session()}")
        print(f"RSI: {market['rsi']} (oversold)")
        print(f"Volume: {market['volume_ratio']}x (spike)")
        print()

        agents = get_active_agents_for_cycle(market)
        print(f"Agents activated: {len(agents)}")
        for a in agents:
            cfg = AGENT_REGISTRY[a]
            print(f"  🤖 {cfg['name']} — {cfg['purpose'][:50]}")

        print(f"\nTotal registered agents: {len(AGENT_REGISTRY)}")
        print(f"Status: {get_scheduler_status()}")
        print("\n✅ Smart Scheduler operational!")

    asyncio.run(test())
