"""
AiTrader Internet Trader
─────────────────────────
Works exactly like a human trader:
1. Fetch live price + calculate ALL indicators (free)
2. Read FXStreet analysis (free)
3. Check ForexFactory calendar (free)
4. Read Investing.com signals (free)
5. Read Kitco news (free)
6. Send everything to Claude in ONE call
7. Claude decides BUY/SELL/WAIT
8. Execute trade

Cost per trade decision: $0.012 (Claude only)
Everything else: FREE
"""

import asyncio
import json
import re
import os
from datetime import datetime, timezone, timedelta
from loguru import logger


# ══════════════════════════════════════════════════════
# SECTION 1: INDICATORS (calculated locally — FREE)
# ══════════════════════════════════════════════════════

async def get_full_market_analysis() -> dict:
    """
    Fetch OHLCV data and calculate ALL indicators a
    professional trader uses. No API needed.
    """
    import yfinance as yf
    import pandas as pd
    import numpy as np

    def _calc():
        # Fetch 5 days of 5min bars
        df = yf.download("GC=F", period="5d", interval="5m",
                        progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None

        df.columns = [c[0].lower() if isinstance(c,tuple) else c.lower()
                     for c in df.columns]
        df = df.dropna()

        cl = df['close']; hi = df['high']
        lo = df['low'];   vl = df['volume']
        price = float(cl.iloc[-1])

        # ── EMAs ──────────────────────────────────
        e9   = float(cl.ewm(span=9,  adjust=False).mean().iloc[-1])
        e21  = float(cl.ewm(span=21, adjust=False).mean().iloc[-1])
        e50  = float(cl.ewm(span=50, adjust=False).mean().iloc[-1])
        e200 = float(cl.ewm(span=200,adjust=False).mean().iloc[-1])

        # ── RSI ───────────────────────────────────
        d = cl.diff()
        g = d.clip(lower=0).ewm(14,adjust=False).mean()
        l = (-d.clip(upper=0)).ewm(14,adjust=False).mean()
        rsi = round(float(100-(100/(1+(g/(l+1e-9)).iloc[-1]))),1)

        # ── ATR ───────────────────────────────────
        tr = pd.concat([hi-lo,(hi-cl.shift()).abs(),
                       (lo-cl.shift()).abs()],axis=1).max(axis=1)
        atr = round(float(tr.ewm(14,adjust=False).mean().iloc[-1]),2)

        # ── MACD ──────────────────────────────────
        macd_line   = cl.ewm(12,adjust=False).mean() - cl.ewm(26,adjust=False).mean()
        signal_line = macd_line.ewm(9,adjust=False).mean()
        macd_val    = round(float(macd_line.iloc[-1]),2)
        macd_sig    = round(float(signal_line.iloc[-1]),2)
        macd_hist   = round(macd_val - macd_sig, 2)
        macd_cross  = ("BULLISH_CROSS" if macd_val>macd_sig and float(macd_line.iloc[-2])<=float(signal_line.iloc[-2])
                      else "BEARISH_CROSS" if macd_val<macd_sig and float(macd_line.iloc[-2])>=float(signal_line.iloc[-2])
                      else "BULL" if macd_val>macd_sig else "BEAR")

        # ── ADX (trend strength) ───────────────────
        tr_s   = tr.ewm(14,adjust=False).mean()
        up_move   = hi.diff()
        down_move = -lo.diff()
        plus_dm   = up_move.where((up_move>down_move)&(up_move>0),0)
        minus_dm  = down_move.where((down_move>up_move)&(down_move>0),0)
        plus_di   = 100*(plus_dm.ewm(14,adjust=False).mean()/tr_s)
        minus_di  = 100*(minus_dm.ewm(14,adjust=False).mean()/tr_s)
        dx        = 100*(abs(plus_di-minus_di)/(plus_di+minus_di+1e-9))
        adx       = round(float(dx.ewm(14,adjust=False).mean().iloc[-1]),1)
        adx_trend = ("STRONG TREND" if adx>25 else
                    "WEAK TREND" if adx>15 else "RANGING/SIDEWAYS")
        di_bias   = "BULLISH" if float(plus_di.iloc[-1])>float(minus_di.iloc[-1]) else "BEARISH"

        # ── Stochastic RSI ─────────────────────────
        rsi_s    = 100-(100/(1+(d.clip(lower=0).ewm(14,adjust=False).mean()/
                               (-d.clip(upper=0)).ewm(14,adjust=False).mean()+1e-9)))
        stoch_min = rsi_s.rolling(14).min()
        stoch_max = rsi_s.rolling(14).max()
        stoch_rsi = round(float(((rsi_s-stoch_min)/(stoch_max-stoch_min+1e-9)).iloc[-1]*100),1)
        stoch_sig = "OVERBOUGHT" if stoch_rsi>80 else "OVERSOLD" if stoch_rsi<20 else "NEUTRAL"

        # ── Fibonacci levels ───────────────────────
        # Use 20-period high/low for swing
        high_20 = float(hi.tail(96).max())  # ~1 day on 5m
        low_20  = float(lo.tail(96).min())
        diff    = high_20 - low_20
        fib = {
            "0.0":   round(low_20, 2),
            "23.6":  round(low_20 + diff*0.236, 2),
            "38.2":  round(low_20 + diff*0.382, 2),
            "50.0":  round(low_20 + diff*0.500, 2),
            "61.8":  round(low_20 + diff*0.618, 2),
            "78.6":  round(low_20 + diff*0.786, 2),
            "100.0": round(high_20, 2),
        }
        # Find nearest fib level
        nearest_fib = min(fib.items(), key=lambda x: abs(x[1]-price))

        # ── Support & Resistance ───────────────────
        # Pivot points (standard)
        prev_high = float(hi.iloc[-48:-1].max())
        prev_low  = float(lo.iloc[-48:-1].min())
        prev_close= float(cl.iloc[-2])
        pivot = round((prev_high+prev_low+prev_close)/3, 2)
        r1 = round(2*pivot - prev_low, 2)
        r2 = round(pivot + (prev_high - prev_low), 2)
        s1 = round(2*pivot - prev_high, 2)
        s2 = round(pivot - (prev_high - prev_low), 2)

        # ── Volume ────────────────────────────────
        vol_avg = float(vl.rolling(20).mean().iloc[-1])
        vol_now = float(vl.iloc[-1])
        vol_ratio = round(vol_now/max(vol_avg,1), 2)

        # ── VWAP ─────────────────────────────────
        tp = (hi+lo+cl)/3
        vwap = round(float((tp*vl).cumsum().iloc[-1]/vl.cumsum().iloc[-1]), 2)

        # ── Trend ─────────────────────────────────
        bull = e9>e21>e50
        bear = e9<e21<e50

        # ── Bollinger Bands ───────────────────────
        bb_mid = round(float(cl.rolling(20).mean().iloc[-1]),2)
        bb_std = float(cl.rolling(20).std().iloc[-1])
        bb_up  = round(bb_mid + 2*bb_std, 2)
        bb_lo  = round(bb_mid - 2*bb_std, 2)
        bb_pos = ("ABOVE_UPPER" if price>bb_up else
                 "BELOW_LOWER" if price<bb_lo else "INSIDE")

        # ── Session ───────────────────────────────
        ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        m = ist.hour*60 + ist.minute
        session = ("LONDON" if 810<=m<1110
                  else "LONDON_NY_OVERLAP" if 1110<=m<1290
                  else "NEW_YORK" if 1290<=m<1380
                  else "ASIAN")
        best_session = session in ["LONDON","LONDON_NY_OVERLAP","NEW_YORK"]

        return {
            "price":      round(price,2),
            "rsi":        rsi,
            "atr":        atr,
            "ema9":       round(e9,2),
            "ema21":      round(e21,2),
            "ema50":      round(e50,2),
            "ema200":     round(e200,2),
            "macd":       macd_val,
            "macd_signal":macd_sig,
            "macd_hist":  macd_hist,
            "macd_cross": macd_cross,
            "adx":        adx,
            "adx_trend":  adx_trend,
            "di_bias":    di_bias,
            "stoch_rsi":  stoch_rsi,
            "stoch_sig":  stoch_sig,
            "fib":        fib,
            "nearest_fib":f"{nearest_fib[0]}% @ ${nearest_fib[1]}",
            "pivot":      pivot,
            "r1":r1,"r2":r2,"s1":s1,"s2":s2,
            "vol_ratio":  vol_ratio,
            "vwap":       vwap,
            "bb_upper":   bb_up,
            "bb_lower":   bb_lo,
            "bb_mid":     bb_mid,
            "bb_position":bb_pos,
            "bull_trend": bull,
            "bear_trend": bear,
            "session":    session,
            "best_session":best_session,
        }

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _calc) or {}
    except Exception as e:
        logger.error(f"Market data error: {e}")
        return {}


