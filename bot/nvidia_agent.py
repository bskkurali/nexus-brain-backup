"""NEXUS Agent — routes to gemma_agent fallback chain"""
from bot.gemma_agent import agent_call, gemma_call

async def nvidia_call(prompt: str, api_key: str = "",
                      max_tokens: int = 500) -> str:
    return await agent_call(prompt, max_tokens)

async def grok_call(prompt: str, api_key: str = "",
                    max_tokens: int = 500) -> str:
    return await agent_call(prompt, max_tokens)

async def nvidia_get_news(api_key: str = "") -> dict:
    from datetime import datetime
    prompt = f"""Gold market {datetime.now().strftime("%B %Y")}.
JSON only: {{"sentiment":"BULLISH/BEARISH/NEUTRAL","score":0-100,"bias":"BUY/SELL/WAIT",
"key_driver":"max 80 chars","blackout":false,"trade_ok":true,"confidence":0-100}}"""
    raw = await agent_call(prompt, 200)
    try:
        import json
        d = json.loads(raw.replace("```json","").replace("```","").strip())
        return d
    except Exception:
        return {"sentiment":"NEUTRAL","bias":"WAIT","trade_ok":True,"confidence":50}

async def nvidia_market_scan(market_data: dict, api_key: str = "") -> dict:
    import json
    p=market_data.get("price",0); rsi=market_data.get("rsi",50)
    prompt=f"""XAUUSD expert. Price:${p:,.2f} RSI:{rsi} Session:{market_data.get("session","")}
JSON only: {{"signal":"BUY/SELL/WAIT","confidence":0-100,"reason":"80 chars",
"urgency":"HIGH/MEDIUM/LOW","call_claude":false}}"""
    raw = await agent_call(prompt, 200)
    try:
        return json.loads(raw.replace("```json","").replace("```","").strip())
    except Exception:
        return {"signal":"WAIT","confidence":0,"call_claude":False}

async def run_nvidia_research_cycle(market_data: dict) -> dict:
    import asyncio
    news, scan = await asyncio.gather(
        nvidia_get_news(), nvidia_market_scan(market_data),
        return_exceptions=True
    )
    if isinstance(news, Exception): news = {"sentiment":"NEUTRAL","bias":"WAIT","trade_ok":True}
    if isinstance(scan, Exception): scan = {"signal":"WAIT","confidence":0}
    return {"news":news,"scan":scan,
            "call_claude":False,
            "combined_signal":scan.get("signal","WAIT"),
            "combined_confidence":scan.get("confidence",0),
            "news_bias":news.get("bias","WAIT")}
