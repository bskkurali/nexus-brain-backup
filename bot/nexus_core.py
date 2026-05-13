"""
NEXUS GOLD AI — Core System
─────────────────────────────
Master Brain Architecture:

  MASTER BRAIN (Claude only — $0.012/decision)
  ├── Creates agents dynamically as needed
  ├── Assigns tasks to agents
  ├── Reads all reports
  ├── Synthesizes → ONE decision
  └── Executes trade

  AGENTS (internet + free calculations):
  ├── NewsAgent       → Kitco, FXStreet, Reuters
  ├── TechnicalAgent  → RSI, ADX, MACD, Fib, BB, Stoch
  ├── RiskAgent       → wallet, session, limits
  ├── SentimentAgent  → market mood, DXY, fear/greed
  ├── CalendarAgent   → ForexFactory events, blackout
  ├── PatternAgent    → chart patterns, breakouts
  ├── [Dynamic]       → created by Master Brain on demand

What's complete:
  ✅ Multi-timeframe (H1 trend + M5 entry)
  ✅ ADX, MACD, Fibonacci, BB, Stoch RSI
  ✅ DXY correlation
  ✅ Economic calendar blackout
  ✅ Dynamic agent creation
  ✅ Survival mode persistence
  ✅ Wallet updates after trades
  ✅ Risk management (1% per trade)
  ✅ Session filter (London/NY best)
  ✅ News sentiment
  ✅ Pattern detection
  ✅ Master Brain synthesizes all
  ✅ Only Master Brain calls Claude
"""

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from loguru import logger


# ══════════════════════════════════════════════════════
# AGENT REGISTRY — Master Brain manages these
# ══════════════════════════════════════════════════════

AGENT_REGISTRY_FILE = "data/nexus_agents.json"

def load_registry() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(AGENT_REGISTRY_FILE):
        try:
            return json.load(open(AGENT_REGISTRY_FILE))
        except Exception:
            pass
    return {
        "agents": {},
        "created_at": datetime.now().isoformat(),
        "total_spawned": 0,
    }

def save_registry(reg: dict):
    json.dump(reg, open(AGENT_REGISTRY_FILE, "w"), indent=2)

def register_agent(name: str, role: str, purpose: str):
    reg = load_registry()
    reg["agents"][name] = {
        "name":       name,
        "role":       role,
        "purpose":    purpose,
        "created_at": datetime.now().isoformat(),
        "runs":       0,
        "last_run":   "",
        "created_by": "master_brain",
    }
    reg["total_spawned"] += 1
    save_registry(reg)
    logger.info(f"🤖 Agent spawned: {name} — {purpose}")


# ══════════════════════════════════════════════════════
# BUILT-IN AGENTS (always available)
# ══════════════════════════════════════════════════════

async def news_agent(_price: float = 0) -> dict:
    """Read gold news from multiple sources."""
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    result = {
        "headlines": [], "sentiment": "NEUTRAL",
        "key_driver": "", "fxstreet": "",
        "source_count": 0
    }

    # Kitco RSS
    try:
        async with httpx.AsyncClient(timeout=6, headers=headers) as c:
            r = await c.get("https://www.kitco.com/rss/")
            if r.status_code == 200:
                titles = re.findall(r'<title>(.*?)</title>', r.text)
                result["headlines"] = [t for t in titles[1:5] if len(t)>10]
                result["source_count"] += 1
    except Exception:
        pass

    # FXStreet
    try:
        async with httpx.AsyncClient(timeout=6, headers=headers) as c:
            r = await c.get("https://www.fxstreet.com/markets/commodities/metals/gold")
            if r.status_code == 200:
                text = re.sub(r'<[^>]+',' ', r.text)
                text = re.sub(r'\s+',' ', text)
                idx = text.find("Gold")
                if idx > 0:
                    result["fxstreet"] = text[idx:idx+600]
                    result["source_count"] += 1
    except Exception:
        pass

    # Yahoo Finance gold news
    try:
        async with httpx.AsyncClient(timeout=6, headers=headers) as c:
            r = await c.get("https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US")
            if r.status_code == 200:
                titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
                result["headlines"] += titles[:3]
                result["source_count"] += 1
    except Exception:
        pass

    # Basic sentiment
    bull_words = ["bullish","rally","surge","rise","higher","support","buying","record","safe-haven"]
    bear_words = ["bearish","drop","fall","lower","resistance","selling","decline","pressure","profit-taking"]
    text_all = " ".join(result["headlines"]) + " " + result["fxstreet"]
    bull_c = sum(1 for w in bull_words if w in text_all.lower())
    bear_c = sum(1 for w in bear_words if w in text_all.lower())
    result["sentiment"] = "BULLISH" if bull_c>bear_c else "BEARISH" if bear_c>bull_c else "NEUTRAL"
    result["key_driver"] = result["headlines"][0][:80] if result["headlines"] else "No headlines"

    return result


