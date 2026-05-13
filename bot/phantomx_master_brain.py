"""
PHANTOMX937 — Master Brain
───────────────────────────
ARCHITECTURE:
  NewsAgent      → internet research → reports JSON
  TechnicalAgent → indicators       → reports JSON
  RiskAgent      → wallet/session   → reports JSON
  Master Brain   → reads all 3 reports → 1 Claude call → trade

Only Master Brain talks to Claude.
All agents use free internet/calculations.
Cost per cycle: $0.012 (1 Claude call)
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone, timedelta
from loguru import logger


# ══════════════════════════════════════════════════════
# AGENT 1: NEWS AGENT — reads internet, reports to brain
# ══════════════════════════════════════════════════════

async def news_agent_report() -> dict:
    """
    Reads gold news from internet.
    Reports findings to Master Brain.
    No API calls — all free.
    """
    import httpx
    report = {
        "agent": "NewsAgent",
        "headlines": [],
        "sentiment": "NEUTRAL",
        "key_driver": "",
        "blackout": False,
        "upcoming_events": [],
    }

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # Read Kitco headlines
    try:
        async with httpx.AsyncClient(timeout=6, headers=headers) as c:
            r = await c.get("https://www.kitco.com/rss/")
            if r.status_code == 200:
                titles = re.findall(r'<title>(.*?)</title>', r.text)
                report["headlines"] = [t for t in titles[1:5] if len(t)>10]
    except Exception:
        pass

    # Read FXStreet snippet
    try:
        async with httpx.AsyncClient(timeout=6, headers=headers) as c:
            r = await c.get("https://www.fxstreet.com/markets/commodities/metals/gold")
            if r.status_code == 200:
                text = re.sub(r'<[^>]+>',' ',r.text)
                text = re.sub(r'\s+',' ',text)
                idx = text.find("Gold")
                if idx>0:
                    snippet = text[idx:idx+800]
                    report["fxstreet"] = snippet

                    # Basic sentiment detection
                    bull_words = ["bullish","rally","surge","rise","higher","support","buying"]
                    bear_words = ["bearish","drop","fall","lower","resistance","selling","decline"]
                    bull_count = sum(1 for w in bull_words if w in snippet.lower())
                    bear_count = sum(1 for w in bear_words if w in snippet.lower())
                    if bull_count > bear_count:
                        report["sentiment"] = "BULLISH"
                    elif bear_count > bull_count:
                        report["sentiment"] = "BEARISH"
    except Exception:
        pass

    # ForexFactory calendar — check for blackout events
    try:
        async with httpx.AsyncClient(timeout=6, headers=headers) as c:
            r = await c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            if r.status_code == 200:
                events = r.json()
                for e in events:
                    if e.get("impact") in ["High","Medium"]:
                        if any(x in e.get("currency","") for x in ["USD","XAU"]):
                            report["upcoming_events"].append({
                                "title":  e.get("title",""),
                                "date":   e.get("date",""),
                                "impact": e.get("impact",""),
                            })
                            # Check blackout (High impact in next 30 min)
                            try:
                                et = datetime.fromisoformat(
                                    e["date"].replace("Z",""))
                                diff = (et-datetime.now()).total_seconds()/60
                                if 0 < diff < 30 and e.get("impact")=="High":
                                    report["blackout"] = True
                                    report["key_driver"] = f"⚠️ {e['title']} in {diff:.0f} min"
                            except Exception:
                                pass
                report["upcoming_events"] = report["upcoming_events"][:3]
    except Exception:
        pass

    # Extract key driver from headlines
    if report["headlines"] and not report["key_driver"]:
        report["key_driver"] = report["headlines"][0][:80]

    logger.debug(f"NewsAgent: {report['sentiment']} | {len(report['headlines'])} headlines | blackout={report['blackout']}")
    return report


# ══════════════════════════════════════════════════════
# AGENT 2: TECHNICAL AGENT — calculates indicators
# ══════════════════════════════════════════════════════

async def technical_agent_report() -> dict:
    """
    Calculates ALL technical indicators.
    Reports findings to Master Brain.
    Uses yfinance — free.
    """
    import yfinance as yf
    import pandas as pd

    def _calc():
        df = yf.download("GC=F", period="5d", interval="5m",
                        progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None

        df.columns=[c[0].lower() if isinstance(c,tuple) else c.lower()
                   for c in df.columns]
        df = df.dropna()
        cl=df['close']; hi=df['high']; lo=df['low']; vl=df['volume']
        price = float(cl.iloc[-1])

        # EMAs
        e9   = float(cl.ewm(span=9,  adjust=False).mean().iloc[-1])
        e21  = float(cl.ewm(span=21, adjust=False).mean().iloc[-1])
        e50  = float(cl.ewm(span=50, adjust=False).mean().iloc[-1])
        e200 = float(cl.ewm(span=200,adjust=False).mean().iloc[-1])

        # RSI
        d=cl.diff(); g=d.clip(lower=0).ewm(14,adjust=False).mean()
        l=(-d.clip(upper=0)).ewm(14,adjust=False).mean()
        rsi=round(float(100-(100/(1+(g/(l+1e-9)).iloc[-1]))),1)

        # ATR
        tr=pd.concat([hi-lo,(hi-cl.shift()).abs(),(lo-cl.shift()).abs()],axis=1).max(axis=1)
        atr=round(float(tr.ewm(14,adjust=False).mean().iloc[-1]),2)

        # MACD
        ml=cl.ewm(12,adjust=False).mean()-cl.ewm(26,adjust=False).mean()
        sl2=ml.ewm(9,adjust=False).mean()
        macd=round(float(ml.iloc[-1]),2); msig=round(float(sl2.iloc[-1]),2)
        macd_dir=("BULL_CROSS" if macd>msig and float(ml.iloc[-2])<=float(sl2.iloc[-2])
                 else "BEAR_CROSS" if macd<msig and float(ml.iloc[-2])>=float(sl2.iloc[-2])
                 else "BULL" if macd>msig else "BEAR")

        # ADX
        tr_s=tr.ewm(14,adjust=False).mean()
        up=hi.diff(); dn=-lo.diff()
        pdm=up.where((up>dn)&(up>0),0)
        mdm=dn.where((dn>up)&(dn>0),0)
        pdi=100*(pdm.ewm(14,adjust=False).mean()/tr_s)
        mdi=100*(mdm.ewm(14,adjust=False).mean()/tr_s)
        dx=100*(abs(pdi-mdi)/(pdi+mdi+1e-9))
        adx=round(float(dx.ewm(14,adjust=False).mean().iloc[-1]),1)
        adx_str=("STRONG" if adx>25 else "WEAK" if adx>15 else "RANGING")
        di_bias="BULL" if float(pdi.iloc[-1])>float(mdi.iloc[-1]) else "BEAR"

        # Fibonacci (1-day swing)
        h24=float(hi.tail(96).max()); l24=float(lo.tail(96).min())
        diff=h24-l24
        fibs={
            "100": round(h24,2),
            "78.6":round(l24+diff*0.786,2),
            "61.8":round(l24+diff*0.618,2),
            "50.0":round(l24+diff*0.500,2),
            "38.2":round(l24+diff*0.382,2),
            "23.6":round(l24+diff*0.236,2),
            "0":   round(l24,2),
        }
        near=min(fibs.items(),key=lambda x:abs(x[1]-price))

        # Pivot points
        ph=float(hi.iloc[-48:-1].max())
        pl=float(lo.iloc[-48:-1].min())
        pc=float(cl.iloc[-2])
        pivot=round((ph+pl+pc)/3,2)
        r1=round(2*pivot-pl,2); r2=round(pivot+(ph-pl),2)
        s1=round(2*pivot-ph,2); s2=round(pivot-(ph-pl),2)

        # BB
        bm=round(float(cl.rolling(20).mean().iloc[-1]),2)
        bstd=float(cl.rolling(20).std().iloc[-1])
        bu=round(bm+2*bstd,2); bl=round(bm-2*bstd,2)
        bpos=("ABOVE_UPPER" if price>bu else "BELOW_LOWER" if price<bl else "INSIDE")

        # Volume
        vr=round(float(vl.iloc[-1])/float(vl.rolling(20).mean().iloc[-1]),2)

        # VWAP
        tp=(hi+lo+cl)/3
        vwap=round(float((tp*vl).cumsum().iloc[-1]/vl.cumsum().iloc[-1]),2)

        # Trend
        bull=e9>e21>e50; bear=e9<e21<e50

        # Support/Resistance near price
        dist_r1=abs(price-r1); dist_s1=abs(price-s1)
        nearest_sr=f"near R1 ${r1}" if dist_r1<atr else f"near S1 ${s1}" if dist_s1<atr else "between S/R"

        return {
            "agent":"TechnicalAgent",
            "price":round(price,2),
            "rsi":rsi, "atr":atr,
            "ema9":round(e9,2),"ema21":round(e21,2),
            "ema50":round(e50,2),"ema200":round(e200,2),
            "macd":macd,"macd_signal":msig,"macd_cross":macd_dir,
            "adx":adx,"adx_strength":adx_str,"di_bias":di_bias,
            "fib_levels":fibs,"nearest_fib":f"{near[0]}% @ ${near[1]}",
            "pivot":pivot,"r1":r1,"r2":r2,"s1":s1,"s2":s2,
            "bb_upper":bu,"bb_lower":bl,"bb_mid":bm,"bb_pos":bpos,
            "vol_ratio":vr,"vwap":vwap,
            "bull_trend":bull,"bear_trend":bear,
            "nearest_sr":nearest_sr,
            "summary": (
                f"Price ${price:,.2f} | RSI {rsi} | ADX {adx}({adx_str}) "
                f"| MACD {macd_dir} | Trend {'BULL' if bull else 'BEAR' if bear else 'SIDE'} "
                f"| {nearest_sr} | Near Fib {near[0]}%"
            )
        }

    try:
        loop=asyncio.get_event_loop()
        result=await loop.run_in_executor(None, _calc)
        if result:
            logger.debug(f"TechnicalAgent: {result['summary']}")
        return result or {"agent":"TechnicalAgent","error":"No data"}
    except Exception as e:
        return {"agent":"TechnicalAgent","error":str(e)}


# ══════════════════════════════════════════════════════
# AGENT 3: RISK AGENT — checks wallet and conditions
# ══════════════════════════════════════════════════════

def risk_agent_report() -> dict:
    """
    Checks wallet, session, open trades, daily limits.
    Reports to Master Brain. No API needed.
    """
    # Session
    ist=datetime.now(timezone(timedelta(hours=5,minutes=30)))
    m=ist.hour*60+ist.minute
    if 810<=m<1110:    sess="LONDON"; sess_ok=True
    elif 1110<=m<1290: sess="LONDON_NY_OVERLAP"; sess_ok=True
    elif 1290<=m<1380: sess="NEW_YORK"; sess_ok=True
    elif 750<=m<810:   sess="PRE_LONDON"; sess_ok=False
    else:              sess="ASIAN"; sess_ok=False

    # Wallet
    equity=100.0; daily_pnl=0.0; trades_today=0; halted=False
    try:
        sf="data/risk_state.json"
        if os.path.exists(sf):
            s=json.load(open(sf))
            equity=float(s.get("equity",100.0))
            daily_pnl=float(s.get("daily_pnl",0.0))
            trades_today=int(s.get("trades_today",0))
            halted=bool(s.get("trading_halted",False))
    except Exception:
        pass

    # Open trades
    open_count=0
    try:
        from bot.execution import _paper_trades
        open_count=len([t for t in _paper_trades.values()
                       if getattr(t,"status","OPEN")=="OPEN"])
    except Exception:
        pass

    # Idle hours
    idle_hours=0
    try:
        sf2="data/survival_state.json"
        if os.path.exists(sf2):
            s2=json.load(open(sf2))
            lt=datetime.fromisoformat(s2.get("last_trade",datetime.now().isoformat()))
            idle_hours=round((datetime.now()-lt).total_seconds()/3600,1)
    except Exception:
        pass

    # Risk assessment
    daily_loss_pct=abs(daily_pnl/equity*100) if equity>0 and daily_pnl<0 else 0
    can_trade=(not halted and open_count<2 and equity>5.0
              and daily_loss_pct<5.0)

    risk_note=""
    if halted:           risk_note="Trading halted"
    elif open_count>=2:  risk_note=f"{open_count} trades already open"
    elif equity<=5.0:    risk_note="Wallet too low"
    elif daily_loss_pct>=5.0: risk_note="Daily loss limit hit"
    elif not sess_ok:    risk_note=f"Asian session — fewer signals"
    else:                risk_note="All clear"

    report={
        "agent":       "RiskAgent",
        "can_trade":   can_trade,
        "equity":      round(equity,2),
        "daily_pnl":   round(daily_pnl,2),
        "trades_today":trades_today,
        "open_trades": open_count,
        "session":     sess,
        "best_session":sess_ok,
        "idle_hours":  idle_hours,
        "risk_note":   risk_note,
        "summary": (
            f"Wallet ${equity:.2f} | Session {sess} "
            f"| Open trades {open_count}/2 "
            f"| Daily PnL ${daily_pnl:+.2f} "
            f"| {'✅ CAN TRADE' if can_trade else '❌ BLOCKED: '+risk_note}"
        )
    }
    logger.debug(f"RiskAgent: {report['summary']}")
    return report


# ══════════════════════════════════════════════════════
# MASTER BRAIN — reads all reports, calls Claude once
# ══════════════════════════════════════════════════════

async def master_brain_decide(
    news: dict,
    tech: dict,
    risk: dict
) -> dict:
    """
    Master Brain reads all agent reports.
    Makes ONE Claude call.
    Returns trade decision.
    """
    from config.settings import settings
    import anthropic

    if not settings.anthropic_api_key:
        return {"decision":"WAIT","reason":"No Claude key","confidence":0}

    price = tech.get("price", 0)
    if price < 2000:
        return {"decision":"WAIT","reason":"Invalid price","confidence":0}

    # Build comprehensive prompt from agent reports
    prompt = f"""You are PHANTOMX937 Master Brain — autonomous gold trader.
