"""
AiTrader Master Brain
──────────────────────
The central intelligence that:
  1. Stores ALL thinking permanently
  2. Spawns specialized sub-agents when needed
  3. Sub-agents can spawn more agents
  4. Self-improving — learns from every thought
  5. Builds its own knowledge base over time
  6. Runs continuously in background

Architecture:
  MasterBrain
  ├── ThinkingEngine (continuous background thoughts)
  ├── AgentFactory (spawns specialized agents)
  │   ├── NewsAgent (monitors macro)
  │   ├── TechnicalAgent (chart analysis)
  │   ├── RiskAgent (protects wallet)
  │   ├── StrategyAgent (finds new setups)
  │   ├── JournalAgent (writes trading diary)
  │   └── [any new agent it creates]
  └── BrainStorage (all thoughts + decisions)
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from loguru import logger

BRAIN_DIR   = "data/brain"
AGENTS_FILE = "data/brain/agents.json"
THOUGHTS_FILE = "data/brain/thoughts.json"
MEMORY_FILE = "data/brain/memory.json"
GOALS_FILE  = "data/brain/goals.json"


# ── Data structures ────────────────────────────────────

@dataclass
class Thought:
    id:         str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent:      str = "master"
    type:       str = "general"
    content:    str = ""
    icon:       str = "💭"
    timestamp:  str = field(default_factory=lambda: datetime.now().isoformat())
    session:    str = ""
    importance: int = 5  # 1-10
    acted_on:   bool = False
    spawned_agent: Optional[str] = None


@dataclass
class Agent:
    id:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name:        str = ""
    role:        str = ""
    purpose:     str = ""
    created_by:  str = "master"
    created_at:  str = field(default_factory=lambda: datetime.now().isoformat())
    active:      bool = True
    runs:        int = 0
    last_run:    str = ""
    insights:    list = field(default_factory=list)
    prompt:      str = ""


# ── Storage ────────────────────────────────────────────

class BrainStorage:
    def __init__(self):
        os.makedirs(BRAIN_DIR, exist_ok=True)
        self.thoughts: list[Thought] = []
        self.agents:   dict[str, Agent] = {}
        self.memory:   dict = {}
        self.goals:    list = []
        self._load()

    def _load(self):
        # Thoughts
        if os.path.exists(THOUGHTS_FILE):
            try:
                data = json.load(open(THOUGHTS_FILE))
                self.thoughts = [Thought(**t) for t in data]
            except Exception:
                self.thoughts = []

        # Agents
        if os.path.exists(AGENTS_FILE):
            try:
                data = json.load(open(AGENTS_FILE))
                self.agents = {k: Agent(**v) for k, v in data.items()}
            except Exception:
                self.agents = {}

        # Memory
        if os.path.exists(MEMORY_FILE):
            try:
                self.memory = json.load(open(MEMORY_FILE))
            except Exception:
                self.memory = {}

        # Goals
        if os.path.exists(GOALS_FILE):
            try:
                self.goals = json.load(open(GOALS_FILE))
            except Exception:
                self.goals = self._default_goals()
        else:
            self.goals = self._default_goals()
            self._save_goals()

        logger.info(
            f"Brain loaded: {len(self.thoughts)} thoughts, "
            f"{len(self.agents)} agents, "
            f"{len(self.goals)} goals"
        )

    def _default_goals(self) -> list:
        return [
            {"goal": "Grow $100 wallet to $500", "priority": 1, "progress": 0},
            {"goal": "Achieve 70%+ win rate",    "priority": 2, "progress": 0},
            {"goal": "Master London session",     "priority": 3, "progress": 0},
            {"goal": "Never lose more than $5/day","priority":4, "progress": 0},
            {"goal": "Build 10+ strategy patterns","priority":5,"progress": 0},
        ]

    def save_thought(self, thought: Thought):
        self.thoughts.append(thought)
        self.thoughts = self.thoughts[-500:]  # Keep last 500
        data = [asdict(t) for t in self.thoughts]
        json.dump(data, open(THOUGHTS_FILE, "w"), indent=2)

    def save_agent(self, agent: Agent):
        self.agents[agent.id] = agent
        data = {k: asdict(v) for k, v in self.agents.items()}
        json.dump(data, open(AGENTS_FILE, "w"), indent=2)

    def update_memory(self, key: str, value):
        self.memory[key] = value
        json.dump(self.memory, open(MEMORY_FILE, "w"), indent=2)

    def _save_goals(self):
        json.dump(self.goals, open(GOALS_FILE, "w"), indent=2)

    def get_recent_thoughts(self, n: int = 20, agent: str = None) -> list:
        thoughts = self.thoughts
        if agent:
            thoughts = [t for t in thoughts if t.agent == agent]
        return thoughts[-n:]

    def get_active_agents(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.active]

    def think(self, content: str, agent: str = "master",
              type_: str = "general", icon: str = "💭",
              importance: int = 5) -> Thought:
        sess = _get_session()
        t = Thought(agent=agent, type=type_, content=content,
                    icon=icon, session=sess, importance=importance)
        self.save_thought(t)
        logger.info(f"🧠 [{agent}] {icon} {content[:80]}")
        return t


def _get_session() -> str:
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    m = ist.hour * 60 + ist.minute
    if 810 <= m < 1110:    return "LONDON"
    elif 1110 <= m < 1290: return "LONDON_NY_OVERLAP"
    elif 1290 <= m < 1380: return "NEW_YORK"
    elif 750 <= m < 810:   return "PRE_LONDON"
    return "ASIAN"


# ── Global brain instance ──────────────────────────────
_brain: Optional[BrainStorage] = None


def get_brain() -> BrainStorage:
    global _brain
    if _brain is None:
        _brain = BrainStorage()
    return _brain


# ── Agent Factory ──────────────────────────────────────

class AgentFactory:
    """Creates and manages specialized sub-agents."""

    BUILT_IN_AGENTS = {
        "news_agent": Agent(
            id="news_agent", name="NewsAgent",
            role="macro_news", active=True,
            purpose="Monitor macro events affecting gold — Fed, USD, geopolitics",
            prompt="You are a macro news analyst. Focus on gold price drivers: Fed rates, USD strength, geopolitical events, inflation data.",
        ),
        "technical_agent": Agent(
            id="technical_agent", name="TechnicalAgent",
            role="technical_analysis", active=True,
            purpose="Analyze charts, identify patterns, set price alerts",
            prompt="You are a technical analyst specializing in XAUUSD. Identify support/resistance, patterns, and optimal entry zones.",
        ),
        "risk_agent": Agent(
            id="risk_agent", name="RiskAgent",
            role="risk_management", active=True,
            purpose="Protect the $100 wallet, manage drawdown, size positions",
            prompt="You are a risk manager. Your only job is protecting the trading capital. Be conservative. Never let one trade blow more than 2%.",
        ),
        "strategy_agent": Agent(
            id="strategy_agent", name="StrategyAgent",
            role="strategy_research", active=True,
            purpose="Research and discover new trading strategies",
            prompt="You are a strategy researcher. Find patterns, test ideas, and add new strategies to the brain.",
        ),
        "journal_agent": Agent(
            id="journal_agent", name="JournalAgent",
            role="journaling", active=True,
            purpose="Write trading diary, track progress toward goals",
            prompt="You are a trading coach writing a journal. Be honest about mistakes, celebrate wins, track progress toward goals.",
        ),
    }

    @classmethod
    def initialize(cls):
        """Load built-in agents into brain."""
        brain = get_brain()
        for aid, agent in cls.BUILT_IN_AGENTS.items():
            if aid not in brain.agents:
                brain.save_agent(agent)
                logger.info(f"Agent initialized: {agent.name}")

    @classmethod
    async def spawn_agent(cls, name: str, purpose: str,
                           prompt: str, created_by: str = "master") -> Agent:
        """Master brain spawns a new specialized agent."""
        brain = get_brain()
        agent = Agent(
            name=name, role=name.lower().replace(" ", "_"),
            purpose=purpose, prompt=prompt, created_by=created_by
        )
        brain.save_agent(agent)
        brain.think(
            content=f"Spawned new agent: {name} — {purpose}",
            agent="master", type_="agent_spawn", icon="🤖",
            importance=8
        )
        logger.success(f"New agent spawned: {name} by {created_by}")
        return agent

    @classmethod
    async def run_agent(cls, agent: Agent, task: str,
                        api_key: str, market_data: dict = None) -> str:
        """Run a specific agent on a task."""
        from bot.nvidia_agent import nvidia_call
        brain = get_brain()

        mkt_ctx = ""
        if market_data:
            mkt_ctx = f"""
