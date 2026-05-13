"""
EMA Ribbon Strategy — NEXUS Gold
──────────────────────────────────
Trend-following using Fibonacci EMA ribbon (8·13·21·34·55·89).

Logic:
  1. Ribbon ordered bullishly (8>13>21>34>55>89) → strong uptrend confirmed
  2. Price PULLS BACK to touch the ribbon (near EMA13–EMA34 zone)
  3. Candle CLOSES BACK above EMA8 → bounce confirmed
  4. Volume expanding on bounce candle → institutional participation
  5. H1 ribbon also bullish → multi-timeframe alignment

Why this works:
  - EMAs act as dynamic support/resistance in trending markets
  - Each pullback to ribbon = institutions adding to trend
  - Fibonacci periods (8,13,21,34,55,89) = natural market rhythm
  - Ribbon spread width = trend strength gauge

Entry:   Pullback to ribbon + bounce candle
SL:      Below EMA55 (deep ribbon — trend invalidated below here)
TP:      2.5× SL (lower RR but ~68%+ win rate in trending markets)

Best sessions: London open (11:30 IST), NY open (17:30 IST)
"""

import asyncio
from datetime import datetime, timezone
from loguru import logger

# ── Ribbon Parameters ──────────────────────────────────
EMA_PERIODS   = [8, 13, 21, 34, 55, 89]   # Fibonacci EMA ribbon
SL_BELOW_EMA  = 55                          # SL below EMA55 (deep ribbon)
TP_RR         = 2.5                         # 2.5:1 RR
MIN_ADX       = 20                          # Minimum trend strength
MIN_CONF      = 70                          # AI minimum confidence
MAX_ATR_MULT  = 2.0                         # Skip on news spike


# ══════════════════════════════════════════════════════
# RIBBON ANALYSIS
# ══════════════════════════════════════════════════════

