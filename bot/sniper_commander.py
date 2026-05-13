"""AiTrader Sniper Commander — grades setups and deploys teams"""
import asyncio, json
from loguru import logger

async def commander_analyze(market_data: dict, api_key: str):
    from bot.nvidia_agent import nvidia_call
    from dataclasses import dataclass, field
    from typing import Optional

    @dataclass
    class Signal:
        grade: str = "C"
        direction: str = "WAIT"
        entry_price: float = 0.0
        stop_loss: float = 0.0
        take_profit_1: float = 0.0
        take_profit_2: float = 0.0
        take_profit_3: float = 0.0
        lot_size: float = 0.01
        confidence: int = 0
        conditions_met: list = field(default_factory=list)
        conditions_failed: list = field(default_factory=list)
        deploy_team: bool = False
        reasoning: str = ""

    p=market_data.get("price",0); rsi=market_data.get("rsi",50)
    atr=market_data.get("atr",30); bull=market_data.get("bull_trend",False)
    sl=round(p-atr*2,2) if bull else round(p+atr*2,2)
    tp1=round(p+atr*2,2) if bull else round(p-atr*2,2)
    tp2=round(p+atr*4,2) if bull else round(p-atr*4,2)
    tp3=round(p+atr*6,2) if bull else round(p-atr*6,2)

    prompt=f"""SNIPER TRADER. Grade this XAUUSD setup.
Price:${p:,.2f} RSI:{rsi} ATR:{atr:.2f}
EMA9:{market_data.get("ema9",0):.2f} EMA21:{market_data.get("ema21",0):.2f}
Session:{market_data.get("session","")} Vol:{market_data.get("volume_ratio",0):.2f}x
Bull:{bull} Bear:{market_data.get("bear_trend",False)}

Grade A+/A/B/C. JSON only:
{{"grade":"A+/A/B/C","direction":"BUY/SELL/WAIT","confidence":0-100,
"deploy_team":true/false,"conditions_met":["list"],"conditions_failed":["list"],
"reasoning":"max 80 chars"}}
deploy_team=true only if grade A or A+ AND confidence>=75"""

    raw=await nvidia_call(prompt,api_key,300)
    sig=Signal(entry_price=p,stop_loss=sl,take_profit_1=tp1,take_profit_2=tp2,take_profit_3=tp3)
    try:
        d=json.loads(raw.replace("```json","").replace("```","").strip())
        sig.grade=d.get("grade","C"); sig.direction=d.get("direction","WAIT")
        sig.confidence=d.get("confidence",0); sig.deploy_team=d.get("deploy_team",False)
        sig.conditions_met=d.get("conditions_met",[]); sig.conditions_failed=d.get("conditions_failed",[])
        sig.reasoning=d.get("reasoning","")
        logger.info(f"Commander: {sig.grade} {sig.direction} conf={sig.confidence}%")
    except Exception as e:
        logger.debug(f"Commander parse: {e}")
    return sig

async def spawn_trade_team(signal, api_key: str):
    logger.info(f"Spawning team for {signal.direction} @ ${signal.entry_price:,.2f}")
    return signal

async def execute_with_team(team, api_key: str) -> dict:
    from bot.sniper_executor import place_paper_trade, get_live_price
    price = await get_live_price()
    if price < 2000: price = team.entry_price
    atr = 30
    sl = round(price - atr*2, 2) if team.direction=="BUY" else round(price + atr*2, 2)
    tp = round(price + atr*6, 2) if team.direction=="BUY" else round(price - atr*6, 2)
    result = await place_paper_trade(team.direction, price, sl, tp, atr, team.confidence)
    return result

async def run_sniper_commander_cycle() -> dict:
    from bot.sniper_executor import run_sniper_executor
    return await run_sniper_executor()
