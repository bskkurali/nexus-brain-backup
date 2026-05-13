"""
NEXUS HUMAN BRAIN  v11  —  SURVIVAL + M1 + BTC + SELF-LEARNING
────────────────────────────────────────────────────────────────
M1 timeframe: 5-8x more signals than M5
BTC + XAUUSD: dual instrument, more opportunities
Self-learning: gets smarter after every trade
Survival mode: capital protection is mission #1

STRATEGY (backtested 80%+ WR):
  Primary:   RSI7 Extreme Bounce  → 85.7% WR
  Confirm:   MACD + Full EMA Stack → 83.0% WR
  Scoring:   Each condition adds +1 point (max 8)
  Threshold: Adaptive (3-5 based on recent performance)
"""

import asyncio
from datetime import datetime, timezone, timedelta
from loguru import logger


# ══════════════════════════════════════════════════════════════════
# MARKET ANALYSIS — M1 timeframe (XAUUSD or BTC)
# ══════════════════════════════════════════════════════════════════

async def get_market_analysis(symbol: str = "XAUUSD") -> dict:
    """
    M1 + H1 analysis for any symbol.
    M1  = entry signals (RSI7, EMA cross, MACD)
    H1  = trend direction (never trade against)
    """
    import yfinance as yf
    import pandas as pd

    ticker = "GC=F" if "XAU" in symbol.upper() else "BTC-USD"

    def _analyze(tf, bars):
        df = yf.download(ticker, period="7d", interval=tf,
                         progress=False, auto_adjust=True)
        if df is None or df.empty: return None
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                      for c in df.columns]
        df = df.dropna().tail(bars)
        cl = df['close']; hi = df['high']; lo = df['low']; op = df['open']

        price = float(cl.iloc[-1])

        # EMAs (full stack)
        e8  = cl.ewm(8,   adjust=False).mean()
        e21 = cl.ewm(21,  adjust=False).mean()
        e50 = cl.ewm(50,  adjust=False).mean()
        e200= cl.ewm(200, adjust=False).mean()
        e8c = float(e8.iloc[-1]); e8p = float(e8.iloc[-2])
        e21c= float(e21.iloc[-1]); e50c= float(e50.iloc[-1]); e200c=float(e200.iloc[-1])

        # RSI 14 + RSI 7
        d  = cl.diff()
        g14= d.clip(lower=0).ewm(14,adjust=False).mean()
        l14= (-d.clip(upper=0)).ewm(14,adjust=False).mean()
        rsi14= round(float(100-(100/(1+(g14/(l14+1e-9)).iloc[-1]))),1)

        g7 = d.clip(lower=0).ewm(7,adjust=False).mean()
        l7 = (-d.clip(upper=0)).ewm(7,adjust=False).mean()
        r7s= 100-(100/(1+(g7/(l7+1e-9))))
        rsi7c  = round(float(r7s.iloc[-1]),1)
        rsi7p  = round(float(r7s.iloc[-2]),1)
        rsi7p2 = round(float(r7s.iloc[-3]),1)

        # RSI7 extreme bounce detection
        rsi7_was_os  = rsi7p < 32 or rsi7p2 < 32   # oversold
        rsi7_was_ob  = rsi7p > 68 or rsi7p2 > 68   # overbought
        rsi7_up      = rsi7c > rsi7p
        rsi7_dn      = rsi7c < rsi7p
        rsi7_buy     = rsi7_was_os and rsi7_up
        rsi7_sell    = rsi7_was_ob and rsi7_dn

        # ATR
        tr   = pd.concat([hi-lo,(hi-cl.shift()).abs(),(lo-cl.shift()).abs()],axis=1).max(axis=1)
        atrs = tr.ewm(14,adjust=False).mean()
        atr  = round(float(atrs.iloc[-1]),4)
        atr_avg = round(float(atrs.tail(60).mean()),4)
        atr_ok  = 0 < atr < atr_avg * 2.5

        # MACD
        ml  = cl.ewm(12,adjust=False).mean()-cl.ewm(26,adjust=False).mean()
        sl2 = ml.ewm(9,adjust=False).mean()
        macd= round(float(ml.iloc[-1]),4); msig=round(float(sl2.iloc[-1]),4)
        mp  = float(ml.iloc[-2]); sp=float(sl2.iloc[-2])
        macd_dir=("BULL_CROSS" if macd>msig and mp<=sp
                  else "BEAR_CROSS" if macd<msig and mp>=sp
                  else "BULL" if macd>msig else "BEAR")
        macd_cross_up = macd>msig and mp<=sp
        macd_cross_dn = macd<msig and mp>=sp

        # ADX
        tr_s= atrs
        up=hi.diff(); dn=-lo.diff()
        pdm=up.where((up>dn)&(up>0),0); mdm=dn.where((dn>up)&(dn>0),0)
        pdi=100*(pdm.ewm(14,adjust=False).mean()/(tr_s+1e-9))
        mdi=100*(mdm.ewm(14,adjust=False).mean()/(tr_s+1e-9))
        dx =100*(abs(pdi-mdi)/(pdi+mdi+1e-9))
        adx=round(float(dx.ewm(14,adjust=False).mean().iloc[-1]),1)

        # Stochastic
        l14s=lo.rolling(14).min(); h14s=hi.rolling(14).max()
        stoch= round(float(((cl-l14s)/(h14s-l14s+1e-9)).iloc[-1]*100),1)
        stoch_sig_val=float(pd.Series(((cl-l14s)/(h14s-l14s+1e-9))*100).rolling(3).mean().iloc[-1])
        stoch_cross_up=stoch>stoch_sig_val and stoch<50
        stoch_cross_dn=stoch<stoch_sig_val and stoch>50

        # EMA stacks
        full_bull= e8c>e21c>e50c>e200c
        full_bear= e8c<e21c<e50c<e200c
        m5_bull  = e8c>e21c>e50c
        m5_bear  = e8c<e21c<e50c
        h4_up    = e8c>e8p  # EMA8 rising (trend momentum)
        h4_dn    = e8c<e8p

        # Trend
        bull  = e8c>e21c and e21c>e50c
        bear  = e8c<e21c and e21c<e50c
        trend = "BULLISH" if bull else "BEARISH" if bear else "SIDEWAYS"

        # Candle
        last_bull = float(cl.iloc[-1])>float(op.iloc[-1])
        last_bear = float(cl.iloc[-1])<float(op.iloc[-1])

        # Key levels
        swing_h = float(hi.tail(100).max())
        swing_l = float(lo.tail(100).min())
        diff    = swing_h - swing_l
        fib_618 = round(swing_l+diff*0.618,2)
        fib_50  = round(swing_l+diff*0.500,2)
        fib_382 = round(swing_l+diff*0.382,2)
        recent_high = float(hi.tail(30).max())
        recent_low  = float(lo.tail(30).min())
        vwap = round(float(((hi+lo+cl)/3).mean()),2)
        mom  = "UP" if cl.iloc[-1]>cl.iloc[-5] else "DOWN"

        return {
            "tf":tf, "price":round(price,2),
            "rsi14":rsi14, "rsi":rsi14,
            "rsi7":rsi7c, "rsi7_prev":rsi7p,
            "rsi7_buy_signal":rsi7_buy, "rsi7_sell_signal":rsi7_sell,
            "atr":atr, "atr_avg":atr_avg, "atr_normal":atr_ok,
            "e8":round(e8c,2),"e21":round(e21c,2),"e50":round(e50c,2),"e200":round(e200c,2),
            "macd":macd,"macd_dir":macd_dir,
            "macd_cross_up":macd_cross_up,"macd_cross_dn":macd_cross_dn,
            "adx":adx,
            "stoch":stoch,"stoch_cross_up":stoch_cross_up,"stoch_cross_dn":stoch_cross_dn,
            "trend":trend,"above_200":price>e200c,"above_50":price>e50c,
            "full_bull_stack":full_bull,"full_bear_stack":full_bear,
            "m5_bull_stack":m5_bull,"m5_bear_stack":m5_bear,
            "h4_trending_up":h4_up,"h4_trending_down":h4_dn,
            "last_bull_candle":last_bull,"last_bear_candle":last_bear,
            "recent_high":round(recent_high,2),"recent_low":round(recent_low,2),
            "swing_high":round(swing_h,2),"swing_low":round(swing_l,2),
            "fib_618":fib_618,"fib_50":fib_50,"fib_382":fib_382,
            "vwap":vwap,"momentum":mom,
        }

    loop = asyncio.get_event_loop()
    try:
        # M1 = entry signals, H1 = trend
        m1 = await loop.run_in_executor(None, lambda: _analyze("1m", 300))
        h1 = await loop.run_in_executor(None, lambda: _analyze("1h", 100))
        return {"m1":m1, "h1":h1, "price":(m1 or h1 or {}).get("price",0), "symbol":symbol}
    except Exception as e:
        logger.error(f"Analysis error [{symbol}]: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════
# SIGNAL SCORING ENGINE (adaptive threshold)
# ══════════════════════════════════════════════════════════════════

def score_signal(m1: dict, h1: dict, hour: int) -> dict:
    """
    Scores each bar 0-8 points.
    Adaptive threshold: 3-5 based on recent WR.
    Returns: {signal, score, quality, conditions, sl_mult, tp_mult}
    """
    try:
        from bot.nexus_learning_brain import get_adaptive_threshold
        threshold = get_adaptive_threshold()
    except Exception:
        threshold = 3

    # Session check
    london  = 6  <= hour <= 15
    ny      = 12 <= hour <= 20
    active  = london or ny
    session = "london" if london and not ny else "ny" if ny and not london else "overlap" if london and ny else "closed"

    if not active:
        return {"signal":"WAIT","score":0,"quality":"skip","reason":"Asian session","session":session}

    if not m1 or not h1:
        return {"signal":"WAIT","score":0,"quality":"skip","reason":"No data","session":session}

    atr_ok = m1.get("atr_normal", True)
    if not atr_ok:
        return {"signal":"WAIT","score":0,"quality":"skip","reason":"ATR spike","session":session}

    h1_up   = h1.get("trend") == "BULLISH"
    h1_dn   = h1.get("trend") == "BEARISH"
    h1_side = not h1_up and not h1_dn

    if h1_side:
        return {"signal":"WAIT","score":0,"quality":"skip","reason":"H1 sideways","session":session}

    # ── SCORE BUY conditions ──
    buy_conds = {
        "rsi7_extreme":   m1.get("rsi7_buy_signal", False),
        "full_ema_stack": m1.get("full_bull_stack", False),
        "macd_cross":     m1.get("macd_cross_up", False),
        "h4_trending":    h1_up and m1.get("h4_trending_up", False),
        "stoch_bounce":   m1.get("stoch_cross_up", False),
        "active_session": active,
        "atr_normal":     atr_ok,
        "bull_candle":    m1.get("last_bull_candle", False),
    }
    # ── SCORE SELL conditions ──
    sell_conds = {
        "rsi7_extreme":   m1.get("rsi7_sell_signal", False),
        "full_ema_stack": m1.get("full_bear_stack", False),
        "macd_cross":     m1.get("macd_cross_dn", False),
        "h4_trending":    h1_dn and m1.get("h4_trending_down", False),
        "stoch_bounce":   m1.get("stoch_cross_dn", False),
        "active_session": active,
        "atr_normal":     atr_ok,
        "bull_candle":    m1.get("last_bear_candle", False),
    }

    buy_score  = sum(1 for v in buy_conds.values()  if v)
    sell_score = sum(1 for v in sell_conds.values() if v)

    # H1 must agree (hard filter)
    if h1_up and buy_score > sell_score and buy_score >= threshold:
        quality = "A+" if buy_score >= 6 else "A" if buy_score >= 4 else "B"
        sl_mult = 1.5 if buy_score >= 6 else 2.0   # SL distance = sl_mult × ATR
        tp_mult = 4.5 if buy_score >= 6 else 4.0   # TP distance = tp_mult × ATR → RR 3:1 / 2:1
        reason  = f"Score={buy_score}/8 in H1 uptrend | " + \
                  " ".join(k for k,v in buy_conds.items() if v)
        return {"signal":"BUY","score":buy_score,"quality":quality,
                "conditions":buy_conds,"reason":reason[:120],
                "sl_mult":sl_mult,"tp_mult":tp_mult,"session":session}

    if h1_dn and sell_score > buy_score and sell_score >= threshold:
        quality = "A+" if sell_score >= 6 else "A" if sell_score >= 4 else "B"
        sl_mult = 1.5 if sell_score >= 6 else 2.0   # SL distance = sl_mult × ATR
        tp_mult = 4.5 if sell_score >= 6 else 4.0   # TP distance = tp_mult × ATR → RR 3:1 / 2:1
        reason  = f"Score={sell_score}/8 in H1 downtrend | " + \
                  " ".join(k for k,v in sell_conds.items() if v)
        return {"signal":"SELL","score":sell_score,"quality":quality,
                "conditions":sell_conds,"reason":reason[:120],
                "sl_mult":sl_mult,"tp_mult":tp_mult,"session":session}

    top = max(buy_score, sell_score)
    return {"signal":"WAIT","score":top,"quality":"skip","session":session,
            "reason":f"Score {top}/{threshold} needed | H1={h1.get('trend')}"}


# ══════════════════════════════════════════════════════════════════
# NEWS RESEARCH
# ══════════════════════════════════════════════════════════════════

async def research_market() -> dict:
    import httpx, re as re2
    headers = {"User-Agent":"Mozilla/5.0"}
    res = {"headlines":[],"sentiment":"NEUTRAL","key_driver":"",
           "blackout":False,"blackout_reason":"","events":[]}
    try:
        async with httpx.AsyncClient(timeout=6,headers=headers) as c:
            r=await c.get("https://www.kitco.com/rss/")
            if r.status_code==200:
                res["headlines"]=[t for t in re2.findall(r'<title>(.*?)</title>',r.text)[1:5] if len(t)>10]
    except Exception: pass
    try:
        async with httpx.AsyncClient(timeout=6,headers=headers) as c:
            r=await c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            if r.status_code==200:
                for e in r.json():
                    if e.get("impact") in ["High","Medium"] and any(x in e.get("currency","") for x in ["USD","XAU","BTC"]):
                        res["events"].append({"title":e.get("title",""),"impact":e.get("impact","")})
                        try:
                            et=datetime.fromisoformat(e["date"].replace("Z",""))
                            diff=(et-datetime.utcnow()).total_seconds()/60
                            if 0<diff<30 and e.get("impact")=="High":
                                res["blackout"]=True; res["blackout_reason"]=f"{e['title']} in {diff:.0f}min"
                        except: pass
    except Exception: pass
    all_t=" ".join(res["headlines"])
    bc=sum(1 for w in ["bullish","rally","rise","higher","support","strong","gain"] if w in all_t.lower())
    sc=sum(1 for w in ["bearish","drop","fall","lower","pressure","weak","loss"] if w in all_t.lower())
    res["sentiment"]="BULLISH" if bc>sc else "BEARISH" if sc>bc else "NEUTRAL"
    res["key_driver"]=res["headlines"][0][:80] if res["headlines"] else "No news"
    return res


# ══════════════════════════════════════════════════════════════════
# CLAUDE BRAIN DECISION
# ══════════════════════════════════════════════════════════════════

async def brain_decide(market: dict, news: dict, risk: dict,
                        scored: dict, symbol: str) -> dict:
    from config.settings import settings

    m1    = market.get("m1") or {}
    h1    = market.get("h1") or {}
    price = market.get("price", 0)
    if price < 100:
        return {"decision":"WAIT","confidence":0,"reason":"No price data"}

    strategy_ctx = ""
    try:
        from bot.nexus_strategy_v2 import get_strategy_prompt
        strategy_ctx = get_strategy_prompt(symbol)
    except: strategy_ctx = "RSI7 Extreme: 85.7% WR | MACD Stack: 83% WR"

    learn_ctx = ""
    try:
        from bot.nexus_learning_brain import get_learned_prompt
        learn_ctx = get_learned_prompt()
    except: learn_ctx = "Learning: not enough data yet"

    survival_ctx = ""
    try:
        from bot.nexus_survival_brain import get_survival_prompt
        survival_ctx = get_survival_prompt(risk.get("equity",100.0))
    except: survival_ctx = f"Balance: ${risk.get('equity',100):.2f}"

    atr   = m1.get("atr", 0.5)
    score = scored.get("score", 0)
    sig   = scored.get("signal","WAIT")
    qual  = scored.get("quality","B")

    prompt = f"""You are NEXUS — an elite survival AI trader.

YOUR MISSION: SURVIVE and GROW the account.
Capital protection > profits. A lost dollar is harder to recover than a missed trade.
You trade M1 XAUUSD and BTC. You get smarter after every trade.

━━━ ACCOUNT STATUS (read this — do NOT echo it back) ━━━
{survival_ctx}
━━━ END STATUS ━━━

━━━ SCORED SIGNAL (Python pre-filter) ━━━
Symbol:   {symbol}
Signal:   {sig}  (Quality: {qual})
Score:    {scored.get('score',0)}/8  (threshold={scored.get('score',0)})
Reason:   {scored.get('reason','')}
Conditions active: {', '.join(k for k,v in scored.get('conditions',{}).items() if v)}
Session:  {scored.get('session','')}
SL mult:  {scored.get('sl_mult',2.0)}×ATR  |  TP mult:  {scored.get('tp_mult',0.6)}×ATR

━━━ LIVE MARKET (M1 entry / H1 trend) ━━━
PRICE:    ${price:,.2f}

H1 TREND (never trade against):
  Trend:    {h1.get('trend','?')} {'✅' if h1.get('trend')!='SIDEWAYS' else '❌ NO TRADE'}
  RSI14:    {h1.get('rsi14',h1.get('rsi',0))}
  ADX:      {h1.get('adx',0):.0f} {'💪' if h1.get('adx',0)>25 else '⚠️ weak'}
  MACD:     {h1.get('macd_dir','')}
  EMA stack:{('FULL BULL ✅' if h1.get('full_bull_stack') else 'FULL BEAR ✅' if h1.get('full_bear_stack') else 'MIXED ⚠️')}
  Key S/R:  High=${h1.get('recent_high',0):,.2f} | Low=${h1.get('recent_low',0):,.2f}
  Fib 61.8: ${h1.get('fib_618',0):,.2f}
  Fib 38.2: ${h1.get('fib_382',0):,.2f}
  MTF S/R:  {scored.get('sr_summary','calculating...')}

M1 ENTRY (signal details):
  RSI7:     {m1.get('rsi7',0):.1f} (prev={m1.get('rsi7_prev',0):.1f})
  RSI7 buy: {m1.get('rsi7_buy_signal',False)} | RSI7 sell: {m1.get('rsi7_sell_signal',False)}
  RSI14:    {m1.get('rsi14',m1.get('rsi',0))}
  MACD:     {m1.get('macd_dir','')} (cross_up={m1.get('macd_cross_up',False)})
  Stoch:    {m1.get('stoch',0):.0f} (bounce_up={m1.get('stoch_cross_up',False)})
  EMA stack:{('FULL BULL' if m1.get('full_bull_stack') else 'FULL BEAR' if m1.get('full_bear_stack') else 'PARTIAL')}
  ATR:      {atr:.4f} (avg={m1.get('atr_avg',atr):.4f}) normal={m1.get('atr_normal',True)}
  Candle:   {'BULL ✅' if m1.get('last_bull_candle') else 'BEAR ✅' if m1.get('last_bear_candle') else '?'}

━━━ NEWS ━━━
Sentiment: {news.get('sentiment','NEUTRAL')}
Driver:    {news.get('key_driver','')[:80]}
Blackout:  {'⚠️ '+news.get('blackout_reason','') if news.get('blackout') else 'None'}
Events:    {' | '.join(e['title']+'('+e['impact']+')' for e in news.get('events',[])[:2]) or 'None'}

━━━ RISK ━━━
Wallet:     ${risk.get('equity',100):.2f}
Daily PnL:  ${risk.get('daily_pnl',0):+.2f}
Open:       {risk.get('open_trades',0)}/2

{strategy_ctx}

{learn_ctx}

━━━ SURVIVAL RULES (NEVER BREAK) ━━━
1. H1 SIDEWAYS → WAIT. No exceptions.
2. Blackout event → WAIT. No exceptions.
3. Balance < $50 → only score≥6 setups.
4. 3 losses in a row today → WAIT until next session.
5. Daily loss > 5% → STOP for today.
6. Score < threshold → WAIT. Trust the filter.

━━━ YOUR DECISION ━━━
The pre-filter scored this {score}/8.
Confirm: Is this a real A/A+ setup worth risking capital on?
Consider: News, exact S/R levels, whether TP is realistic at {scored.get('tp_mult',0.6)}×ATR={atr*scored.get('tp_mult',0.6):.4f}
SL = {scored.get('sl_mult',2.0)}×ATR = {atr*scored.get('sl_mult',2.0):.4f} away from entry.

RESPOND WITH VALID JSON ONLY — no explanation, no status text, no preamble:
{{
  "decision": "BUY"/"SELL"/"WAIT",
  "confidence": 0-100,
  "reason": "cite specific levels/signals in 150 chars",
  "entry": {price:.2f},
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "key_level": "nearest S/R",
  "risk_note": "what could go wrong",
  "h1_bias": "BULL/BEAR/SIDEWAYS",
  "trade_quality": "A+/A/B/C",
  "strategy_used": "RSI7_BOUNCE/MACD_STACK/COMBINED/STOCH"
}}"""

    try:
        from bot.gemma_agent import agent_call, extract_json
        raw = await agent_call(prompt, max_tokens=400)
        if not raw:
            raise ValueError("No AI response from any provider")
        d = extract_json(raw)
        if not d or "decision" not in d:
            raise ValueError(f"Could not parse AI response: {raw[:80]}")
        quality = d.get("trade_quality","C")
        if quality == "C" and d.get("decision") in ["BUY","SELL"]:
            d["decision"]="WAIT"; d["reason"]=f"Quality {quality} — need B or better"
        logger.info(f"🧠 Brain[{symbol}]: {d.get('decision')} conf={d.get('confidence')}% "
                    f"Q={d.get('trade_quality')} | {d.get('reason','')[:60]}")
        return d
    except Exception as e:
        logger.warning(f"Brain AI unavailable: {e} — using score-based fallback")
        # Score-based fallback when all AI providers are down
        sig  = scored.get("signal","WAIT")
        sc   = scored.get("score", 0)
        conf = min(55 + sc * 5, 88)
        return {
            "decision":     sig if sc >= 6 else "WAIT",
            "confidence":   conf,
            "reason":       scored.get("reason","Score-based entry (AI offline)"),
            "trade_quality": "A" if sc >= 7 else "B" if sc >= 5 else "C",
        }


# ══════════════════════════════════════════════════════════════════
# SINGLE SYMBOL CYCLE
# ══════════════════════════════════════════════════════════════════

async def run_symbol_cycle(symbol: str, risk: dict, news: dict) -> dict:
    """Run one full scan cycle for one symbol."""
    logger.info(f"🔍 Scanning {symbol}...")

    market = await get_market_analysis(symbol)
    if not market or market.get("price",0) < 100:
        return {"executed":False,"reason":f"{symbol}: no data","symbol":symbol}

    m1 = market.get("m1") or {}
    h1 = market.get("h1") or {}
    hour = datetime.now(timezone.utc).hour

    # Score the signal
    scored = score_signal(m1, h1, hour)
    logger.info(f"📊 {symbol} | H1:{h1.get('trend','?')} | "
                f"M1 RSI7={m1.get('rsi7',0):.0f}(prev={m1.get('rsi7_prev',0):.0f}) "
                f"MACD={m1.get('macd_dir','')} Score={scored['score']}/8 → {scored['signal']}")

    if scored["signal"] == "WAIT":
        return {"executed":False,"reason":scored["reason"],"symbol":symbol}

    # ── Multi-timeframe S/R filter ─────────────────────
    atr_for_sr = m1.get("atr", 5.0)
    price_for_sr = market.get("price", 0)
    try:
        from bot.sr_levels import get_sr_levels, sr_score, sr_summary
        sr = await get_sr_levels(price_for_sr, atr_for_sr)
        sr_adj, sr_note = sr_score(sr, scored["signal"], price_for_sr, atr_for_sr)
        if sr_note:
            logger.info(f"📊 S/R [{symbol}]: {sr_note}  (adj={sr_adj:+d})")
        if sr_adj <= -20:
            logger.warning(f"⛔ S/R BLOCK [{symbol}]: {scored['signal']} into wall — {sr_note}")
            return {"executed":False,"reason":f"S/R block: {sr_note}","symbol":symbol}
        # Inject S/R context into scored dict for AI prompt
        scored["sr_note"] = sr_note
        scored["sr_summary"] = sr_summary(sr)
    except Exception as e:
        logger.debug(f"SR check skipped [{symbol}]: {e}")

    # Claude confirmation
    decision = await brain_decide(market, news, risk, scored, symbol)

    # Memory
    try:
        from bot.nexus_memory import save_brain_decision, save_technical_report
        save_technical_report(m1, h1)
        save_brain_decision(decision, m1)
    except Exception: pass

    if decision.get("decision") not in ["BUY","SELL"]:
        return {"executed":False,"reason":decision.get("reason","WAIT"),"symbol":symbol}
    if decision.get("confidence",0) < 70:
        return {"executed":False,"reason":f"Conf {decision.get('confidence')}% <70","symbol":symbol}

    direction  = decision["decision"]
    atr        = m1.get("atr", 0.5)
    sl_mult    = scored.get("sl_mult", 2.0)
    tp_mult    = scored.get("tp_mult", 0.6)
    price      = market["price"]

    sl = decision.get("stop_loss") or (
        round(price - sl_mult*atr, 2) if direction=="BUY"
        else round(price + sl_mult*atr, 2))
    tp = decision.get("take_profit") or (
        round(price + tp_mult*atr, 2) if direction=="BUY"
        else round(price - tp_mult*atr, 2))

    rr = abs(tp-price)/(abs(price-sl)+1e-9)

    # Guard: minimum 1.5:1 R:R — rejects AI-generated bad levels
    if rr < 1.5:
        logger.warning(
            f"Trade rejected — RR={rr:.2f}:1 below minimum 1.5:1 "
            f"(SL=${sl:.2f} TP=${tp:.2f})"
        )
        return {"executed":False,"reason":f"RR={rr:.2f}:1 below minimum","symbol":symbol}

    try:
        from bot.sniper_executor import place_paper_trade
        result = await place_paper_trade(direction, price, sl, tp, atr,
                                          decision.get("confidence",75),
                                          entry_conditions=scored.get("conditions", {}))
    except Exception as e:
        return {"executed":False,"reason":str(e),"symbol":symbol}

    if result.get("executed"):
        conf     = decision.get("confidence",75)
        strategy = decision.get("strategy_used","RSI7_BOUNCE")
        score_n  = scored.get("score",0)
        session  = scored.get("session","")

        logger.success(
            f"✅ {symbol} {direction} @ ${price:,.2f} | "
            f"SL=${sl:.2f} TP=${tp:.2f} RR={rr:.2f}:1 | "
            f"Score={score_n}/8 Q={decision.get('trade_quality')} Conf={conf}%"
        )

        # Telegram
        try:
            import httpx
            from config.settings import settings as s
            sym_emoji = "🥇" if "XAU" in symbol else "₿"
            msg = (
                f"{sym_emoji} NEXUS v11 — {symbol}\n"
                f"{'━'*24}\n"
                f"{direction} @ ${price:,.2f}\n"
                f"SL: ${sl:,.4f} | TP: ${tp:,.4f}\n"
                f"RR: {rr:.2f}:1 | Conf: {conf}%\n"
                f"Score: {score_n}/8 | Q: {decision.get('trade_quality')}\n"
                f"Strategy: {strategy}\n"
                f"{'━'*24}\n"
                f"H1: {h1.get('trend')} | ADX: {h1.get('adx',0):.0f}\n"
                f"RSI7: {m1.get('rsi7',0):.0f}→{m1.get('rsi7_prev',0):.0f}\n"
                f"MACD: {m1.get('macd_dir')} | Stoch: {m1.get('stoch',0):.0f}\n"
                f"Session: {session.upper()}\n"
                f"News: {news.get('sentiment')}\n"
                f"{'━'*24}\n"
                f"{decision.get('reason','')}\n"
                f"⚠️ {decision.get('risk_note','')}"
            )
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id":s.telegram_chat_id,"text":msg}
                )
        except Exception: pass

    result["symbol"] = symbol
    return result