async def get_ribbon_data() -> dict:
    """Fetch M5 + H1 data with full EMA ribbon calculation."""
    import yfinance as yf
    import pandas as pd

    def _calc(tf: str, bars: int) -> dict:
        df = yf.download("GC=F", period="7d", interval=tf,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                      for c in df.columns]
        df = df.dropna().tail(bars)

        cl = df["close"]; hi = df["high"]; lo = df["low"]

        # ── EMA Ribbon ─────────────────────────────────
        emas = {}
        for p in EMA_PERIODS:
            emas[p] = cl.ewm(p, adjust=False).mean()

        e8  = float(emas[8].iloc[-1]);  e8p  = float(emas[8].iloc[-2])
        e13 = float(emas[13].iloc[-1])
        e21 = float(emas[21].iloc[-1])
        e34 = float(emas[34].iloc[-1])
        e55 = float(emas[55].iloc[-1])
        e89 = float(emas[89].iloc[-1])

        price  = float(cl.iloc[-1])
        price_p = float(cl.iloc[-2])
        open_c = float(df["open"].iloc[-1])

        # ── Ribbon state ───────────────────────────────
        ribbon_bull = e8 > e13 > e21 > e34 > e55 > e89
        ribbon_bear = e8 < e13 < e21 < e34 < e55 < e89

        # Spread: distance between fastest and slowest EMA
        spread      = abs(e8 - e89)
        # Spread expanding = trend accelerating
        spread_p    = abs(e8p - float(emas[89].iloc[-2]))
        spread_wide = spread > spread_p  # ribbon widening

        # ── Pullback detection ──────────────────────────
        # BUY: price dipped into ribbon (touched EMA13–EMA34) then closed above EMA8
        touched_ribbon_buy  = price_p <= e21 or (lo.iloc[-1] <= e21)
        bounced_buy         = price > e8   # close back above fastest EMA
        candle_bull         = price > open_c  # bullish candle body

        # SELL: price rose into ribbon (touched EMA13–EMA34) then closed below EMA8
        touched_ribbon_sell = price_p >= e21 or (hi.iloc[-1] >= e21)
        bounced_sell        = price < e8
        candle_bear         = price < open_c

        # ── ATR ────────────────────────────────────────
        tr      = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
        atr     = round(float(tr.ewm(14, adjust=False).mean().iloc[-1]), 4)
        atr_avg = round(float(tr.ewm(14, adjust=False).mean().tail(60).mean()), 4)

        # ── RSI14 ──────────────────────────────────────
        d   = cl.diff()
        g14 = d.clip(lower=0).ewm(14, adjust=False).mean()
        l14 = (-d.clip(upper=0)).ewm(14, adjust=False).mean()
        rsi14 = round(float((100 - 100 / (1 + g14 / (l14 + 1e-9))).iloc[-1]), 1)

        # ── ADX ────────────────────────────────────────
        tr_s = tr.ewm(14, adjust=False).mean()
        up   = hi.diff(); dn = -lo.diff()
        pdm  = up.where((up > dn) & (up > 0), 0)
        mdm  = dn.where((dn > up) & (dn > 0), 0)
        pdi  = 100 * (pdm.ewm(14, adjust=False).mean() / (tr_s + 1e-9))
        mdi  = 100 * (mdm.ewm(14, adjust=False).mean() / (tr_s + 1e-9))
        dx   = 100 * (abs(pdi - mdi) / (pdi + mdi + 1e-9))
        adx  = round(float(dx.ewm(14, adjust=False).mean().iloc[-1]), 1)

        # ── MACD ───────────────────────────────────────
        ml      = cl.ewm(12, adjust=False).mean() - cl.ewm(26, adjust=False).mean()
        sig     = ml.ewm(9, adjust=False).mean()
        hist    = ml - sig
        macd_bull = float(ml.iloc[-1]) > float(sig.iloc[-1])
        macd_bear = float(ml.iloc[-1]) < float(sig.iloc[-1])
        hist_rising  = float(hist.iloc[-1]) > float(hist.iloc[-2])
        hist_falling = float(hist.iloc[-1]) < float(hist.iloc[-2])

        # ── Volume footprint ───────────────────────────
        try:
            vol_col = "volume" if "volume" in df.columns else None
            vol     = df[vol_col] if vol_col else None
            if vol is not None and vol.sum() > 0:
                hl       = (hi - lo).replace(0, 1e-9)
                buy_vol  = vol * (cl - lo) / hl
                sell_vol = vol * (hi - cl) / hl
                delta    = buy_vol - sell_vol
                vol_avg  = float(vol.tail(20).mean()) or 1
                vol_ratio = round(float(vol.iloc[-1]) / vol_avg, 2)
                delta_now = float(delta.iloc[-1])
                vol_expanding = vol_ratio >= 1.1
                delta_confirms_buy  = delta_now > 0 and vol_expanding
                delta_confirms_sell = delta_now < 0 and vol_expanding
            else:
                raise ValueError("no vol")
        except Exception:
            vol_ratio = 1.0; delta_now = 0
            delta_confirms_buy = False; delta_confirms_sell = False
            vol_expanding = False

        return {
            "price":    round(price, 2),
            "open":     round(open_c, 2),
            # Ribbon values
            "e8":  round(e8,2),  "e13": round(e13,2),
            "e21": round(e21,2), "e34": round(e34,2),
            "e55": round(e55,2), "e89": round(e89,2),
            # Ribbon state
            "ribbon_bull":  ribbon_bull,
            "ribbon_bear":  ribbon_bear,
            "spread":       round(spread, 2),
            "spread_wide":  spread_wide,
            # Pullback + bounce
            "touched_ribbon_buy":  touched_ribbon_buy,
            "bounced_buy":         bounced_buy,
            "candle_bull":         candle_bull,
            "touched_ribbon_sell": touched_ribbon_sell,
            "bounced_sell":        bounced_sell,
            "candle_bear":         candle_bear,
            # Indicators
            "atr":      atr, "atr_avg": atr_avg,
            "rsi14":    rsi14,
            "adx":      adx,
            "macd_bull":    macd_bull,  "macd_bear":    macd_bear,
            "hist_rising":  hist_rising, "hist_falling": hist_falling,
            # Volume
            "vol_ratio":             vol_ratio,
            "delta":                 round(delta_now, 0),
            "delta_confirms_buy":    delta_confirms_buy,
            "delta_confirms_sell":   delta_confirms_sell,
        }

    loop = asyncio.get_event_loop()
    try:
        m5 = await loop.run_in_executor(None, lambda: _calc("5m", 300))
        h1 = await loop.run_in_executor(None, lambda: _calc("1h", 100))
        return {"m5": m5, "h1": h1, "price": (m5 or {}).get("price", 0)}
    except Exception as e:
        logger.error(f"Ribbon data error: {e}")
        return {}


