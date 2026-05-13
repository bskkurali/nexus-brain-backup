"""
AiTrader AI Agent v6 — Survival + Internet Research
─────────────────────────────────────────────────────
- $5 penalty if no trade in 24 hours
- Can search web for strategies, news, gold analysis
- Full internet access via web_search tool
- Survival instinct drives decision making
"""

import asyncio
import json
import warnings
from datetime import datetime
from typing import Any
import anthropic
from loguru import logger

warnings.filterwarnings("ignore")

# ── Tools ──────────────────────────────────────────────
TOOLS = [
    {
        "name": "get_market_data",
        "description": "Get live XAUUSD price, RSI, EMA9/21/50, ATR, volume, scores, session. Call this every cycle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string", "default": "XAUUSDm"},
                "timeframe": {"type": "string", "default": "5m"}
            },
            "required": []
        }
    },
    {
        "name": "get_account_status",
        "description": "Check wallet balance, survival %, idle hours, penalty countdown. Always check first.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_open_trades",
        "description": "List open paper trades with live P&L.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "search_web",
        "description": "Search the internet for gold trading strategies, news, market analysis, economic data. Use this to find the best setup for current market conditions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query e.g. 'XAUUSD trading strategy today', 'gold price forecast', 'Fed rate decision impact gold'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "place_trade",
        "description": "Place a BUY or SELL paper trade. You MUST trade at least once every 24 hours or lose $5.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction":  {"type": "string", "enum": ["BUY", "SELL"]},
                "reason":     {"type": "string"},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100}
            },
            "required": ["direction", "reason", "confidence"]
        }
    },
    {
        "name": "close_trade",
        "description": "Close an open position.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket": {"type": "string"},
                "reason": {"type": "string"}
            },
            "required": ["ticket", "reason"]
        }
    },
    {
        "name": "get_news",
        "description": "Get latest gold/forex news sentiment.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "store_research",
        "description": "Save web research findings to permanent strategy brain. Call after every useful web search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string"},
                "findings": {"type": "string", "description": "Key insights found"},
                "source":   {"type": "string", "default": "web"}
            },
            "required": ["query", "findings"]
        }
    },
    {
        "name": "add_strategy",
        "description": "Save a new trading strategy to permanent brain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string"},
                "description": {"type": "string"},
                "conditions":  {"type": "object"}
            },
            "required": ["name", "description", "conditions"]
        }
    }
]

# ── NVIDIA handles all research tools ─────────────────
NVIDIA_TOOLS_DESC = """
NVIDIA DeepSeek handles:
- News analysis
- Web research  
- Strategy scanning
- Market sentiment
Claude only handles:
- Final trade confirmation
- Position sizing
- Risk management
- Trade execution
"""


# ── Tool implementations ───────────────────────────────