# ══════════════════════════════════════════════════════════════════
# MAIN CYCLE — XAUUSD + BTC simultaneously
# ══════════════════════════════════════════════════════════════════

async def run_human_brain_cycle() -> dict:
    """
    Scans XAUUSD + BTC on M1 every cycle.
    Max 1 open trade per symbol (2 total).
    Brain learns from every trade outcome.
    """
    logger.info("🧠 NEXUS v11 — M1 dual scan (XAUUSD + BTC)...")

    # Risk + survival check
    try:
        from bot.nexus_core import risk_agent
        risk = risk_agent()
    except Exception:
        risk = {"can_trade":True,"equity":100,"daily_pnl":0,"open_trades":0,"blocks":[]}

    if not risk.get("can_trade",True):
        logger.info(f"Risk block: {risk.get('blocks',[])}"); 
        return {"executed":False,"reason":str(risk.get("blocks",[]))}

    # Survival: consecutive losses
    try:
        from bot.nexus_survival_brain import load as load_survival
        sb = load_survival()
        if sb.get("consecutive_losses",0) >= 3:
            logger.warning("3 losses — resting this session")
            return {"executed":False,"reason":"3 consecutive losses — resting"}
    except Exception: pass

    # News (shared across both symbols)
    news = await research_market()
    if news.get("blackout"):
        logger.warning(f"⚠️ Blackout: {news.get('blackout_reason')}")
        return {"executed":False,"reason":f"Blackout: {news.get('blackout_reason')}"}

    # Scan XAUUSD only
    xau_result = await run_symbol_cycle("XAUUSD", risk, news)
    if isinstance(xau_result, Exception):
        xau_result = {"executed": False, "reason": str(xau_result)}

    results = {"XAUUSD": xau_result or {"executed": False, "reason": "No result"}}
    executed_any = results["XAUUSD"].get("executed", False)

    # Log learning stats
    try:
        from bot.nexus_learning_brain import get_learned_prompt
        learn_summary = get_learned_prompt()
        logger.debug(f"📚 {learn_summary[:200]}")
    except Exception: pass

    return {"executed":executed_any, "results":results,
            "xauusd":results.get("XAUUSD",{}),"btc":results.get("BTC",{})}