# ══════════════════════════════════════════════════════
# RIBBON SIGNAL SCORING — 9 conditions
# ══════════════════════════════════════════════════════

def score_ribbon_signal(m5: dict, h1: dict) -> dict:
    """
    Score the EMA ribbon pullback setup.
    9 conditions — threshold adapts to session + ribbon strength.
    """
    if not m5 or not h1:
        return {"signal": "WAIT", "score": 0, "reason": "No data"}

    # Session gate
    hour_utc = datetime.now(timezone.utc).hour
    london   = 6  <= hour_utc <= 15
    ny       = 13 <= hour_utc <= 20
    if not (london or ny):
        return {"signal": "WAIT", "score": 0, "reason": "Outside London/NY session"}

    # ATR spike gate
    atr = m5.get("atr", 1); atr_avg = m5.get("atr_avg", 1) or 1
    if atr > atr_avg * MAX_ATR_MULT:
        return {"signal": "WAIT", "score": 0, "reason": "ATR spike — skip"}

    # ADX gate — ribbon only works in trending markets
    if m5.get("adx", 0) < MIN_ADX:
        return {"signal": "WAIT", "score": 0,
                "reason": f"ADX={m5.get('adx',0):.1f} too low — market ranging"}

    # RSI health check — not overextended
    rsi = m5.get("rsi14", 50)
    rsi_healthy_buy  = 35 < rsi < 68   # healthy pullback territory
    rsi_healthy_sell = 32 < rsi < 65

    # H1 ribbon alignment (multi-timeframe)
    h1_bull = h1.get("ribbon_bull", False)
    h1_bear = h1.get("ribbon_bear", False)

    # ── BUY conditions (9) ────────────────────────────
    buy_conds = {
        "ribbon_bullish":     m5.get("ribbon_bull", False),    # All EMAs stacked up
        "h1_ribbon_aligned":  h1_bull,                         # H1 also bullish
        "price_at_ribbon":    m5.get("touched_ribbon_buy", False),  # Pulled back to ribbon
        "bounce_candle":      m5.get("bounced_buy", False),    # Closed back above EMA8
        "bullish_candle":     m5.get("candle_bull", False),    # Green candle on bounce
        "ribbon_expanding":   m5.get("spread_wide", False),    # Trend accelerating
        "rsi_healthy":        rsi_healthy_buy,                 # Not overbought
        "macd_positive":      m5.get("macd_bull", False),      # MACD aligned
        "volume_confirms":    m5.get("delta_confirms_buy", False),  # Volume footprint
    }

    # ── SELL conditions (9) ───────────────────────────
    sell_conds = {
        "ribbon_bearish":     m5.get("ribbon_bear", False),
        "h1_ribbon_aligned":  h1_bear,
        "price_at_ribbon":    m5.get("touched_ribbon_sell", False),
        "bounce_candle":      m5.get("bounced_sell", False),
        "bearish_candle":     m5.get("candle_bear", False),
        "ribbon_expanding":   m5.get("spread_wide", False),
        "rsi_healthy":        rsi_healthy_sell,
        "macd_negative":      m5.get("macd_bear", False),
        "volume_confirms":    m5.get("delta_confirms_sell", False),
    }

    buy_score  = sum(1 for v in buy_conds.values()  if v)
    sell_score = sum(1 for v in sell_conds.values() if v)

    threshold = 6   # Ribbon needs 6/9 — stricter than Fib (ribbon has more fakeouts)

    if m5.get("ribbon_bull") and h1_bull and buy_score >= threshold:
        quality = "A+" if buy_score >= 8 else "A" if buy_score >= 7 else "B"
        # SL below EMA55, TP = SL × RR
        sl_dist = abs(m5["price"] - m5.get("e55", m5["price"] - atr * 1.5))
        sl_dist = max(sl_dist, atr * 1.0)   # minimum 1×ATR
        tp_dist = sl_dist * TP_RR
        reason  = (f"[RIBBON] BUY score={buy_score}/9 | "
                   + " ".join(k for k, v in buy_conds.items() if v))
        return {
            "signal":     "BUY",
            "score":      buy_score,
            "threshold":  threshold,
            "quality":    quality,
            "strategy":   "EMA_RIBBON",
            "sl_dist":    round(sl_dist, 2),
            "tp_dist":    round(tp_dist, 2),
            "reason":     reason[:130],
            "conditions": buy_conds,
            "e55":        m5.get("e55", 0),
        }

    if m5.get("ribbon_bear") and h1_bear and sell_score >= threshold:
        quality = "A+" if sell_score >= 8 else "A" if sell_score >= 7 else "B"
        sl_dist = abs(m5["price"] - m5.get("e55", m5["price"] + atr * 1.5))
        sl_dist = max(sl_dist, atr * 1.0)
        tp_dist = sl_dist * TP_RR
        reason  = (f"[RIBBON] SELL score={sell_score}/9 | "
                   + " ".join(k for k, v in sell_conds.items() if v))
        return {
            "signal":     "SELL",
            "score":      sell_score,
            "threshold":  threshold,
            "quality":    quality,
            "strategy":   "EMA_RIBBON",
            "sl_dist":    round(sl_dist, 2),
            "tp_dist":    round(tp_dist, 2),
            "reason":     reason[:130],
            "conditions": sell_conds,
            "e55":        m5.get("e55", 0),
        }

    top = max(buy_score, sell_score)
    ribbon_state = ("BULL" if m5.get("ribbon_bull") else
                    "BEAR" if m5.get("ribbon_bear") else "MIXED")
    return {
        "signal":    "WAIT",
        "score":     top,
        "threshold": threshold,
        "reason":    f"[RIBBON {ribbon_state}] Score {top}/{threshold} | ADX={m5.get('adx',0):.1f}",
    }