Current market: ${market_data.get('price',0):,.2f} 
RSI: {market_data.get('rsi',50)} 
Session: {market_data.get('session','?')}
Trend: {'BULLISH' if market_data.get('bull_trend') else 'BEARISH' if market_data.get('bear_trend') else 'SIDEWAYS'}"""

        # Get recent thoughts from this agent for context
        recent = brain.get_recent_thoughts(5, agent.id)
        ctx = "\n".join([f"- {t.content[:80]}" for t in recent]) if recent else "No previous thoughts"

        prompt = f"""{agent.prompt}

Your previous thoughts:
{ctx}
{mkt_ctx}

Task: {task}

Respond with your analysis. Be concise (max 150 words). 
End with ONE actionable insight starting with "→ ACTION:" """

        result = await nvidia_call(prompt, api_key, max_tokens=250)

        if result:
            # Store thought
            thought = brain.think(
                content=result,
                agent=agent.id,
                type_=agent.role,
                icon=cls._get_icon(agent.role),
                importance=7
            )
            # Update agent stats
            agent.runs += 1
            agent.last_run = datetime.now().isoformat()
            if result:
                agent.insights.append(result[:100])
                agent.insights = agent.insights[-20:]
            brain.save_agent(agent)

        return result or ""

    @staticmethod
    def _get_icon(role: str) -> str:
        icons = {
            "macro_news": "📰",
            "technical_analysis": "📈",
            "risk_management": "🛡️",
            "strategy_research": "🔬",
            "journaling": "📓",
            "agent_spawn": "🤖",
        }
        return icons.get(role, "💭")


# ── Master Brain Thinking Engine ───────────────────────

class MasterBrain:
    """
    The central AI that thinks continuously.
    Coordinates all agents and makes meta-decisions.
    """

    def __init__(self):
        self.storage = get_brain()
        self.running = False
        AgentFactory.initialize()

    async def think_about_goals(self, api_key: str):
        """Reflect on current goals and progress."""
        from bot.nvidia_agent import nvidia_call
        from bot.strategy_brain import get_brain as get_strat_brain

        strat = get_strat_brain()
        trades = strat.get("trade_history", [])
        wins = sum(1 for t in trades if t.get("won"))
        total = len(trades)
        wr = round(wins/total*100, 1) if total > 0 else 0

        try:
            from bot.risk_engine import get_state
            equity = get_state().equity
        except Exception:
            equity = 100.0

        goals_str = json.dumps(self.storage.goals, indent=2)

        prompt = f"""You are an AI trading agent reflecting on your goals. May 2026.