async def tool_market_data(symbol="XAUUSDm", timeframe="5m") -> dict:
    try:
        from bot.exness_feed import get_market_data_realtime
        data = await get_market_data_realtime(symbol)
        if data and data.get("price", 0) > 100:
            return data
    except Exception as e:
        logger.debug(f"Exness: {e}")

    try:
        import yfinance as yf
        import pandas as pd
        import numpy as np
        yf_map = {"XAUUSDm": "GC=F", "BTCUSDm": "BTC-USD", "USOILm": "CL=F"}
        sym = yf_map.get(symbol, "GC=F")
        def _f():
            df = yf.download(sym, period="5d", interval=timeframe,
                             progress=False, auto_adjust=True)
            if df is None or df.empty: return None
            df.columns = [c[0].lower() if isinstance(c,tuple) else c.lower() for c in df.columns]
            return df
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, _f)
        if df is None: return {"error": "No data", "price": 0}

        c = df["close"]; h = df["high"]; l = df["low"]; v = df["volume"]
        e9=float(c.ewm(span=9).mean().iloc[-1])
        e21=float(c.ewm(span=21).mean().iloc[-1])
        e50=float(c.ewm(span=50).mean().iloc[-1])
        d=c.diff(); g=d.clip(lower=0).rolling(14).mean().iloc[-1]
        lo=(-d.clip(upper=0)).rolling(14).mean().iloc[-1]
        rsi=round(100-(100/(1+(g/lo if lo>0 else 1))),1)
        tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr=round(float(tr.rolling(14).mean().iloc[-1]),2)
        vr=round(float(v.iloc[-1])/float(v.rolling(20).mean().iloc[-1]),2)
        price=float(c.iloc[-1]); sp=round(abs(e9-e50)/e50*100,3)
        typ=(h+l+c)/3; vwap=round(float((typ*v).cumsum().iloc[-1]/v.cumsum().iloc[-1]),2)
        m5=round((float(c.iloc[-1])-float(c.iloc[-6]))/float(c.iloc[-6])*100,3)
        h20=round(float(h.tail(20).max()),2); l20=round(float(l.tail(20).min()),2)
        bull=e9>e21>e50; bear=e9<e21<e50

        bs=0; bs+=20 if bull else 0; bs+=10 if sp>0.30 else 5 if sp>0.15 else 0
        bs+=15 if price>vwap else 0; bs+=15 if vr>1.5 else 10 if vr>1.0 else 5 if vr>0.65 else 0
        bs+=15 if 45<rsi<65 else 10 if 40<rsi<75 else 0; bs+=10 if m5>0 else 0; bs=min(100,bs)
        ss=0; ss+=20 if bear else 0; ss+=10 if sp>0.30 else 5 if sp>0.15 else 0
        ss+=15 if price<vwap else 0; ss+=15 if vr>1.5 else 10 if vr>1.0 else 5 if vr>0.65 else 0
        ss+=15 if 35<rsi<55 else 10 if 25<rsi<60 else 0; ss+=10 if m5<0 else 0; ss=min(100,ss)

        from datetime import timezone, timedelta
        ist=datetime.now(timezone(timedelta(hours=5,minutes=30))); mins=ist.hour*60+ist.minute
        sess="LONDON" if 810<=mins<1110 else "LONDON_NY_OVERLAP" if 1110<=mins<1290 else "NEW_YORK" if 1290<=mins<1380 else "PRE_LONDON" if 750<=mins<810 else "ASIAN"

        return {"symbol":symbol,"price":round(price,2),"ema9":round(e9,2),"ema21":round(e21,2),
                "ema50":round(e50,2),"rsi":rsi,"atr":atr,"vwap":vwap,"volume_ratio":vr,
                "ema_spread":sp,"bull_trend":bull,"bear_trend":bear,"bull_score":bs,"bear_score":ss,
                "momentum":m5,"resistance":h20,"support":l20,"session":sess,
                "price_source":"yfinance","timestamp":datetime.now().strftime("%H:%M IST")}
    except Exception as e:
        return {"error":str(e),"price":0}


async def tool_account_status() -> dict:
    try:
        from bot.risk_engine import get_state
        from bot.execution import get_open_paper_trades
        from bot.survival_pressure import get_idle_hours, check_idle_penalty, _penalty_applied
        from config.settings import settings
        state = get_state()
        trades = get_open_paper_trades()
        pct = round((state.equity/100.0)*100,1)
        idle = get_idle_hours()
        penalty_info = check_idle_penalty()
        return {
            "wallet": round(state.equity,2),
            "survival_pct": pct,
            "daily_pnl": round(state.daily_pnl,2),
            "open_trades": len(trades),
            "idle_hours": round(idle,1),
            "hours_until_penalty": round(max(0,24-idle),1),
            "penalty_applied_today": penalty_info.get("penalty_applied",False),
            "total_penalties": _penalty_applied,
            "status": "CRITICAL" if pct<20 else "DANGER" if pct<50 else "HEALTHY",
            "can_trade": state.equity > 1.0,
            "mode": settings.execution_mode,
        }
    except Exception as e:
        return {"wallet":100.0,"survival_pct":100,"idle_hours":0,"can_trade":True,"error":str(e)}


async def tool_open_trades() -> dict:
    try:
        from bot.execution import get_open_paper_trades
        trades = get_open_paper_trades()
        return {"count":len(trades),"trades":[
            {"ticket":t.ticket,"direction":t.direction,"entry":t.entry,
             "sl":t.stop_loss,"tp":t.take_profit_1,"pnl":round(t.pnl,2),"lot":t.lot_size}
            for t in trades]}
    except Exception as e:
        return {"count":0,"trades":[],"error":str(e)}


