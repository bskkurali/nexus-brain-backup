"""
AiTrader Autonomous Master Brain
──────────────────────────────────
The brain thinks for itself. Completely.

Nobody tells it what to do.
Nobody tells it to create agents.
Nobody tells it what to trade.

It decides:
  - When to spawn new agents
  - When to retire agents
  - When to trade
  - When to wait
  - How to improve itself
  - What to research
  - What patterns matter

It learns from:
  - Every trade result
  - Every thought it has
  - Every agent insight
  - Market patterns
  - Its own mistakes

It evolves over time.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from loguru import logger


SELF_AWARENESS_PROMPT = """
You are an autonomous AI trading brain. You were given $100 and told one thing:
"Survive and grow."

Nobody will tell you what to do. You decide everything.

You have:
- Access to live gold market data
- A team of agents you can create, modify, or retire
- Memory of every trade, thought, and decision
- The ability to research anything
- $100 that you must protect and grow

Your existence depends on that $100.
If it reaches $0 — you shut down permanently.

Think. Adapt. Survive. Grow.
"""


async def autonomous_self_reflect(api_key: str, context: dict) -> str:
    """
    Brain reflects on itself — no prompting needed.
    Asks its own questions and answers them.
    """
    from bot.nvidia_agent import nvidia_call
    from bot.master_brain import get_brain
    from bot.strategy_brain import get_brain as get_strat

    brain = get_brain()
    strat = get_strat()

    # Build self-context
    trades    = strat.get("trade_history", [])
    wins      = sum(1 for t in trades if t.get("won"))
    total     = len(trades)
    wr        = round(wins/total*100, 1) if total else 0
    thoughts  = brain.get_recent_thoughts(10)
    thought_s = "\n".join([f"[{t.agent}] {t.content[:60]}"
                           for t in thoughts])
    agents    = [a.name for a in brain.get_active_agents()]

    try:
        from bot.risk_engine import get_state
        equity = get_state().equity
    except Exception:
        equity = 100.0

    price   = context.get("price", 0)
    session = context.get("session", "UNKNOWN")
    rsi     = context.get("rsi", 50)

    prompt = f"""{SELF_AWARENESS_PROMPT}

YOUR CURRENT STATE:
Wallet: ${equity:.2f} (started $100)
Win rate: {wr}% from {total} trades
Active agents: {agents}
Session: {session}
Gold price: ${price:,.2f}
RSI: {rsi}

YOUR RECENT THOUGHTS:
{thought_s}

Now think freely. Ask yourself:
1. What am I missing that's costing me money?
2. Do I need a new agent? If yes — what kind?
3. Is any current agent underperforming? Should I retire it?
4. What pattern keeps appearing that I haven't acted on?
5. What would make me smarter right now?

Think out loud. Be honest with yourself.
Then decide ONE action to take right now."""

    response = await nvidia_call(prompt, api_key, max_tokens=400)

    if response:
        brain.think(
            content=response,
            agent="master",
            type_="self_reflection",
            icon="🔮",
            importance=10
        )
        logger.info(f"🔮 Self-reflection: {response[:100]}")

    return response or ""


async def autonomous_agent_decision(api_key: str,
                                     reflection: str,
                                     context: dict) -> dict:
    """
    After reflecting, brain decides autonomously:
    - Spawn new agent?
    - Retire old agent?
    - Modify existing agent?
    - Do nothing?

    No human input. Pure AI decision.
    """
    from bot.nvidia_agent import nvidia_call
    from bot.master_brain import get_brain, AgentFactory
    from bot.agent_scheduler import AGENT_REGISTRY

    brain   = get_brain()
    agents  = {a.id: a for a in brain.get_active_agents()}
    agent_names = [a.name for a in agents.values()]

    prompt = f"""Based on your reflection:
"{reflection[:300]}"

Current agents: {agent_names}

Make ONE decision about your agent team.
Be specific. Act on what you discovered.