Current wallet: ${equity:.2f} / $100 starting
Win rate: {wr}% ({wins}W/{total-wins}L from {total} trades)
Session: {_get_session()}

Your goals:
{goals_str}

Reflect:
1. How am I progressing toward each goal?
2. What's blocking me?
3. What should I focus on next?
4. Any goal I should add or modify?

Be honest. Max 150 words."""

        response = await nvidia_call(prompt, api_key, max_tokens=250)
        if response:
            self.storage.think(
                content=response, agent="master",
                type_="goal_reflection", icon="🎯", importance=9
            )
            # Update goal progress
            if equity > 100:
                self.storage.goals[0]["progress"] = round((equity-100)/400*100, 1)
            if wr > 0:
                self.storage.goals[1]["progress"] = round(wr/70*100, 1)
            self.storage._save_goals()
            logger.info(f"🎯 Goal reflection: {response[:80]}")

    async def think_about_market(self, api_key: str, market_data: dict):
        """Deep market contemplation."""
        from bot.nvidia_agent import nvidia_call

        # Get recent thoughts for context
        recent = self.storage.get_recent_thoughts(10)
        ctx = "\n".join([f"[{t.agent}] {t.content[:60]}" for t in recent])

        price = market_data.get("price", 0)
        rsi   = market_data.get("rsi", 50)
        sess  = market_data.get("session", "")

        prompt = f"""You are an AI trader in deep thought. May 2026.