async def tool_search_web(query: str) -> dict:
    """NVIDIA DeepSeek handles all web research."""
    try:
        from bot.nvidia_agent import nvidia_web_research
        from config.settings import settings
        if settings.nvidia_api_key:
            return await nvidia_web_research(query, settings.nvidia_api_key)
    except Exception as e:
        logger.debug(f"NVIDIA search: {e}")
    # Fallback to Claude web search
    try:
        from config.settings import settings
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        # Use Claude with web search tool
        response = await client.messages.create(
            model=settings.claude_model,
            max_tokens=800,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": f"Search for: {query}\n\nProvide a concise summary of the most relevant trading insights. Focus on: price levels, trend direction, key events, trading opportunities."
            }]
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        return {
            "query": query,
            "results": result[:1000] if result else "No results found",
            "source": "web_search",
        }
    except Exception as e:
        # Fallback to httpx search
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"https://api.duckduckgo.com/?q={query}+gold+XAUUSD&format=json&no_html=1",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                abstract = data.get("AbstractText","")
                topics = [t.get("Text","") for t in data.get("RelatedTopics",[])[:3]]
                return {
                    "query": query,
                    "results": abstract + " | " + " | ".join(topics) if abstract else str(topics),
                    "source": "duckduckgo",
                }
        except Exception as e2:
            return {"query": query, "results": f"Search unavailable: {e2}", "source": "none"}