# ══════════════════════════════════════════════════════
# MAIN CYCLE
# ══════════════════════════════════════════════════════

async def run_ribbon_cycle() -> dict:
    """
    Full scan cycle for EMA Ribbon strategy.
    Called every 5 minutes from main.py scheduler.
    """
    from config.settings import settings

    logger.info("🎀 EMA Ribbon — scanning trend pullback...")

    # Risk gate
    try:
        from bot.risk_engine import circuit_breaker_check
        paused, reason = circuit_breaker_check()
        if paused:
            logger.info(f"Ribbon risk gate: {reason}")
            return {"executed": False, "reason": reason}
    except Exception:
        pass

    # Max open trades gate
    try:
        from bot.execution import _paper_trades
        open_count = sum(1 for t in _paper_trades.values()
                         if getattr(t, "status", "OPEN") == "OPEN")
        if open_count >= 2:
            return {"executed": False, "reason": "Max 2 open trades"}
    except Exception:
        pass

    # Fetch data
    data = await get_ribbon_data()
    if not data or data.get("price", 0) < 1000:
        return {"executed": False, "reason": "No price data"}

    m5 = data.get("m5") or {}
    h1 = data.get("h1") or {}

    # Score signal
    scored = score_ribbon_signal(m5, h1)

    logger.info(
        f"🎀 Ribbon | "
        f"{'BULL' if m5.get('ribbon_bull') else 'BEAR' if m5.get('ribbon_bear') else 'MIXED'} | "
        f"Spread={m5.get('spread',0):.1f} ADX={m5.get('adx',0):.1f} | "
        f"Δ={m5.get('delta',0):+.0f} Vol×{m5.get('vol_ratio',1):.1f} | "
        f"Score={scored.get('score',0)}/{scored.get('threshold',6)} → {scored.get('signal')}"
    )

    if scored.get("signal") == "WAIT":
        return {"executed": False, "reason": scored.get("reason", "No setup")}

    # ── Multi-timeframe S/R filter ─────────────────────
    try:
        from bot.sr_levels import get_sr_levels, sr_score, sr_summary
        sr = await get_sr_levels(data["price"], m5.get("atr", 5.0))
        sr_adj, sr_note = sr_score(sr, scored["signal"], data["price"], m5.get("atr", 5.0))
        if sr_note:
            logger.info(f"📊 S/R [Ribbon]: {sr_note}  (adj={sr_adj:+d})")
        if sr_adj <= -20:
            logger.warning(f"⛔ S/R BLOCK [Ribbon]: {scored['signal']} into wall — {sr_note}")
            return {"executed": False, "reason": f"S/R block: {sr_note}"}
        scored["sr_summary"] = sr_summary(sr)
    except Exception as e:
        logger.debug(f"SR check skipped [Ribbon]: {e}")

    # AI confirmation
    decision = await _ai_confirm(data, scored, settings)

    if decision.get("decision") not in ("BUY", "SELL"):
        return {"executed": False, "reason": decision.get("reason", "AI said WAIT")}

    if decision.get("confidence", 0) < MIN_CONF:
        return {"executed": False, "reason": f"Low confidence {decision['confidence']}%"}

    # Build entry
    direction = decision["decision"]
    price     = data["price"]
    sl_dist   = scored["sl_dist"]
    tp_dist   = scored["tp_dist"]

    if direction == "BUY":
        sl = round(price - sl_dist, 2)
        tp = round(price + tp_dist, 2)
    else:
        sl = round(price + sl_dist, 2)
        tp = round(price - tp_dist, 2)

    rr          = tp_dist / (sl_dist + 1e-9)
    dollar_risk = sl_dist * 0.01 * 100

    logger.info(
        f"🎀 {direction} @ ${price:,.2f} | SL=${sl:.2f} TP=${tp:.2f} | "
        f"RR={rr:.1f}:1 Risk=${dollar_risk:.2f} | "
        f"EMA55=${scored.get('e55',0):,.2f} | Q={scored['quality']} Conf={decision['confidence']}%"
    )

    # Execute
    try:
        from bot.sniper_executor import place_paper_trade
        result = await place_paper_trade(
            direction, price, sl, tp,
            m5.get("atr", 5.0),
            decision.get("confidence", 75)
        )
    except Exception as e:
        return {"executed": False, "reason": str(e)}

    if result.get("executed"):
        ticket = result.get("ticket", "")

        # Record in trade journal
        try:
            from bot.trade_journal import record_entry
            record_entry(ticket, {
                "direction":   direction,
                "entry_price": price,
                "stop_loss":   sl,
                "take_profit": tp,
                "fib_level":   scored.get("e55", 0),   # SL anchor for journal
                "score":       scored["score"],
                "quality":     scored["quality"],
                "regime":      "TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
                "rsi7":        m5.get("rsi14", 0),
                "atr":         m5.get("atr", 0),
                "h1_bias":     "UP" if direction == "BUY" else "DN",
                "confidence":  decision.get("confidence", 0),
            })
        except Exception:
            pass

        # Telegram
        try:
            import httpx
            from config.settings import settings as s
            msg = (
                f"🎀 EMA RIBBON TRADE\n"
                f"{'━'*22}\n"
                f"{direction} XAUUSDm @ ${price:,.2f}\n"
                f"SL: ${sl:,.2f} | TP: ${tp:,.2f}\n"
                f"RR: {rr:.1f}:1 | Risk: ${dollar_risk:.2f}\n"
                f"EMA55 anchor: ${scored.get('e55',0):,.2f}\n"
                f"Score: {scored['score']}/9 | Q: {scored['quality']}\n"
                f"Conf: {decision['confidence']}%\n"
                f"{'━'*22}\n"
                f"{decision.get('reason','')[:80]}"
            )
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id": s.telegram_chat_id, "text": msg}
                )
        except Exception:
            pass

    result["strategy"] = "EMA_RIBBON"
    return result