# ══════════════════════════════════════════════════════
# SECTION 2: INTERNET RESEARCH (all free)
# ══════════════════════════════════════════════════════

async def fetch_fxstreet() -> str:
    """Read FXStreet gold analysis — free."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }) as c:
            r = await c.get("https://www.fxstreet.com/markets/commodities/metals/gold")
            if r.status_code == 200:
                text = re.sub(r'<[^>]+>', ' ', r.text)
                text = re.sub(r'\s+', ' ', text)
                # Extract relevant sections
                idx = text.find("Gold")
                if idx > 0:
                    snippet = text[idx:idx+1500]
                    return snippet
    except Exception:
        pass
    return ""


async def fetch_forexfactory() -> list:
    """Get high-impact economic events — free."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8, headers={
            "User-Agent": "Mozilla/5.0"
        }) as c:
            r = await c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            if r.status_code == 200:
                events = r.json()
                high_impact = []
                for e in events:
                    if e.get("impact") in ["High","Medium"]:
                        if any(x in e.get("currency","") for x in ["USD","XAU","EUR"]):
                            high_impact.append({
                                "title":    e.get("title",""),
                                "date":     e.get("date",""),
                                "impact":   e.get("impact",""),
                                "forecast": e.get("forecast",""),
                                "previous": e.get("previous",""),
                            })
                return high_impact[:5]
    except Exception:
        pass
    return []