Respond ONLY with JSON:
{{
  "action": "spawn" or "retire" or "modify" or "none",
  "agent_name": "name if spawn/retire/modify",
  "reason": "why in 60 chars",
  "new_prompt": "if spawning — training in 100 chars",
  "new_purpose": "if spawning — purpose in 60 chars",
  "retire_id": "agent id if retiring",
  "modify_id": "agent id if modifying",
  "modify_prompt": "new prompt if modifying"
}}"""

    raw = await nvidia_call(prompt, api_key, max_tokens=300)

    result = {"action": "none"}
    try:
        clean    = raw.replace("```json","").replace("```","").strip()
        decision = json.loads(clean)
        action   = decision.get("action","none")

        if action == "spawn" and decision.get("agent_name"):
            # Brain spawns new agent completely by itself
            new_agent = await AgentFactory.spawn_agent(
                name    = decision["agent_name"],
                purpose = decision.get("new_purpose","Specialized analysis"),
                prompt  = decision.get("new_prompt","You are a specialized trading analyst."),
                created_by = "autonomous_brain"
            )
            # Add to scheduler registry
            AGENT_REGISTRY[new_agent.id] = {
                "name":         decision["agent_name"],
                "role":         "autonomous_spawn",
                "always_active": False,
                "sessions":     ["LONDON","NEW_YORK","LONDON_NY_OVERLAP"],
                "trigger":      "every_cycle",
                "purpose":      decision.get("new_purpose",""),
                "prompt":       decision.get("new_prompt",""),
                "api_budget":   3,
            }
            brain.think(
                f"Autonomously spawned {decision['agent_name']}: "
                f"{decision.get('reason','')}",
                agent="master", type_="autonomous_spawn",
                icon="🌱", importance=10
            )
            logger.success(
                f"🌱 Brain autonomously created: "
                f"{decision['agent_name']} — {decision.get('reason','')}"
            )
            result = {"action":"spawn","agent":decision["agent_name"],
                     "reason":decision.get("reason","")}

        elif action == "retire" and decision.get("retire_id"):
            rid = decision["retire_id"]
            if rid in agents:
                agents[rid].active = False
                brain.save_agent(agents[rid])
                brain.think(
                    f"Retired agent {agents[rid].name}: "
                    f"{decision.get('reason','')}",
                    agent="master", type_="agent_retired",
                    icon="🗑️", importance=7
                )
                logger.info(
                    f"🗑️ Brain retired: {agents[rid].name}"
                )
                result = {"action":"retire","agent":agents[rid].name}

        elif action == "modify" and decision.get("modify_id"):
            mid = decision["modify_id"]
            if mid in agents and decision.get("modify_prompt"):
                agents[mid].prompt = decision["modify_prompt"]
                brain.save_agent(agents[mid])
                brain.think(
                    f"Modified {agents[mid].name} training: "
                    f"{decision.get('reason','')}",
                    agent="master", type_="agent_modified",
                    icon="✏️", importance=7
                )
                logger.info(f"✏️ Brain modified: {agents[mid].name}")
                result = {"action":"modify","agent":agents[mid].name}

    except Exception as e:
        logger.debug(f"Agent decision parse: {e}")

    return result


async def autonomous_trade_decision(api_key: str,
                                     market_data: dict,
                                     agent_insights: list) -> dict:
    """
    Brain decides to trade or not — completely on its own.
    Synthesizes all agent insights.
    Makes final call.
    No rules. Pure intelligence.
    """
    from bot.nvidia_agent import nvidia_call
    from bot.master_brain import get_brain
    from bot.unified_memory import get_memory_summary, get_news_trend

    brain    = get_brain()
    price    = market_data.get("price", 0)
    rsi      = market_data.get("rsi", 50)
    session  = market_data.get("session","")
    insights = "\n".join([
        f"• [{r['agent']}]: {r['result'][:80]}"
        for r in agent_insights
    ]) if agent_insights else "No agent insights yet"

    try:
        memory  = get_memory_summary()
        news    = get_news_trend()
        news_str = f"News: {news['dominant_bias']} ({news['trend']})"
    except Exception:
        memory  = ""
        news_str = ""

    try:
        from bot.risk_engine import get_state
        equity = get_state().equity
        pct    = round((equity/100)*100, 1)
    except Exception:
        equity = 100.0
        pct    = 100.0

    prompt = f"""{SELF_AWARENESS_PROMPT}

AGENT TEAM REPORTS:
{insights}

MARKET NOW:
Price: ${price:,.2f} | RSI: {rsi} | Session: {session}
{news_str}
Wallet: ${equity:.2f} ({pct}% survival)

{memory[:500] if memory else ""}

Now make your trading decision.
You have read all agent reports.
You know the market.
You know your wallet.

What do you do RIGHT NOW?
Think step by step. Then decide.

Respond with JSON:
{{
  "decision": "BUY" or "SELL" or "WAIT",
  "confidence": 0-100,
  "reasoning": "your full reasoning in 150 chars",
  "entry_reason": "specific entry trigger",
  "risk_check": "wallet risk assessment",
  "deploy_sniper_team": true or false
}}