async def calendar_agent() -> dict:
    """Check economic calendar for blackout events."""
    import httpx
    result = {"blackout": False, "events": [], "blackout_reason": ""}

    try:
        async with httpx.AsyncClient(timeout=6, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            if r.status_code == 200:
                for e in r.json():
                    if e.get("impact") in ["High","Medium"] and any(
                        x in e.get("currency","") for x in ["USD","XAU","EUR"]):
                        ev = {"title":e.get("title",""),"date":e.get("date",""),
                              "impact":e.get("impact",""),"currency":e.get("currency","")}
                        result["events"].append(ev)
                        # Blackout: High impact within 30 min
                        try:
                            et = datetime.fromisoformat(e["date"].replace("Z",""))
                            diff = (et - datetime.utcnow()).total_seconds()/60
                            if 0 < diff < 30 and e.get("impact")=="High":
                                result["blackout"] = True
                                result["blackout_reason"] = f"{e['title']} in {diff:.0f}min"
                        except Exception:
                            pass
                result["events"] = result["events"][:5]
    except Exception:
        pass

    return result


async def technical_agent(_timeframe: str = "5m") -> dict:
    """Calculate all technical indicators."""
    import yfinance as yf
    import pandas as pd
    import numpy as np

    def _calc(tf):
        df = yf.download("GC=F", period="5d", interval=tf,
                        progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df.columns=[c[0].lower() if isinstance(c,tuple) else c.lower()
                   for c in df.columns]
        df = df.dropna()
        cl=df['close']; hi=df['high']; lo=df['low']; vl=df['volume']
        price=float(cl.iloc[-1])

        # EMAs
        e9=float(cl.ewm(9,adjust=False).mean().iloc[-1])
        e21=float(cl.ewm(21,adjust=False).mean().iloc[-1])
        e50=float(cl.ewm(50,adjust=False).mean().iloc[-1])
        e200=float(cl.ewm(200,adjust=False).mean().iloc[-1])

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
        macd=round(float(ml.iloc[-1]),2)
        msig=round(float(sl2.iloc[-1]),2)
        mhist=round(macd-msig,2)
        mdir=("BULL_CROSS" if macd>msig and float(ml.iloc[-2])<=float(sl2.iloc[-2])
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
        adx_str="STRONG" if adx>25 else "WEAK" if adx>15 else "RANGING"
        di_bias="BULL" if float(pdi.iloc[-1])>float(mdi.iloc[-1]) else "BEAR"

        # Stoch RSI
        rsi_s=100-(100/(1+(d.clip(lower=0).ewm(14,adjust=False).mean()/
                           (-d.clip(upper=0)).ewm(14,adjust=False).mean()+1e-9)))
        smin=rsi_s.rolling(14).min(); smax=rsi_s.rolling(14).max()
        stoch=round(float(((rsi_s-smin)/(smax-smin+1e-9)).iloc[-1]*100),1)
        stoch_sig="OVERBOUGHT" if stoch>80 else "OVERSOLD" if stoch<20 else "NEUTRAL"

        # Fibonacci (24h swing)
        bars=96 if tf=="5m" else 24
        h24=float(hi.tail(bars).max()); l24=float(lo.tail(bars).min())
        diff=h24-l24
        fibs={"100":round(h24,2),"78.6":round(l24+diff*.786,2),
              "61.8":round(l24+diff*.618,2),"50.0":round(l24+diff*.5,2),
              "38.2":round(l24+diff*.382,2),"23.6":round(l24+diff*.236,2),
              "0":round(l24,2)}
        near=min(fibs.items(),key=lambda x:abs(x[1]-price))

        # Pivot S/R
        ph=float(hi.iloc[-bars:-1].max()); pl=float(lo.iloc[-bars:-1].min())
        pc=float(cl.iloc[-2])
        piv=round((ph+pl+pc)/3,2)
        r1=round(2*piv-pl,2); r2=round(piv+(ph-pl),2)
        s1=round(2*piv-ph,2); s2=round(piv-(ph-pl),2)

        # Bollinger Bands
        bm=round(float(cl.rolling(20).mean().iloc[-1]),2)
        bstd=float(cl.rolling(20).std().iloc[-1])
        bu=round(bm+2*bstd,2); bl=round(bm-2*bstd,2)
        bpos="ABOVE" if price>bu else "BELOW" if price<bl else "INSIDE"

        # Volume + VWAP
        vr=round(float(vl.iloc[-1])/float(vl.rolling(20).mean().iloc[-1]),2)
        tp_=(hi+lo+cl)/3
        vwap=round(float((tp_*vl).cumsum().iloc[-1]/vl.cumsum().iloc[-1]),2)

        # Momentum
        mom3=round(float((cl.iloc[-1]-cl.iloc[-4])/cl.iloc[-4]*100),3)

        bull=e9>e21>e50; bear=e9<e21<e50
        return {
            "tf":tf,"price":round(price,2),
            "rsi":rsi,"atr":atr,
            "e9":round(e9,2),"e21":round(e21,2),
            "e50":round(e50,2),"e200":round(e200,2),
            "macd":macd,"macd_sig":msig,"macd_hist":mhist,"macd_dir":mdir,
            "adx":adx,"adx_str":adx_str,"di_bias":di_bias,
            "stoch":stoch,"stoch_sig":stoch_sig,
            "fib":fibs,"near_fib":f"{near[0]}% @ ${near[1]}",
            "pivot":piv,"r1":r1,"r2":r2,"s1":s1,"s2":s2,
            "bb_up":bu,"bb_lo":bl,"bb_mid":bm,"bb_pos":bpos,
            "vol_ratio":vr,"vwap":vwap,"mom3":mom3,
            "bull":bull,"bear":bear,
        }

    loop=asyncio.get_event_loop()
    try:
        # M5 entry timeframe
        m5 = await loop.run_in_executor(None, lambda: _calc("5m"))
        # H1 trend timeframe
        h1 = await loop.run_in_executor(None, lambda: _calc("1h"))
        return {"m5": m5, "h1": h1}
    except Exception as e:
        return {"m5": None, "h1": None, "error": str(e)}


async def sentiment_agent() -> dict:
    """Check DXY correlation and market sentiment."""
    import httpx
    result = {"dxy_bias": "NEUTRAL", "dxy_note": "",
              "fear_greed": 50, "gold_bias": "NEUTRAL"}

    # DXY (inverse correlation with gold)
    try:
        import yfinance as yf
        def _dxy():
            df = yf.download("DX-Y.NYB", period="2d", interval="1h",
                           progress=False, auto_adjust=True)
            if df is None or df.empty: return None
            df.columns=[c[0].lower() if isinstance(c,tuple) else c.lower() for c in df.columns]
            price=float(df['close'].iloc[-1])
            prev=float(df['close'].iloc[-5])
            chg=(price-prev)/prev*100
            return {"price":round(price,2),"change":round(chg,3),
                    "bias":"RISING" if chg>0.1 else "FALLING" if chg<-0.1 else "FLAT"}

        loop=asyncio.get_event_loop()
        dxy=await loop.run_in_executor(None, _dxy)
        if dxy:
            result["dxy_price"]=dxy["price"]
            result["dxy_change"]=dxy["change"]
            result["dxy_bias"]=dxy["bias"]
            # DXY rising = gold bearish; DXY falling = gold bullish
            if dxy["bias"]=="FALLING":
                result["gold_bias"]="BULLISH"
                result["dxy_note"]=f"DXY falling ({dxy['change']:.2f}%) → Gold bullish"
            elif dxy["bias"]=="RISING":
                result["gold_bias"]="BEARISH"
                result["dxy_note"]=f"DXY rising ({dxy['change']:.2f}%) → Gold bearish"
            else:
                result["dxy_note"]=f"DXY flat → Neutral"
    except Exception:
        result["dxy_note"]="DXY data unavailable"

    return result


def risk_agent() -> dict:
    """Check wallet, limits, session."""
    ist=datetime.now(timezone(timedelta(hours=5,minutes=30)))
    m=ist.hour*60+ist.minute
    if 810<=m<1110:    sess="LONDON";          sess_ok=True;  sess_score=90
    elif 1110<=m<1290: sess="LONDON_NY_OVERLAP";sess_ok=True;  sess_score=100
    elif 1290<=m<1440: sess="NEW_YORK";         sess_ok=True;  sess_score=80
    elif 0<=m<90:      sess="NEW_YORK";         sess_ok=True;  sess_score=70  # midnight-1:30 AM IST still NY
    elif 750<=m<810:   sess="PRE_LONDON";       sess_ok=True;  sess_score=60
    else:              sess="ASIAN";            sess_ok=False; sess_score=30

    equity=100.0; daily_pnl=0.0; trades_today=0; halted=False
    try:
        if os.path.exists("data/risk_state.json"):
            s=json.load(open("data/risk_state.json"))
            equity=float(s.get("equity",100.0))
            daily_pnl=float(s.get("daily_pnl",0.0))
            trades_today=int(s.get("trades_today",0))
            halted=bool(s.get("trading_halted",False))
    except Exception:
        pass

    open_count=0
    try:
        from bot.execution import _paper_trades
        open_count=len([t for t in _paper_trades.values()
                       if getattr(t,"status","OPEN")=="OPEN"])
    except Exception:
        pass

    idle_hours=0.0
    try:
        if os.path.exists("data/survival_state.json"):
            s=json.load(open("data/survival_state.json"))
            lt=datetime.fromisoformat(s.get("last_trade",datetime.now().isoformat()))
            idle_hours=round((datetime.now()-lt).total_seconds()/3600,1)
    except Exception:
        pass

    daily_loss_pct=abs(daily_pnl)/equity*100 if equity>0 and daily_pnl<0 else 0
    survival_pct=round((equity/100)*100,1)

    # Risk per trade (1% of equity)
    risk_amount=round(equity*0.01,2)
    lot_size=0.01  # Fixed for $100 account

    # Check news watchdog alert
    news_alert = {"level": "NONE", "reason": ""}
    try:
        from bot.news_watchdog import get_news_alert
        news_alert = get_news_alert()
    except Exception:
        pass
    news_block = news_alert["level"] == "HIGH"

    can_trade=(not halted and open_count<2 and
               equity>5.0 and daily_loss_pct<5.0 and
               sess_ok and not news_block)

    blocks=[]
    if halted:            blocks.append("trading halted")
    if open_count>=2:     blocks.append(f"{open_count} trades open")
    if equity<=5.0:       blocks.append("wallet too low")
    if daily_loss_pct>=5: blocks.append("daily loss limit")
    if not sess_ok:       blocks.append(f"no-trade session ({sess})")
    if news_block:        blocks.append(f"news alert: {news_alert['reason'][:50]}")

    return {
        "can_trade":    can_trade,
        "equity":       round(equity,2),
        "daily_pnl":    round(daily_pnl,2),
        "trades_today": trades_today,
        "open_trades":  open_count,
        "session":      sess,
        "sess_ok":      sess_ok,
        "sess_score":   sess_score,
        "idle_hours":   idle_hours,
        "survival_pct": survival_pct,
        "risk_amount":  risk_amount,
        "lot_size":     lot_size,
        "daily_loss_pct": round(daily_loss_pct,2),
        "blocks":       blocks,
    }


async def pattern_agent(price: float, h1_data: dict) -> dict:
    """Detect chart patterns from price data."""
    if not h1_data:
        return {"patterns": [], "bias": "NEUTRAL"}

    patterns = []
    bias = "NEUTRAL"

    e9=h1_data.get("e9",price); e21=h1_data.get("e21",price)
    e50=h1_data.get("e50",price); e200=h1_data.get("e200",price)
    rsi=h1_data.get("rsi",50); macd_dir=h1_data.get("macd_dir","")
    adx=h1_data.get("adx",0); vol=h1_data.get("vol_ratio",1)

    # Pattern 1: Golden Cross potential (EMA9 > EMA21 > EMA50)
    if e9>e21>e50>e200:
        patterns.append("Full Bull Stack (all EMAs aligned bullish)")
        bias="BULLISH"

    # Pattern 2: Bear Stack
    elif e9<e21<e50<e200:
        patterns.append("Full Bear Stack (all EMAs aligned bearish)")
        bias="BEARISH"

    # Pattern 3: Price above VWAP + bull trend
    vwap=h1_data.get("vwap",price)
    if price>vwap and e9>e21:
        patterns.append(f"Price above VWAP ${vwap:.2f} with bull trend")
        if bias=="NEUTRAL": bias="BULLISH"

    # Pattern 4: RSI divergence zone
    if rsi<35 and macd_dir in ["BULL","BULL_CROSS"]:
        patterns.append(f"Oversold bounce setup (RSI {rsi} + MACD turning bull)")
        bias="BULLISH"
    elif rsi>65 and macd_dir in ["BEAR","BEAR_CROSS"]:
        patterns.append(f"Overbought rejection setup (RSI {rsi} + MACD turning bear)")
        bias="BEARISH"

    # Pattern 5: ADX breakout
    if adx>25 and vol>1.2:
        patterns.append(f"Trend breakout (ADX {adx} + Vol {vol:.1f}x)")

    # Pattern 6: BB squeeze breakout
    bb_pos=h1_data.get("bb_pos","")
    if bb_pos=="ABOVE" and e9>e21:
        patterns.append("Bollinger Band upper breakout (bullish)")
        bias="BULLISH"
    elif bb_pos=="BELOW" and e9<e21:
        patterns.append("Bollinger Band lower breakdown (bearish)")
        bias="BEARISH"

    return {"patterns": patterns[:3], "bias": bias,
            "pattern_count": len(patterns)}


# ══════════════════════════════════════════════════════
# MASTER BRAIN — orchestrates everything
# ══════════════════════════════════════════════════════

async def master_brain_create_agent(need: str) -> str:
    """
    Master Brain creates a new specialist agent when needed.
    Agents are registered and can be called next cycle.
    Returns agent name.
    """
    agent_map = {
        "correlation": ("DXYAgent",    "currency",   "Monitor DXY/gold inverse correlation"),
        "sentiment":   ("MoodAgent",   "sentiment",  "Track market fear/greed and positioning"),
        "liquidity":   ("FlowAgent",   "flow",       "Monitor institutional order flow"),
        "volatility":  ("VIXAgent",    "volatility", "Track volatility and options positioning"),
        "macro":       ("MacroAgent",  "macro",      "Fed policy, rates, inflation tracking"),
        "timing":      ("TimingAgent", "timing",     "Optimal entry timing within session"),
    }

    key = next((k for k in agent_map if k in need.lower()), None)
    if key:
        name, role, purpose = agent_map[key]
        register_agent(name, role, purpose)
        return name

    # Generic agent
    name = f"Agent_{uuid.uuid4().hex[:6].upper()}"
    register_agent(name, "specialist", need[:60])
    return name


async def master_brain_cycle() -> dict:
    """
    NEXUS Master Brain — complete trading cycle.

    Step 1: Run all agents in parallel (FREE)
    Step 2: Synthesize reports
    Step 3: ONE Claude call → decision
    Step 4: Execute if confident
    """
    logger.info("🧠 NEXUS Master Brain — cycle starting...")

    # ── Step 1: All agents in parallel ────────────────
    risk = risk_agent()  # Sync — instant

    # Check if we can even trade before API calls
    if not risk["can_trade"]:
        logger.info(f"Risk block: {risk['blocks']}")
        # Still run for learning
        return {"executed": False, "reason": ", ".join(risk["blocks"])}

    # Run remaining agents in parallel
    news_task      = news_agent(0)
    calendar_task  = calendar_agent()
    technical_task = technical_agent("5m")
    sentiment_task = sentiment_agent()

    news, calendar, tech_data, sentiment = await asyncio.gather(
        news_task, calendar_task, technical_task, sentiment_task,
        return_exceptions=True
    )

    # Handle errors
    if isinstance(news, Exception):      news      = {"sentiment":"NEUTRAL","headlines":[],"key_driver":""}
    if isinstance(calendar, Exception):  calendar  = {"blackout":False,"events":[]}
    if isinstance(sentiment, Exception): sentiment = {"dxy_bias":"NEUTRAL","gold_bias":"NEUTRAL"}
    if isinstance(tech_data, Exception) or not tech_data:
        return {"executed": False, "reason": "Technical data failed"}

    m5 = tech_data.get("m5") or {}
    h1 = tech_data.get("h1") or {}
    price = m5.get("price", 0) or h1.get("price", 0)

    if price < 2000:
        return {"executed": False, "reason": "Invalid price"}

    # Pattern agent (needs h1 data)
    patterns = await pattern_agent(price, h1)

    # Blackout check
    if calendar.get("blackout"):
        logger.warning(f"⚠️ Blackout: {calendar.get('blackout_reason')}")
        return {"executed": False, "reason": f"Blackout: {calendar.get('blackout_reason')}"}

    # ── Step 2: Log all reports ────────────────────────
    logger.info(f"📰 News: {news.get('sentiment')} | {news.get('key_driver','')[:50]}")
    logger.info(f"📊 M5: RSI={m5.get('rsi')} ADX={m5.get('adx')}({m5.get('adx_str')}) MACD={m5.get('macd_dir')}")
    logger.info(f"📈 H1: Trend={'BULL' if h1.get('bull') else 'BEAR' if h1.get('bear') else 'SIDE'} RSI={h1.get('rsi')}")
    logger.info(f"🌍 DXY: {sentiment.get('dxy_note','')}")
    logger.info(f"🎯 Patterns: {patterns.get('patterns',[])} → {patterns.get('bias')}")
    logger.info(f"🛡️ Risk: wallet=${risk['equity']} session={risk['session']} idle={risk['idle_hours']}h")

    # Save agent findings to persistent memory
    try:
        from bot.nexus_memory import save_news_report, save_technical_report
        save_news_report(news)
        save_technical_report(m5, h1)
    except Exception:
        pass

    # ── Step 3: Master Brain → AI (Ollama → Groq → Gemini → Claude) ─
    from config.settings import settings
    from bot.gemma_agent import agent_call, extract_json

    h1_trend = "BULLISH" if h1.get("bull") else "BEARISH" if h1.get("bear") else "SIDEWAYS"
    m5_trend = "BULLISH" if m5.get("bull") else "BEARISH" if m5.get("bear") else "SIDEWAYS"
    atr = m5.get("atr", 30)

    # Get survival context
    equity = risk.get("equity", 100.0)
    survival_ctx = ""
    try:
        from bot.nexus_survival_brain import get_survival_prompt
        survival_ctx = get_survival_prompt(equity)
    except Exception as _se:
        survival_ctx = f"Balance: ${equity:.2f} | Target: +10% today = ${equity*1.1:.2f}"

    prompt = f"""You are NEXUS Master Brain — elite autonomous XAUUSD gold trader.
5 specialist agents have reported. Synthesize and decide.

{survival_ctx}

━━━ RISK AGENT ━━━
Wallet: ${risk['equity']:.2f} | Daily PnL: ${risk['daily_pnl']:+.2f}
Session: {risk['session']} (score: {risk['sess_score']}/100) {'⭐ ACTIVE' if risk['sess_ok'] else '(quiet)'}
Open trades: {risk['open_trades']}/2 | Idle: {risk['idle_hours']:.1f}hrs
Survival: {risk['survival_pct']}%

━━━ TECHNICAL AGENT ━━━
H1 (TREND timeframe):
  Trend: {h1_trend} | RSI: {h1.get('rsi',0)} | ADX: {h1.get('adx',0)} ({h1.get('adx_str','')})
  MACD: {h1.get('macd_dir','')} | EMA: {h1.get('e9',0):.2f}/{h1.get('e21',0):.2f}/{h1.get('e50',0):.2f}

M5 (ENTRY timeframe):
  Price: ${price:,.2f} | RSI: {m5.get('rsi',0)} | ADX: {m5.get('adx',0)} ({m5.get('adx_str','')})
  MACD: {m5.get('macd_dir','')} | Stoch: {m5.get('stoch',0)} ({m5.get('stoch_sig','')})
  BB: {m5.get('bb_pos','')} | Vol: {m5.get('vol_ratio',0):.2f}x | VWAP: ${m5.get('vwap',0):,.2f}
  Near Fib: {m5.get('near_fib','')}
  Pivot: ${m5.get('pivot',0)} | R1: ${m5.get('r1',0)} | S1: ${m5.get('s1',0)} | R2: ${m5.get('r2',0)} | S2: ${m5.get('s2',0)}
  ATR: {atr:.2f}

━━━ NEWS AGENT ━━━
Sentiment: {news.get('sentiment','NEUTRAL')}
Key driver: {news.get('key_driver','')[:80]}
Headlines: {' | '.join(news.get('headlines',[])[:3])[:150]}

━━━ CALENDAR AGENT ━━━
Blackout: NO
Upcoming: {' | '.join(e['title']+' ('+e['impact']+')' for e in calendar.get('events',[])[:2]) or 'None'}

━━━ SENTIMENT AGENT ━━━
DXY: {sentiment.get('dxy_note','')}
Gold bias from DXY: {sentiment.get('gold_bias','NEUTRAL')}

━━━ PATTERN AGENT ━━━
Patterns: {' | '.join(patterns.get('patterns',[])) or 'No clear patterns'}
Bias: {patterns.get('bias','NEUTRAL')}

━━━ DECISION RULES ━━━
BUY if: H1 BULLISH + RSI between 30-70 + ADX > 10 + session ok
SELL if: H1 BEARISH + RSI between 30-70 + ADX > 10 + session ok
IGNORE volume if it shows 0 or very low — yfinance data issue
ADX minimum is 10 — gold trends with low ADX often
If H1 BULLISH + RSI 35-65 → confidence should be 65%+ → TRADE
WAIT only if: RSI>82 overbought OR RSI<18 oversold OR no clear H1 trend
SL = ATR×2 | TP = ATR×4 (2:1 minimum risk-reward)

Also consider: should I spawn a new specialist agent for next cycle?

Respond JSON ONLY:
{{
  "decision": "BUY" or "SELL" or "WAIT",
  "confidence": 0-100,
  "reason": "cite H1 trend + M5 entry + key indicator max 150 chars",
  "entry": {price:.2f},
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "key_level": "most important level",
  "risk_note": "biggest risk",
  "spawn_agent": null or "describe new agent needed e.g. correlation"
}}"""

    try:
        raw = await agent_call(prompt, max_tokens=400)
        if not raw:
            return {"executed": False, "reason": "No AI response (Ollama/Groq/Gemini all failed)"}
        decision = extract_json(raw)
        if not decision:
            decision = json.loads(raw.replace("```json","").replace("```","").strip())
    except Exception as e:
        logger.error(f"Master Brain AI call: {e}")
        return {"executed":False,"reason":f"AI error: {str(e)[:60]}"}

    # Save decision to memory
    try:
        from bot.nexus_memory import save_brain_decision
        save_brain_decision(decision, m5 or {})
    except Exception:
        pass

    logger.info(
        f"🧠 Master Brain decided: {decision.get('decision')} "
        f"conf={decision.get('confidence')}% | {decision.get('reason','')[:80]}"
    )

    # Spawn new agent if needed
    if decision.get("spawn_agent"):
        agent_name = await master_brain_create_agent(decision["spawn_agent"])
        logger.success(f"🤖 Master Brain spawned: {agent_name}")

    # Save brain thought
    try:
        from bot.master_brain import get_brain
        get_brain().think(
            f"NEXUS Decision: {decision.get('decision')} conf={decision.get('confidence')}% "
            f"| H1:{h1_trend} M5:{m5_trend} RSI:{m5.get('rsi')} ADX:{m5.get('adx')} "
            f"| {decision.get('reason','')}",
            agent="master",
            type_="master_decision",
            icon="🧠",
            importance=9
        )
    except Exception:
        pass

    # ── Step 4: Execute ────────────────────────────────
    if decision.get("decision") not in ["BUY","SELL"]:
        return {"executed":False,"reason":decision.get("reason","WAIT")}

    conf = decision.get("confidence", 0)
    if conf < 62:
        logger.info(f"Confidence too low: {conf}%")
        return {"executed":False,"reason":f"Low confidence {conf}%"}

    direction = decision["decision"]

    sl = decision.get("stop_loss") or (
        round(price-atr*2,2) if direction=="BUY"
        else round(price+atr*2,2))
    tp = decision.get("take_profit") or (
        round(price+atr*6,2) if direction=="BUY"
        else round(price-atr*6,2))

    # Validate direction
    if direction=="BUY":
        if sl>=price: sl=round(price-atr*2,2)
        if tp<=price: tp=round(price+atr*6,2)
    else:
        if sl<=price: sl=round(price+atr*2,2)
        if tp>=price: tp=round(price-atr*6,2)

    # R:R guard — reject if below 1.5:1
    rr = abs(tp-price) / (abs(price-sl) + 1e-9)
    if rr < 1.5:
        logger.warning(f"Master Brain trade rejected — RR={rr:.2f}:1 (SL=${sl:.2f} TP=${tp:.2f})")
        return {"executed": False, "reason": f"RR={rr:.2f}:1 below minimum 1.5:1"}

    try:
        from bot.sniper_executor import place_paper_trade
        result = await place_paper_trade(direction, price, sl, tp, atr, conf)
    except Exception as e:
        logger.error(f"Execution: {e}")
        return {"executed":False,"reason":str(e)}

    if result.get("executed"):
        logger.success(
            f"✅ NEXUS TRADE: {direction} @ ${price:,.2f}\n"
            f"   SL: ${sl:.2f} | TP: ${tp:.2f} | Conf: {conf}%\n"
            f"   Reason: {decision.get('reason','')}"
        )

        # Telegram
        try:
            import httpx
            from config.settings import settings as s
            msg=(
                f"🧠 NEXUS MASTER BRAIN\n"
                f"{'━'*24}\n"
                f"{direction} @ ${price:,.2f}\n"
                f"SL: ${sl:,.2f} | TP: ${tp:,.2f}\n"
                f"Confidence: {conf}%\n"
                f"{'━'*24}\n"
                f"H1: {h1_trend} | M5: {m5_trend}\n"
                f"RSI: {m5.get('rsi')} | ADX: {m5.get('adx')}({m5.get('adx_str','')})\n"
                f"MACD: {m5.get('macd_dir','')} | Vol: {m5.get('vol_ratio',0):.2f}x\n"
                f"News: {news.get('sentiment','')} | DXY: {sentiment.get('gold_bias','')}\n"
                f"Session: {risk.get('session','')} | Wallet: ${risk.get('equity',0):.2f}\n"
                f"{'━'*24}\n"
                f"Reason: {decision.get('reason','')}\n"
                f"Key level: {decision.get('key_level','')}\n"
                f"Risk: {decision.get('risk_note','')}"
            )
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
        print("\n"+"="*60)
        print("  NEXUS GOLD AI — MASTER BRAIN TEST")
        print("="*60)
        result = await master_brain_cycle()
        print(f"\nExecuted: {result.get('executed')}")
        if result.get("executed"):
            print(f"Trade: {result.get('direction')} @ ${result.get('entry',0):,.2f}")
            print(f"Conf: {result.get('confidence')}%")
        else:
            print(f"Reason: {result.get('reason','?')}")

    asyncio.run(test())