3 specialist agents have researched the market and reported to you.
Read their reports carefully. Make ONE trading decision.

━━━ RISK AGENT REPORT ━━━
{risk.get('summary','')}
Session:     {risk.get('session','')} {"⭐ ACTIVE" if risk.get('best_session') else "(quiet)"}
Wallet:      ${risk.get('equity',0):.2f} | Daily PnL: ${risk.get('daily_pnl',0):+.2f}
Open trades: {risk.get('open_trades',0)}/2 max
Idle:        {risk.get('idle_hours',0):.1f} hours (penalty after 24hrs)
Can trade:   {"YES" if risk.get('can_trade') else "NO — "+risk.get('risk_note','')}

━━━ TECHNICAL AGENT REPORT ━━━
{tech.get('summary','')}
Price:     ${price:,.2f}
RSI:       {tech.get('rsi',0)} {"🔴 OVERBOUGHT" if tech.get('rsi',0)>70 else "🟢 OVERSOLD" if tech.get('rsi',0)<30 else "✅ NEUTRAL"}
ADX:       {tech.get('adx',0)} — {tech.get('adx_strength','')} | Bias: {tech.get('di_bias','')}
MACD:      {tech.get('macd',0)} | Signal: {tech.get('macd_signal',0)} → {tech.get('macd_cross','')}
EMA trend: {"BULLISH (9>21>50)" if tech.get('bull_trend') else "BEARISH (9<21<50)" if tech.get('bear_trend') else "SIDEWAYS"}
Pivot:     ${tech.get('pivot',0)} | R1: ${tech.get('r1',0)} | S1: ${tech.get('s1',0)}
Near:      {tech.get('nearest_fib','')} | {tech.get('nearest_sr','')}
BB:        {tech.get('bb_pos','')} ({tech.get('bb_lower',0)} — {tech.get('bb_upper',0)})
Volume:    {tech.get('vol_ratio',0):.2f}x {"✅" if tech.get('vol_ratio',0)>0.8 else "⚠️ LOW"}
ATR:       {tech.get('atr',0):.2f}