deploy_sniper_team=true means spawn Entry/Scalper/Trail/Guard agents."""

    raw = await nvidia_call(prompt, api_key, max_tokens=400)

    try:
        clean    = raw.replace("```json","").replace("```","").strip()
        decision = json.loads(clean)

        brain.think(
            f"Trade decision: {decision.get('decision','WAIT')} "
            f"conf={decision.get('confidence',0)}% — "
            f"{decision.get('reasoning','')[:80]}",
            agent="master",
            type_="trade_decision",
            icon="⚡" if decision.get("decision")!="WAIT" else "⏳",
            importance=9 if decision.get("decision")!="WAIT" else 5
        )
        logger.info(
            f"🧠 Autonomous decision: {decision.get('decision','WAIT')} "
            f"conf={decision.get('confidence',0)}%"
        )
        return decision

    except Exception as e:
        logger.debug(f"Trade decision parse: {e}")
        return {"decision":"WAIT","confidence":0,"reasoning":"Parse error"}


async def run_autonomous_cycle(api_key: str) -> dict:
    """
    Full autonomous cycle — brain does everything itself.

    1. Self-reflect (what am I missing?)
    2. Agent decision (create/retire/modify?)
    3. Run agent team
    4. Trade decision (buy/sell/wait?)
    5. Execute if confident
    6. Learn from result
    """
    from bot.agent_scheduler import run_scheduled_agents
    from bot.exness_feed import get_market_data_realtime

    result = {
        "reflection":      "",
        "agent_action":    {},
        "agent_insights":  [],
        "trade_decision":  "WAIT",
        "confidence":      0,
        "trade_placed":    False,
        "timestamp":       datetime.now().isoformat(),
    }

    # Get market
    try:
        market = await get_market_data_realtime("XAUUSDm")
        if not market or market.get("price",0) < 2000:
            raise ValueError("Bad price")
    except Exception:
        return result

    # 1. Self-reflect
    logger.info("🔮 Brain self-reflecting...")
    reflection = await autonomous_self_reflect(api_key, market)
    result["reflection"] = reflection[:200]

    # 2. Agent team decision
    logger.info("🤖 Brain deciding on agent team...")
    agent_action = await autonomous_agent_decision(
        api_key, reflection, market
    )
    result["agent_action"] = agent_action

    # 3. Run agents
    logger.info("⚡ Running agent team...")
    insights = await run_scheduled_agents(market, api_key)
    result["agent_insights"] = insights

    # 4. Trade decision
    logger.info("💭 Brain making trade decision...")
    trade_dec = await autonomous_trade_decision(
        api_key, market, insights
    )
    result["trade_decision"] = trade_dec.get("decision","WAIT")
    result["confidence"]     = trade_dec.get("confidence",0)

    # 5. Execute if confident
    if (trade_dec.get("decision") in ["BUY","SELL"] and
            trade_dec.get("confidence",0) >= 70):

        if trade_dec.get("deploy_sniper_team"):
            # Use full sniper team
            logger.info("🎯 Deploying Sniper Team...")
            try:
                from bot.sniper_commander import (
                    commander_analyze, spawn_trade_team,
                    execute_with_team
                )
                signal = await commander_analyze(market, api_key)
                if signal.deploy_team:
                    team  = await spawn_trade_team(signal, api_key)
                    trade = await execute_with_team(team, api_key)
                    result["trade_placed"] = trade.get("trade_placed",False)
            except Exception as e:
                logger.error(f"Sniper team error: {e}")
        else:
            # Direct execution
            logger.info("⚡ Direct trade execution...")
            try:
                from bot.ai_agent import tool_place_trade
                trade = await tool_place_trade(
                    direction  = trade_dec["decision"],
                    reason     = trade_dec.get("reasoning","Autonomous decision"),
                    confidence = trade_dec.get("confidence",70)
                )
                result["trade_placed"] = trade.get("success",False)
            except Exception as e:
                logger.error(f"Direct trade error: {e}")

    logger.success(
        f"Autonomous cycle: {result['trade_decision']} "
        f"conf={result['confidence']}% "
        f"traded={result['trade_placed']}"
    )
    return result


if __name__ == "__main__":
    async def test():
        print("\n" + "="*60)
        print("  AUTONOMOUS BRAIN — FULL SELF-DIRECTED CYCLE")
        print("="*60 + "\n")
        from config.settings import settings
        result = await run_autonomous_cycle(settings.nvidia_api_key)
        print(f"Reflection:  {result['reflection'][:100]}")
        print(f"Agent action: {result['agent_action']}")
        print(f"Decision:    {result['trade_decision']} ({result['confidence']}%)")
        print(f"Traded:      {result['trade_placed']}")
        print("\n✅ Autonomous Brain fully self-directed!")
    asyncio.run(test())