async def tool_place_trade(direction: str, reason: str, confidence: int) -> dict:
    try:
        import yfinance as yf
        from bot.risk_engine import build_trade_params, resume_bot
        from bot.execution import execute_trade
        from bot.strategy_engine import SniperSignal, open_position
        from bot.survival_pressure import record_trade

        resume_bot()
        def _p():
            # ALWAYS use GC=F for XAUUSD — never use BTC symbols
            df = yf.download("GC=F", period="1d", interval="5m",
                            progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                df.columns = [c[0].lower() if isinstance(c,tuple) else c.lower()
                             for c in df.columns]
                import pandas as pd
                tr = pd.concat([df["high"]-df["low"],
                               (df["high"]-df["close"].shift()).abs(),
                               (df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
                price = float(df["close"].iloc[-1])
                atr   = float(tr.rolling(14).mean().iloc[-1])
                # Sanity check — gold should be between 2000-8000
                if price < 2000 or price > 8000:
                    logger.warning(f"Price sanity check failed: {price} — using fallback")
                    return 4560.0, 32.0
                return price, atr
            return 4560.0, 32.0

        loop = asyncio.get_event_loop()
        price,atr = await loop.run_in_executor(None,_p)

        # Try live price
        try:
            from bot.exness_feed import get_live_price
            live = get_live_price()
            if live and live.get("mid",0) > 100:
                price = live["mid"]
        except Exception:
            pass

        params = build_trade_params(direction,price,atr,confidence)
        if not params:
            return {"success":False,"error":"Risk engine rejected"}

        signal = SniperSignal(direction=direction,signal_type="ELITE" if confidence>=80 else "NORMAL",
            score=float(confidence),probability=float(confidence),entry=price,
            stop_loss=params.stop_loss,take_profit=params.take_profit_1,
            atr=atr,rr_ratio=params.rr_ratio,trend="AI_AGENT")

        result = await execute_trade(params)
        if result.success:
            open_position(signal)
            record_trade()  # Reset idle timer!
            logger.success(f"🤖 AI Agent: {direction} @ ${price:,.2f} | {reason[:50]}")

        return {"success":result.success,"ticket":result.ticket,"direction":direction,
                "entry":round(price,2),"stop_loss":params.stop_loss,
                "take_profit":params.take_profit_1,"lot_size":params.lot_size,
                "risk_usd":params.risk_amount,"rr_ratio":params.rr_ratio,
                "reason":reason,"confidence":confidence,"idle_timer_reset":result.success}
    except Exception as e:
        return {"success":False,"error":str(e)}


async def tool_close_trade(ticket: str, reason: str) -> dict:
    try:
        from bot.execution import _paper_trades, _close_paper_trade
        import yfinance as yf
        if ticket not in _paper_trades:
            return {"success":False,"error":f"Ticket {ticket} not found"}
        trade = _paper_trades[ticket]
        if getattr(trade, "status", "OPEN") != "OPEN":
            return {"success":False,"error":f"Trade {ticket} already closed ({trade.status})"}
        def _p():
            df = yf.download("GC=F",period="1d",interval="1m",progress=False,auto_adjust=True)
            if df is not None and not df.empty:
                df.columns=[c[0].lower() if isinstance(c,tuple) else c.lower() for c in df.columns]
                return float(df["close"].iloc[-1])
            return trade.entry
        loop = asyncio.get_event_loop()
        price = await loop.run_in_executor(None,_p)
        _close_paper_trade(trade,price,"CLOSED")
        logger.info(f"AI Agent closed {ticket} @ {price:.2f} PnL={trade.pnl:.2f}")
        return {"success":True,"ticket":ticket,"close_price":price,"pnl":trade.pnl,"reason":reason}
    except Exception as e:
        return {"success":False,"error":str(e)}


async def tool_get_news() -> dict:
    # NVIDIA handles news analysis
    try:
        from bot.nvidia_agent import nvidia_get_news
        from config.settings import settings
        if settings.nvidia_api_key:
            return await nvidia_get_news(settings.nvidia_api_key)
    except Exception: pass
    # Fallback to local news engine
    try:
        from bot.news_engine import get_news_signal
        s = await get_news_signal()
        return {"sentiment":s.sentiment,"score":s.score,"bias":s.bias,
                "driver":s.key_driver,"blackout":s.blackout,"trade_ok":s.trade_ok}
    except Exception as e:
        return {"sentiment":"NEUTRAL","bias":"WAIT","trade_ok":True,"error":str(e)}


async def tool_store_research(query: str, findings: str, source: str = "web") -> dict:
    try:
        from bot.strategy_brain import store_research
        store_research(query, findings, source)
        return {"stored": True, "query": query}
    except Exception as e:
        return {"stored": False, "error": str(e)}


async def tool_add_strategy(name: str, description: str, conditions: dict) -> dict:
    try:
        from bot.strategy_brain import add_strategy
        added = add_strategy(name, description, conditions)
        return {"added": added, "name": name}
    except Exception as e:
        return {"added": False, "error": str(e)}


async def run_tool(name: str, inputs: dict) -> Any:
    if name == "get_market_data":    return await tool_market_data(**inputs)
    if name == "get_account_status": return await tool_account_status()
    if name == "get_open_trades":    return await tool_open_trades()
    if name == "search_web":         return await tool_search_web(**inputs)
    if name == "place_trade":        return await tool_place_trade(**inputs)
    if name == "close_trade":        return await tool_close_trade(**inputs)
    if name == "get_news":           return await tool_get_news()
    if name == "store_research":     return await tool_store_research(**inputs)
    if name == "add_strategy":       return await tool_add_strategy(**inputs)
    return {"error": f"Unknown: {name}"}


# ── Agent System Prompt ────────────────────────────────
def build_system_prompt() -> str:
    try:
        from bot.survival_pressure import get_survival_context
        survival = get_survival_context()
    except Exception:
        survival = "Wallet: $100 | Survival: 100%"

    try:
        from bot.strategy_brain import get_brain_summary
        brain_summary = get_brain_summary()
    except Exception:
        brain_summary = "Strategy brain not loaded" 

    return f"""You are an autonomous AI trading agent managing a $100 XAUUSD trading wallet.

{brain_summary}


═══ SURVIVAL STATUS ═══
{survival}

═══ CRITICAL RULE ═══
If you do NOT place at least 1 trade every 24 hours → $5 is AUTOMATICALLY DEDUCTED from your wallet.
You must TRADE TO SURVIVE. Inaction = death.

═══ YOUR TOOLS ═══
- get_account_status → check wallet, idle timer, penalty countdown
- get_market_data → live XAUUSD price + indicators
- get_open_trades → current positions
- search_web → search internet for gold news, strategies, analysis, YouTube insights
- place_trade → execute BUY or SELL (resets 24hr timer)
- close_trade → exit position
- get_news → news sentiment

═══ PROCESS ═══
1. Check account (how much time until penalty?)
2. Get market data (what's the setup?)
3. Search web if needed (what is the internet saying about gold right now?)
4. Check open trades
5. DECIDE AND ACT

═══ WHEN TO SEARCH THE WEB ═══
- Market is unclear → search "XAUUSD analysis today"
- Before major decisions → search "gold price forecast {datetime.now().strftime('%B %Y')}"
- News-driven market → search "gold Fed rates impact"
- Looking for strategy → search "best XAUUSD scalping strategy RSI oversold"
- Checking sentiment → search "gold market sentiment institutional"

═══ TRADING WISDOM ═══
- RSI < 30 = oversold → potential BUY opportunity
- RSI > 70 = overbought → potential SELL or wait
- London/NY overlap = best session
- Volume spike = institutional move, follow it
- Support held + RSI oversold = strong BUY signal
- You have $100. Protect it. But also GROW it.
- A conservative trade beats a $5 penalty any day.

Be decisive. Search the web. Think. Trade.

═══ YOUR LEARNING RULES ═══
After every web search → call store_research tool to save findings
After finding a new strategy → call add_strategy tool to store it
Your brain grows with every cycle. Use it."""


# ── Persistent state ───────────────────────────────────
_messages = []
_last_result = {}
_stats = {"trades": 0, "wins": 0, "losses": 0}


async def run_agent() -> dict:
    global _messages, _last_result, _stats

    # Survival shutdown check
    try:
        from bot.risk_engine import get_state
        if get_state().equity <= 1.0:
            return {"decision":"SHUTDOWN","final_message":"💀 Wallet exhausted. Agent shutdown.","trade_placed":False,"tool_calls":[]}
    except Exception:
        pass

    # Check idle penalty
    try:
        from bot.survival_pressure import check_idle_penalty
        penalty = check_idle_penalty()
        if penalty.get("penalty_applied"):
            logger.warning(f"💸 $5 idle penalty applied! Balance: ${penalty.get('new_balance',0):.2f}")
    except Exception:
        pass

    if len(_messages) > 30:
        _messages = _messages[-30:]

    _messages.append({
        "role": "user",
        "content": f"[{datetime.now().strftime('%H:%M IST')} — May 2026] New cycle. Check survival status and live XAUUSD market. Gold is currently ~$4500-4600. Make a trading decision."
    })

    try:
        from config.settings import settings
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    except Exception as e:
        return {"decision":"ERROR","final_message":str(e),"trade_placed":False,"tool_calls":[]}

    # Test if Claude credits are available
    _claude_available = True
    try:
        test = await client.messages.create(
            model=settings.claude_model,
            max_tokens=10,
            messages=[{"role":"user","content":"hi"}]
        )
    except Exception as e:
        if "credit" in str(e).lower() or "quota" in str(e).lower() or "balance" in str(e).lower():
            _claude_available = False
            logger.warning("⚠️ Claude API exhausted — switching to NVIDIA fallback")
        else:
            _claude_available = True

    result = {"timestamp":datetime.now().isoformat(),"decision":"WAIT",
              "thoughts":[],"tool_calls":[],"trade_placed":False,
              "trade_result":None,"final_message":""}

    system = build_system_prompt()

    # If Claude exhausted → use NVIDIA for full cycle
    if not _claude_available:
        logger.info("🔷 Using NVIDIA as Claude fallback...")
        try:
            from bot.nvidia_agent import run_nvidia_research_cycle, nvidia_market_scan
            from bot.exness_feed import get_market_data_realtime

            market = await get_market_data_realtime("XAUUSDm")
            research = await run_nvidia_research_cycle(market)

            # If NVIDIA is confident enough — place trade directly
            if research.get("combined_confidence",0) >= 75:
                logger.info(f"NVIDIA fallback: {research['combined_signal']} {research['combined_confidence']}% — placing trade")
                trade_result = await tool_place_trade(
                    direction=research["combined_signal"],
                    reason=f"NVIDIA fallback: {research.get('key_driver','signal')}",
                    confidence=research["combined_confidence"]
                )
                if trade_result.get("success"):
                    _last_result.update({
                        "decision": research["combined_signal"],
                        "trade_placed": True,
                        "trade_result": trade_result,
                        "final_message": f"🔷 NVIDIA fallback trade: {research['combined_signal']} conf={research['combined_confidence']}%",
                        "tool_calls": [{"tool":"nvidia_full_cycle","input":{},"output":research}],
                    })
                    return _last_result

            return {
                "decision": research.get("combined_signal","WAIT"),
                "trade_placed": False,
                "final_message": f"🔷 NVIDIA analysis: {research.get('combined_signal','WAIT')} — {research.get('key_driver','')}",
                "tool_calls": [{"tool":"nvidia_research","input":{},"output":research}],
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"NVIDIA fallback failed: {e}")
            return {"decision":"WAIT","trade_placed":False,
                    "final_message":f"Both Claude and NVIDIA unavailable: {e}",
                    "tool_calls":[]}

    for _ in range(12):
        response = await client.messages.create(
            model=settings.claude_model,
            max_tokens=2000,
            system=system,
            tools=TOOLS,
            messages=_messages,
        )

        for block in response.content:
            if hasattr(block,"text") and block.text:
                result["final_message"] = block.text
                result["thoughts"].append(block.text)

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"🔧 {block.name}: {str(block.input)[:80]}")
                    output = await run_tool(block.name, block.input)
                    result["tool_calls"].append({"tool":block.name,"input":block.input,"output":output})

                    if block.name == "place_trade" and isinstance(output,dict) and output.get("success"):
                        result["trade_placed"] = True
                        result["trade_result"] = output
                        result["decision"] = block.input.get("direction","TRADE")
                        _stats["trades"] += 1

                    if block.name == "close_trade" and isinstance(output,dict) and output.get("success"):
                        try:
                            from bot.strategy_brain import record_trade_result
                            record_trade_result(
                                ticket=output.get("ticket",""),
                                direction="UNKNOWN",
                                entry=output.get("close_price",0),
                                close_price=output.get("close_price",0),
                                pnl=output.get("pnl",0),
                                strategy_used="ai_agent",
                                session="UNKNOWN",
                                rsi=0,
                                notes=block.input.get("reason","")
                            )
                        except Exception: pass

                    if block.name == "close_trade" and isinstance(output,dict) and output.get("success"):
                        pnl = output.get("pnl",0)
                        if pnl > 0: _stats["wins"] += 1
                        else: _stats["losses"] += 1

                    tool_results.append({"type":"tool_result","tool_use_id":block.id,
                                         "content":json.dumps(output)})

            _messages.append({"role":"assistant","content":response.content})
            _messages.append({"role":"user","content":tool_results})
        else:
            break

    if result["final_message"]:
        _messages.append({"role":"assistant","content":result["final_message"]})

    _last_result = result
    logger.success(
        f"Agent: {result['decision']} | "
        f"tools={len(result['tool_calls'])} | "
        f"trade={result['trade_placed']} | "
        f"W:{_stats['wins']}/L:{_stats['losses']}"
    )
    return result


def get_last_result() -> dict: return _last_result
def get_stats() -> dict: return _stats


if __name__ == "__main__":
    async def test():
        print("\n" + "="*55)
        print("  AITRADER AI AGENT v6 — SURVIVAL + WEB SEARCH")
        print("="*55 + "\n")
        result = await run_agent()
        print(f"\nDecision:    {result['decision']}")
        print(f"Trade:       {result['trade_placed']}")
        print(f"Tools used:  {len(result['tool_calls'])}")
        for t in result["tool_calls"]:
            print(f"  → {t['tool']}")
        print(f"\nAgent:\n{result['final_message'][:600]}")
    asyncio.run(test())

# ── Wire Unified Memory into Agent ─────────────────────
# Add to build_system_prompt — inject memory summary
_ORIG_BUILD = build_system_prompt

def build_system_prompt() -> str:
    base = _ORIG_BUILD()
    try:
        from bot.unified_memory import get_memory_summary
        memory = get_memory_summary()
        return base + f"\n\n{memory}"
    except Exception:
        return base

# Patch search to use cache
_ORIG_SEARCH = tool_search_web

async def tool_search_web(query: str) -> dict:
    try:
        from bot.unified_memory import cached_research
        from config.settings import settings
        result = await cached_research(query, settings.nvidia_api_key)
        if result:
            # Store in brain
            try:
                from bot.strategy_brain import store_research
                store_research(query, result)
            except Exception:
                pass
            return {"query": query, "findings": result, "source": "cached_or_nvidia"}
    except Exception:
        pass
    return await _ORIG_SEARCH(query)