━━━ NEWS AGENT REPORT ━━━
Sentiment:  {news.get('sentiment','NEUTRAL')}
Blackout:   {"⚠️ YES — "+news.get('key_driver','') if news.get('blackout') else "No"}
Key driver: {news.get('key_driver','')}
Headlines:  {chr(10)+'  '.join(news.get('headlines',[])[:3]) if news.get('headlines') else 'None'}
Events:     {chr(10)+'  '.join(e['date'][:16]+' '+e['title']+' ('+e['impact']+')' for e in news.get('upcoming_events',[])[:2]) if news.get('upcoming_events') else 'None'}

━━━ YOUR DECISION AS MASTER BRAIN ━━━
Think step by step:
1. Can we trade? (RiskAgent says {risk.get('can_trade')})
2. What is the trend? (ADX + EMA alignment)
3. Is momentum confirming? (MACD + RSI)
4. Where are key levels? (Fib + S/R)
5. What does news say?
6. Is session good?

RULES:
- WAIT if RiskAgent says cannot trade
- WAIT if ADX<15 (ranging — no trend)
- WAIT if blackout (high-impact event soon)
- WAIT if RSI>78 (overbought) or RSI<22 (oversold) without reversal signal
- BUY if: bull trend + MACD bull + RSI 35-68 + good session + volume ok
- SELL if: bear trend + MACD bear + RSI 32-65 + good session + volume ok
- SL = ATR×2 from entry | TP = ATR×6 from entry (3:1 RR)