async def fetch_kitco_news() -> list:
    """Read Kitco gold news — free."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8, headers={
            "User-Agent": "Mozilla/5.0"
        }) as c:
            r = await c.get("https://www.kitco.com/rss/")
            if r.status_code == 200:
                titles = re.findall(r'<title>(.*?)</title>', r.text)
                return [t for t in titles[1:6] if len(t) > 10]
    except Exception:
        pass
    return []


async def fetch_investing_signals() -> str:
    """Get Investing.com gold technical signals — free."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }) as c:
            r = await c.get("https://www.investing.com/currencies/xau-usd-technical")
            if r.status_code == 200:
                text = re.sub(r'<[^>]+>', ' ', r.text)
                text = re.sub(r'\s+', ' ', text)
                # Find summary section
                for keyword in ["Strong Buy", "Strong Sell", "Buy", "Sell", "Neutral"]:
                    idx = text.find(keyword)
                    if idx > 0:
                        return text[max(0,idx-100):idx+300]
    except Exception:
        pass
    return ""


# ══════════════════════════════════════════════════════
# SECTION 3: CLAUDE DECISION (one call — $0.012)
# ══════════════════════════════════════════════════════

async def claude_trade_decision(market: dict, internet: dict) -> dict:
    """
    Send ALL research to Claude in ONE call.
    Claude reads everything and decides like a human trader.
    """
    from config.settings import settings
    import anthropic

    price  = market.get("price", 0)
    rsi    = market.get("rsi", 50)
    atr    = market.get("atr", 30)
    adx    = market.get("adx", 0)
    macd_c = market.get("macd_cross","")
    stoch  = market.get("stoch_sig","")
    fib    = market.get("nearest_fib","")
    trend  = "BULLISH" if market.get("bull_trend") else "BEARISH" if market.get("bear_trend") else "SIDEWAYS"
    sess   = market.get("session","")
    best   = market.get("best_session", False)

    # Format internet data
    news    = internet.get("news", [])
    events  = internet.get("events", [])
    fxs     = internet.get("fxstreet","")[:500]
    inv_sig = internet.get("investing","")[:200]

    # Check blackout (high impact event in next 30 min)
    blackout = False
    for e in events:
        try:
            et = datetime.fromisoformat(e["date"].replace("Z",""))
            diff = (et - datetime.now()).total_seconds()/60
            if 0 < diff < 30 and e.get("impact")=="High":
                blackout = True
                break
        except Exception:
            pass

    if blackout:
        return {"decision":"WAIT","confidence":0,
                "reason":"High impact event in next 30 min — no trade",
                "blackout":True}

    prompt = f"""You are an expert XAUUSD gold trader. May 2026.
Analyze ALL data below exactly as a professional human trader would.
Then make ONE clear trading decision.

━━━ LIVE PRICE DATA ━━━
Price:    ${price:,.2f}
Session:  {sess} {"⭐ BEST TIME TO TRADE" if best else "(quiet session)"}

━━━ TECHNICAL INDICATORS ━━━
RSI(14):      {rsi} {"🔴 OVERBOUGHT" if rsi>70 else "🟢 OVERSOLD" if rsi<30 else "🟡 NEUTRAL"}
ADX(14):      {adx} — {market.get("adx_trend","")} | Bias: {market.get("di_bias","")}
MACD:         {market.get("macd",0):.2f} | Signal: {market.get("macd_signal",0):.2f} | {macd_c}
Stoch RSI:    {market.get("stoch_rsi",0)} — {stoch}
ATR(14):      {atr:.2f} (volatility measure)

━━━ TREND ━━━
EMA9:  ${market.get("ema9",0):,.2f}
EMA21: ${market.get("ema21",0):,.2f}
EMA50: ${market.get("ema50",0):,.2f}
EMA200:${market.get("ema200",0):,.2f}
Trend: {trend}
VWAP:  ${market.get("vwap",0):,.2f} | Price {"above" if price>market.get("vwap",0) else "below"} VWAP

━━━ SUPPORT & RESISTANCE ━━━
Resistance 2: ${market.get("r2",0):,.2f}
Resistance 1: ${market.get("r1",0):,.2f}
Pivot:        ${market.get("pivot",0):,.2f}
Support 1:    ${market.get("s1",0):,.2f}
Support 2:    ${market.get("s2",0):,.2f}

━━━ FIBONACCI LEVELS ━━━
100%: ${market.get("fib",{}).get("100.0",0):,.2f} (swing high)
78.6%:${market.get("fib",{}).get("78.6",0):,.2f}
61.8%:${market.get("fib",{}).get("61.8",0):,.2f} ← Golden ratio
50.0%:${market.get("fib",{}).get("50.0",0):,.2f}
38.2%:${market.get("fib",{}).get("38.2",0):,.2f}
23.6%:${market.get("fib",{}).get("23.6",0):,.2f}
0%:   ${market.get("fib",{}).get("0.0",0):,.2f} (swing low)
Price near: {fib}

━━━ BOLLINGER BANDS ━━━
Upper: ${market.get("bb_upper",0):,.2f}
Mid:   ${market.get("bb_mid",0):,.2f}
Lower: ${market.get("bb_lower",0):,.2f}
Position: {market.get("bb_position","")}

━━━ VOLUME ━━━
Volume ratio: {market.get("vol_ratio",0):.2f}x {"✅ GOOD" if market.get("vol_ratio",0)>0.8 else "⚠️ LOW"}

━━━ INTERNET RESEARCH ━━━
FXStreet Analysis:
{fxs or "Not available"}

Investing.com Signal:
{inv_sig or "Not available"}

Latest Gold News:
{chr(10).join(f"• {n}" for n in news[:4]) if news else "• No news available"}

Upcoming High-Impact Events:
{chr(10).join(f"• {e['date'][:16]} {e['title']} ({e['impact']} impact)" for e in events[:3]) if events else "• No major events"}

━━━ YOUR DECISION ━━━
Think step by step:
1. What is the overall trend? (EMA alignment + ADX)
2. Is momentum good? (MACD + RSI + Stoch)
3. Where is price relative to key levels? (S/R + Fibonacci)
4. What does the news say?
5. Is this a good session to trade?
6. What is the trade?

Respond in JSON ONLY — no other text:
{{
  "decision": "BUY" or "SELL" or "WAIT",
  "confidence": 0-100,
  "reason": "specific reason citing indicators max 120 chars",
  "entry": {price:.2f},
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "key_level": "which S/R or Fib level matters most",
  "risk_note": "main risk to this trade"
}}

SL = entry ± ATR×2 | TP = entry ± ATR×6 (3:1 RR minimum)
BUY: SL below entry, TP above. SELL: opposite.
WAIT if: ADX<15 (ranging), RSI extreme without reversal signal, poor session, no clear setup."""

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        r = await client.messages.create(
            model=settings.claude_model,
            max_tokens=400,
            messages=[{"role":"user","content":prompt}]
        )
        raw = r.content[0].text.strip()
        clean = raw.replace("```json","").replace("```","").strip()
        result = json.loads(clean)
        logger.info(
            f"Claude decision: {result.get('decision')} "
            f"conf={result.get('confidence')}% | "
            f"{result.get('reason','')[:80]}"
        )
        return result
    except Exception as e:
        logger.error(f"Claude decision failed: {e}")
        return {"decision":"WAIT","confidence":0,
                "reason":f"Claude error: {str(e)[:50]}"}