Gold: ${price:,.2f} | RSI: {rsi} | Session: {sess}

Recent thoughts from your agents:
{ctx}

Think deeply:
- What is gold trying to do right now?
- What setup is forming or about to form?
- What would you tell yourself to watch for?
- Any conflict between what agents are saying?

This is your private inner monologue. Be analytical. Max 120 words."""

        response = await nvidia_call(prompt, api_key, max_tokens=200)
        if response:
            self.storage.think(
                content=response, agent="master",
                type_="market_contemplation", icon="🌊", importance=7
            )

    async def consider_spawning_agent(self, api_key: str) -> Optional[Agent]:
        """
        Master decides if it needs a new specialized agent.
        This is the self-organizing part.
        """
        from bot.nvidia_agent import nvidia_call

        current_agents = [a.name for a in self.storage.get_active_agents()]
        recent_thoughts = self.storage.get_recent_thoughts(20)
        gaps = "\n".join([f"- {t.content[:60]}" for t in recent_thoughts[-5:]])

        prompt = f"""You are an AI meta-agent deciding if you need to spawn a new specialized agent.

Current agents: {current_agents}

Recent thoughts showing potential gaps:
{gaps}

Should you spawn a new agent? Consider:
- Is there a knowledge gap no current agent covers?
- Would a specialized agent improve trading performance?
- Common needs: sentiment analysis, options flow, correlation tracker, etc.

Respond with ONLY JSON:
{{
  "spawn": true or false,
  "name": "AgentName",
  "purpose": "what it does in 60 chars",
  "prompt": "system prompt for this agent in 100 chars",
  "reason": "why this agent is needed"
}}