Respond JSON ONLY:
{{
  "decision": "BUY" or "SELL" or "WAIT",
  "confidence": 0-100,
  "reason": "cite 2-3 specific indicators max 120 chars",
  "entry": {price:.2f},
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "key_level": "most important price level right now",
  "risk_note": "biggest risk to this trade"
}}"""

    try:
        client=anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        r=await client.messages.create(
            model=settings.claude_model,
            max_tokens=350,
            messages=[{"role":"user","content":prompt}]
        )
        raw=r.content[0].text.strip()
        d=json.loads(raw.replace("```json","").replace("```","").strip())
        logger.info(
            f"🧠 Master Brain → {d.get('decision')} "
            f"conf={d.get('confidence')}% | "
            f"{d.get('reason','')[:80]}"
        )
        return d
    except Exception as e:
        logger.error(f"Master Brain Claude call failed: {e}")
        return {"decision":"WAIT","confidence":0,
                "reason":f"Claude error: {str(e)[:60]}"}


# ══════════════════════════════════════════════════════
# MAIN CYCLE — orchestrates all agents
# ══════════════════════════════════════════════════════

async def run_master_cycle() -> dict:
    """
    Full Master Brain cycle:
    1. All 3 agents research in parallel (FREE)
    2. Master Brain reads reports
    3. ONE Claude call → decision
    4. Execute if BUY/SELL with confidence ≥ 62%
    """
    logger.info("🧠 PHANTOMX937 Master Brain cycle starting...")

    # Step 1: All agents research in parallel — FREE
    news_task = news_agent_report()
    tech_task = technical_agent_report()

    news, tech = await asyncio.gather(
        news_task, tech_task, return_exceptions=True
    )

    if isinstance(news, Exception):
        news = {"agent":"NewsAgent","sentiment":"NEUTRAL",
                "headlines":[],"blackout":False,"upcoming_events":[]}
    if isinstance(tech, Exception) or not tech or tech.get("error"):
        logger.error(f"Technical data failed: {tech}")
        return {"executed":False,"reason":"No technical data"}

    # Risk agent is synchronous — no API needed
    risk = risk_agent_report()

    # Log all reports
    logger.info(f"📰 News: {news.get('sentiment','?')} | {news.get('key_driver','')[:50]}")
    logger.info(f"📊 Tech: {tech.get('summary','')[:80]}")
    logger.info(f"🛡️ Risk: {risk.get('summary','')[:80]}")

    # Check blackout immediately
    if news.get("blackout"):
        logger.warning(f"⚠️ BLACKOUT: {news.get('key_driver','')}")
        return {"executed":False,"reason":"Blackout period"}

    # Check if we can even trade
    if not risk.get("can_trade"):
        logger.info(f"Risk block: {risk.get('risk_note','')}")
        return {"executed":False,"reason":risk.get("risk_note","")}

    # Step 2: Master Brain makes ONE Claude call
    decision = await master_brain_decide(news, tech, risk)

    # Save brain thought
    try:
        from bot.master_brain import get_brain
        get_brain().think(
            f"Master Brain cycle: {decision.get('decision')} "
            f"conf={decision.get('confidence')}% | "
            f"RSI={tech.get('rsi')} ADX={tech.get('adx')} "
            f"MACD={tech.get('macd_cross')} | "
            f"{decision.get('reason','')}",
            agent="master",
            type_="master_decision",
            icon="🧠",
            importance=8
        )
    except Exception:
        pass

    # Step 3: Execute if confident
    if decision.get("decision") not in ["BUY","SELL"]:
        return {"executed":False,"reason":decision.get("reason","WAIT")}

    conf = decision.get("confidence", 0)
    if conf < 62:
        logger.info(f"Confidence too low: {conf}%")
        return {"executed":False,"reason":f"Low confidence {conf}%"}

    direction = decision["decision"]
    price     = tech["price"]
    atr       = tech.get("atr", 30)

    sl = decision.get("stop_loss") or (
        round(price-atr*2,2) if direction=="BUY"
        else round(price+atr*2,2))
    tp = decision.get("take_profit") or (
        round(price+atr*6,2) if direction=="BUY"
        else round(price-atr*6,2))

    # Validate levels
    if direction=="BUY":
        if sl>=price: sl=round(price-atr*2,2)
        if tp<=price: tp=round(price+atr*6,2)
    else:
        if sl<=price: sl=round(price+atr*2,2)
        if tp>=price: tp=round(price-atr*6,2)

    # Execute
    try:
        from bot.sniper_executor import place_paper_trade
        result = await place_paper_trade(direction, price, sl, tp, atr, conf)
    except Exception as e:
        logger.error(f"Execution error: {e}")
        return {"executed":False,"reason":str(e)}

    if result.get("executed"):
        logger.success(
            f"✅ MASTER BRAIN TRADE: {direction} @ ${price:,.2f}\n"
            f"   SL: ${sl:.2f} | TP: ${tp:.2f}\n"
            f"   Confidence: {conf}%\n"
            f"   Reason: {decision.get('reason','')}\n"
            f"   Key level: {decision.get('key_level','')}"
        )

        # Telegram alert
        try:
            import httpx
            from config.settings import settings
            msg=(
                f"🧠 MASTER BRAIN TRADE\n"
                f"{'━'*22}\n"
                f"{direction} @ ${price:,.2f}\n"
                f"SL: ${sl:,.2f} | TP: ${tp:,.2f}\n"
                f"Confidence: {conf}%\n"
                f"{'━'*22}\n"
                f"📊 RSI:{tech.get('rsi')} ADX:{tech.get('adx')}({tech.get('adx_strength','')})\n"
                f"📈 MACD:{tech.get('macd_cross','')} Trend:{'BULL' if tech.get('bull_trend') else 'BEAR'}\n"
                f"📰 News:{news.get('sentiment','')} | {news.get('key_driver','')[:50]}\n"
                f"🛡️ Session:{risk.get('session','')} Wallet:${risk.get('equity',0):.2f}\n"
                f"{'━'*22}\n"
                f"Reason: {decision.get('reason','')}\n"
                f"Key level: {decision.get('key_level','')}\n"
                f"Risk: {decision.get('risk_note','')}"
            )
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id":settings.telegram_chat_id,"text":msg}
                )
        except Exception:
            pass

    return result


if __name__ == "__main__":
    async def test():
        print("\n" + "="*60)
        print("  PHANTOMX937 MASTER BRAIN TEST")
        print("="*60)

        print("\n1. Running all agents in parallel...")
        news = await news_agent_report()
        print(f"   NewsAgent: {news['sentiment']} | {len(news['headlines'])} headlines")

        tech = await technical_agent_report()
        if not tech.get("error"):
            print(f"   TechAgent: {tech.get('summary','')[:70]}")
        else:
            print(f"   TechAgent: Error — {tech.get('error')}")

        risk = risk_agent_report()
        print(f"   RiskAgent: {risk.get('summary','')[:70]}")

        print("\n2. Master Brain deciding (1 Claude call)...")
        result = await run_master_cycle()
        print(f"\n   Executed: {result.get('executed')}")
        if result.get('executed'):
            print(f"   Trade: {result.get('direction')} @ ${result.get('entry',0):,.2f}")
        else:
            print(f"   Reason: {result.get('reason','?')}")

        print("\n✅ Master Brain architecture working!")
        print("   3 agents research FREE")
        print("   1 Claude call = $0.012")

    asyncio.run(test())