# ══════════════════════════════════════════════════════
# SECTION 4: MAIN CYCLE
# ══════════════════════════════════════════════════════

async def run_internet_trader() -> dict:
    """
    Full internet trading cycle — like a human trader.
    All research free. Claude decides. One trade.
    """
    from config.settings import settings

    if not settings.anthropic_api_key:
        return {"executed":False,"reason":"No Claude API key"}

    logger.info("🌐 Internet Trader — researching market...")

    # Run all research in parallel (all free)
    market_task   = get_full_market_analysis()
    fxstreet_task = fetch_fxstreet()
    calendar_task = fetch_forexfactory()
    kitco_task    = fetch_kitco_news()
    investing_task= fetch_investing_signals()

    market, fxstreet, events, news, inv_signals = await asyncio.gather(
        market_task, fxstreet_task, calendar_task,
        kitco_task, investing_task,
        return_exceptions=True
    )

    if isinstance(market, Exception) or not market:
        logger.error("No market data")
        return {"executed":False,"reason":"No market data"}

    internet = {
        "fxstreet": fxstreet if isinstance(fxstreet, str) else "",
        "events":   events   if isinstance(events, list)  else [],
        "news":     news     if isinstance(news, list)    else [],
        "investing":inv_signals if isinstance(inv_signals, str) else "",
    }

    logger.info(
        f"Research: price=${market.get('price',0):,.2f} "
        f"RSI={market.get('rsi',0)} ADX={market.get('adx',0)} "
        f"MACD={market.get('macd_cross','')} "
        f"News={len(internet['news'])} "
        f"Events={len(internet['events'])}"
    )

    # Store brain context
    try:
        from bot.internet_brain import get_internet_context
        brain_context = get_internet_context()
        internet["brain"] = brain_context[:300]
    except Exception:
        pass

    # Claude makes the decision (1 API call)
    decision = await claude_trade_decision(market, internet)

    if decision.get("blackout"):
        logger.warning("⚠️ Blackout period — no trading")
        return {"executed":False,"reason":"Blackout"}

    if decision.get("decision") not in ["BUY","SELL"]:
        logger.info(f"Internet Trader: WAIT — {decision.get('reason','')}")
        return {"executed":False,"reason":decision.get("reason","WAIT")}

    conf = decision.get("confidence", 0)
    if conf < 62:
        logger.info(f"Internet Trader: confidence too low {conf}%")
        return {"executed":False,"reason":f"Low confidence {conf}%"}

    # Check max open trades
    try:
        from bot.execution import _paper_trades
        open_t = [t for t in _paper_trades.values()
                 if getattr(t,"status","OPEN")=="OPEN"]
        if len(open_t) >= 2:
            logger.info(f"Internet Trader: {len(open_t)} trades open — waiting")
            return {"executed":False,"reason":"Max 2 trades open"}
    except Exception:
        pass

    # Execute
    direction = decision["decision"]
    price     = market["price"]
    atr       = market.get("atr", 30)

    sl = decision.get("stop_loss") or (
        round(price - atr*2, 2) if direction=="BUY"
        else round(price + atr*2, 2))
    tp = decision.get("take_profit") or (
        round(price + atr*6, 2) if direction=="BUY"
        else round(price - atr*6, 2))

    # Validate
    if direction=="BUY":
        if sl>=price: sl=round(price-atr*2,2)
        if tp<=price: tp=round(price+atr*6,2)
    else:
        if sl<=price: sl=round(price+atr*2,2)
        if tp>=price: tp=round(price-atr*6,2)

    # Place trade
    try:
        from bot.sniper_executor import place_paper_trade
        result = await place_paper_trade(direction, price, sl, tp, atr, conf)
    except Exception as e:
        logger.error(f"Trade execution: {e}")
        return {"executed":False,"reason":str(e)}

    if result.get("executed"):
        logger.success(
            f"✅ INTERNET TRADER: {direction} @ ${price:,.2f} "
            f"SL={sl:.2f} TP={tp:.2f} conf={conf}%\n"
            f"   Reason: {decision.get('reason','')}\n"
            f"   Key level: {decision.get('key_level','')}\n"
            f"   Risk: {decision.get('risk_note','')}"
        )

        # Store what we researched in brain
        try:
            from bot.master_brain import get_brain
            get_brain().think(
                f"INTERNET TRADE: {direction} @ ${price:,.2f} | "
                f"RSI={market.get('rsi')} ADX={market.get('adx')} "
                f"MACD={market.get('macd_cross')} | "
                f"{decision.get('reason','')}",
                agent="internet_trader",
                type_="trade_executed",
                icon="🌐",
                importance=10
            )
        except Exception:
            pass

        # Telegram
        try:
            import httpx
            msg = (
                f"🌐 INTERNET TRADER\n"
                f"{'━'*22}\n"
                f"Direction:  {direction}\n"
                f"Entry:      ${price:,.2f}\n"
                f"SL:         ${sl:,.2f}\n"
                f"TP:         ${tp:,.2f}\n"
                f"Confidence: {conf}%\n"
                f"{'━'*22}\n"
                f"RSI: {market.get('rsi')} | ADX: {market.get('adx')} | MACD: {market.get('macd_cross')}\n"
                f"Trend: {'BULL' if market.get('bull_trend') else 'BEAR'} | Session: {market.get('session')}\n"
                f"Near: {market.get('nearest_fib','')}\n"
                f"{'━'*22}\n"
                f"Reason: {decision.get('reason','')}\n"
                f"Risk: {decision.get('risk_note','')}"
            )
            from config.settings import settings as s
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id":s.telegram_chat_id,"text":msg}
                )
        except Exception:
            pass

    return result