# ══════════════════════════════════════════════════════
# AI CONFIRMATION
# ══════════════════════════════════════════════════════

async def _ai_confirm(data: dict, scored: dict, settings) -> dict:
    """AI reviews the ribbon setup before execution."""
    import json

    m5    = data.get("m5") or {}
    price = data.get("price", 0)

    has_ai = (settings.google_ai_api_key or
              settings.grok_api_key or
              settings.anthropic_api_key)

    if not has_ai:
        base_conf = 50 + scored["score"] * 5
        return {
            "decision":     scored["signal"],
            "confidence":   base_conf,
            "reason":       scored.get("reason", "Score-based"),
            "trade_quality": scored.get("quality", "B"),
        }

    # Recent performance from journal
    try:
        from bot.trade_journal import get_recent_performance
        perf = get_recent_performance(10)
        perf_str = (f"{perf.get('win_rate',0)}% WR | "
                    f"streak={perf.get('streak','none')} | "
                    f"P&L=${perf.get('total_pnl',0):+.2f}")
    except Exception:
        perf_str = "no data"

    # News sentiment
    try:
        from bot.internet_brain import get_news_sentiment
        ns       = get_news_sentiment()
        news_str = f"{ns['sentiment']} — {ns['summary']}"
    except Exception:
        news_str = "no data"

    # DXY / macro context
    macro_line = "DXY unavailable"
    try:
        from bot.dxy_brain import get_macro_prompt
        macro_line = await get_macro_prompt()
    except Exception:
        pass

    prompt = f"""You are NEXUS Ribbon, a trend-following gold trader.

STRATEGY: EMA Ribbon Pullback
- 6 Fibonacci EMAs: 8·13·21·34·55·89
- Enter when price pulls back to ribbon then bounces
- SL: below EMA55 (trend invalidation) = ${scored.get('sl_dist',0):.2f} pts
- TP: {TP_RR}× SL = ${scored.get('tp_dist',0):.2f} pts → {TP_RR}:1 RR

━━━ RIBBON STATE ━━━
M5 Ribbon:  {'BULLISH ✅ — EMAs stacked 8>13>21>34>55>89' if m5.get('ribbon_bull') else 'BEARISH ✅' if m5.get('ribbon_bear') else 'MIXED ❌'}
H1 Ribbon:  {'BULLISH ✅' if data.get('h1',{}).get('ribbon_bull') else 'BEARISH ✅' if data.get('h1',{}).get('ribbon_bear') else 'MIXED ❌'}
Spread:     {m5.get('spread',0):.2f} pts ({'EXPANDING ✅' if m5.get('spread_wide') else 'CONTRACTING ⚠️'})
ADX:        {m5.get('adx',0):.1f} ({'trending ✅' if m5.get('adx',0) >= MIN_ADX else 'weak ❌'})

━━━ PULLBACK ━━━
EMA8:  ${m5.get('e8',0):,.2f} | EMA13: ${m5.get('e13',0):,.2f} | EMA21: ${m5.get('e21',0):,.2f}
EMA34: ${m5.get('e34',0):,.2f} | EMA55: ${m5.get('e55',0):,.2f} | EMA89: ${m5.get('e89',0):,.2f}
Price: ${price:,.2f}
Touched ribbon: {scored.get('conditions',{}).get('price_at_ribbon', False)} | Bounced: {scored.get('conditions',{}).get('bounce_candle', False)}

━━━ CONFIRMATION ━━━
RSI14:  {m5.get('rsi14',50):.1f} | MACD: {'BULL' if m5.get('macd_bull') else 'BEAR'}
Volume: {m5.get('vol_ratio',1):.2f}× avg | Delta: {m5.get('delta',0):+.0f} {'🟢' if m5.get('delta',0)>0 else '🔴'}
Signal: {scored['signal']} score={scored['score']}/9 ({scored.get('quality','?')})

━━━ MACRO / DXY ━━━
{macro_line}
→ DXY rising = bearish for gold | DXY falling = bullish for gold

━━━ CONTEXT ━━━
News:       {news_str}
Recent P&L: {perf_str}

━━━ RULES ━━━
1. Both M5 AND H1 ribbons must be aligned — if H1 is mixed, WAIT
2. Ribbon must be EXPANDING not contracting — contracting = trend dying
3. Price must actually have touched the ribbon and bounced — not entering mid-air
4. ADX >= {MIN_ADX} — ribbon only works in trending markets
5. If news strongly opposes direction — WAIT

Respond JSON only:
{{
  "decision": "BUY"/"SELL"/"WAIT",
  "confidence": 0-100,
  "reason": "cite ribbon state + pullback quality, max 120 chars",
  "trade_quality": "A+/A/B/C",
  "concern": "one line if WAIT"
}}"""

    try:
        from bot.gemma_agent import agent_call, extract_json
        raw = await agent_call(prompt, max_tokens=300)
        if not raw:
            raise ValueError("No AI response")
        d = extract_json(raw)
        if not d or "decision" not in d:
            raise ValueError(f"No decision in response: {raw[:80]}")

        if d.get("trade_quality", "C") not in ("A+", "A") and d.get("decision") in ("BUY", "SELL"):
            d["decision"] = "WAIT"
            d["reason"]   = f"Quality {d.get('trade_quality','C')} — need A or A+"

        logger.info(
            f"AI Ribbon: {d.get('decision')} conf={d.get('confidence')}% "
            f"Q={d.get('trade_quality')} | {d.get('reason','')[:70]}"
        )
        return d
    except Exception as e:
        logger.warning(f"AI ribbon confirm error: {e}")
        return {
            "decision":     scored["signal"],
            "confidence":   50 + scored["score"] * 5,
            "reason":       scored.get("reason", "Fallback"),
            "trade_quality": scored.get("quality", "B"),
        }