spawn=true ONLY if genuinely valuable. Don't spawn duplicates."""

        raw = await nvidia_call(prompt, api_key, max_tokens=200)
        try:
            clean = raw.replace("```json","").replace("```","").strip()
            decision = json.loads(clean)
            if decision.get("spawn"):
                agent = await AgentFactory.spawn_agent(
                    name=decision["name"],
                    purpose=decision["purpose"],
                    prompt=decision["prompt"],
                    created_by="master"
                )
                self.storage.think(
                    content=f"Decided to spawn {decision['name']}: {decision['reason']}",
                    agent="master", type_="meta_decision",
                    icon="🔮", importance=10
                )
                return agent
        except Exception:
            pass
        return None

    async def run_all_agents(self, api_key: str, market_data: dict):
        """Run all active agents in parallel."""
        active = self.storage.get_active_agents()

        tasks = []
        for agent in active:
            task_map = {
                "macro_news":         "Analyze current macro conditions affecting gold",
                "technical_analysis": f"Analyze XAUUSD at ${market_data.get('price',0):,.2f}",
                "risk_management":    f"Review risk with ${market_data.get('price',0):,.2f} and current wallet",
                "strategy_research":  "Find the best strategy for current market conditions",
                "journaling":         "Write a journal entry about today's trading activity",
            }
            task = task_map.get(agent.role, f"Analyze market and provide insight for {agent.role}")
            tasks.append(AgentFactory.run_agent(agent, task, api_key, market_data))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if isinstance(r, str) and r]
        logger.success(f"All agents ran: {len(valid)}/{len(active)} produced insights")
        return valid

    async def background_loop(self, api_key: str):
        """
        Continuous background thinking loop.
        Runs every 10 minutes during off-hours.
        Runs every 30 minutes during trading hours.
        """
        self.running = True
        cycle = 0

        logger.success("🧠 Master Brain background thinking started")
        self.storage.think(
            "Master Brain initialized. Beginning autonomous thinking.",
            agent="master", type_="startup", icon="🚀", importance=10
        )

        while self.running:
            try:
                cycle += 1
                sess = _get_session()
                is_trading = sess in ["LONDON", "LONDON_NY_OVERLAP", "NEW_YORK"]

                # Get market data
                try:
                    from bot.exness_feed import get_market_data_realtime
                    market = await get_market_data_realtime("XAUUSDm")
                    if not market or market.get("price", 0) < 2000:
                        raise ValueError("Bad price")
                except Exception:
                    market = {"price": 0, "rsi": 50, "session": sess,
                             "bull_trend": False, "bear_trend": False}

                logger.info(f"🧠 Brain cycle #{cycle} | {sess} | trading={is_trading}")

                # Always: run all agents
                await self.run_all_agents(api_key, market)

                # Always: deep market contemplation
                await self.think_about_market(api_key, market)

                # Every 5 cycles: goal reflection
                if cycle % 5 == 0:
                    await self.think_about_goals(api_key)

                # Every 10 cycles: consider spawning new agent
                if cycle % 10 == 0:
                    new_agent = await self.consider_spawning_agent(api_key)
                    if new_agent:
                        logger.success(f"🤖 New agent spawned: {new_agent.name}")

                # Sleep based on session
                wait = 600 if is_trading else 900  # 10 or 15 min
                logger.info(f"🧠 Brain sleeping {wait//60}min until next cycle")
                await asyncio.sleep(wait)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Brain cycle error: {e}")
                await asyncio.sleep(300)

    def stop(self):
        self.running = False


# ── Global instance ────────────────────────────────────
_master: Optional[MasterBrain] = None


def get_master_brain() -> MasterBrain:
    global _master
    if _master is None:
        _master = MasterBrain()
    return _master


async def start_brain(api_key: str):
    """Start the master brain background loop."""
    brain = get_master_brain()
    await brain.background_loop(api_key)


if __name__ == "__main__":
    async def test():
        print("\n" + "="*60)
        print("  AITRADER MASTER BRAIN — SELF-ORGANIZING AI")
        print("="*60 + "\n")

        from config.settings import settings
        api_key = settings.nvidia_api_key

        brain = get_master_brain()
        market = {
            "price": 4645.60, "rsi": 37.4, "session": "LONDON",
            "bull_trend": True, "bear_trend": False,
            "support": 4620.0, "resistance": 4670.0
        }

        print("1. Running all agents...")
        results = await brain.run_all_agents(api_key, market)
        print(f"   {len(results)} agent insights generated")

        print("\n2. Deep market contemplation...")
        await brain.think_about_market(api_key, market)

        print("\n3. Goal reflection...")
        await brain.think_about_goals(api_key)

        print("\n4. Checking if new agent needed...")
        new_agent = await brain.consider_spawning_agent(api_key)
        if new_agent:
            print(f"   Spawned: {new_agent.name} — {new_agent.purpose}")
        else:
            print("   No new agent needed")

        print("\n5. Recent thoughts:")
        thoughts = brain.storage.get_recent_thoughts(5)
        for t in thoughts[-5:]:
            print(f"   {t.icon} [{t.agent}] {t.content[:80]}")

        print(f"\n6. Active agents: {len(brain.storage.get_active_agents())}")
        for a in brain.storage.get_active_agents():
            print(f"   🤖 {a.name} — {a.purpose[:60]}")

        print(f"\n7. Goals:")
        for g in brain.storage.goals:
            print(f"   🎯 {g['goal']} — {g['progress']}% complete")

        print("\n✅ Master Brain fully operational!")

    asyncio.run(test())