if __name__ == "__main__":
    async def test():
        print("\n" + "="*60)
        print("  INTERNET TRADER — FULL ANALYSIS TEST")
        print("="*60 + "\n")

        print("1. Fetching market + indicators...")
        m = await get_full_market_analysis()
        if m:
            print(f"   Price: ${m['price']:,.2f}")
            print(f"   RSI: {m['rsi']} | ADX: {m['adx']} ({m['adx_trend']})")
            print(f"   MACD: {m['macd']} | Signal: {m['macd_signal']} → {m['macd_cross']}")
            print(f"   Stoch RSI: {m['stoch_rsi']} ({m['stoch_sig']})")
            print(f"   Trend: {'BULL' if m['bull_trend'] else 'BEAR' if m['bear_trend'] else 'SIDEWAYS'}")
            print(f"   Session: {m['session']}")
            print(f"   Near Fib: {m['nearest_fib']}")
            print(f"   Pivot: {m['pivot']} | R1: {m['r1']} | S1: {m['s1']}")
            print(f"   BB: {m['bb_lower']} — {m['bb_upper']} ({m['bb_position']})")

        print("\n2. Reading FXStreet...")
        fxs = await fetch_fxstreet()
        print(f"   Got {len(fxs)} chars")

        print("\n3. ForexFactory calendar...")
        events = await fetch_forexfactory()
        print(f"   {len(events)} high-impact events")
        for e in events[:2]:
            print(f"   → {e['date'][:16]} {e['title']}")

        print("\n4. Kitco news...")
        news = await fetch_kitco_news()
        print(f"   {len(news)} headlines")
        for n in news[:2]:
            print(f"   → {n[:60]}")

        print("\n5. Running full Internet Trader...")
        result = await run_internet_trader()
        print(f"\n   Executed: {result.get('executed')}")
        if result.get('executed'):
            print(f"   Direction: {result.get('direction')}")
            print(f"   Entry: ${result.get('entry',0):,.2f}")
            print(f"   Confidence: {result.get('confidence')}%")
        else:
            print(f"   Reason: {result.get('reason')}")

        print("\n✅ Internet Trader ready!")
        print("   Cost per cycle: $0.012 (1 Claude call)")
        print("   All research: FREE")

    asyncio.run(test())
