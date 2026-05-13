"""
AiTrader Sniper Executor v2 - Clean version
Direct AI trade execution. No complex imports.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from loguru import logger


def _get_session() -> str:
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    m = ist.hour * 60 + ist.minute
    if 810 <= m < 1110:    return "LONDON"
    elif 1110 <= m < 1290: return "LONDON_NY_OVERLAP"
    elif 1290 <= m < 1380: return "NEW_YORK"
    elif 750 <= m < 810:   return "PRE_LONDON"
    return "ASIAN"


async def get_live_price() -> float:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.gold-api.com/price/XAU",
                           headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                p = float(r.json().get("price", 0))
                if p > 2000:
                    return p
    except Exception:
        pass
    return 0.0


async def get_market_snapshot() -> dict:
    import yfinance as yf
    import pandas as pd

    try:
        def _f():
            df = yf.download("GC=F", period="3d", interval="5m",
                            progress=False, auto_adjust=True)
            if df is None or df.empty:
                return None
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                         for c in df.columns]
            cl = df['close']; hi = df['high']; lo = df['low']; vl = df['volume']
            e9  = float(cl.ewm(span=9).mean().iloc[-1])
            e21 = float(cl.ewm(span=21).mean().iloc[-1])
            e50 = float(cl.ewm(span=50).mean().iloc[-1])
            d = cl.diff()
            g = d.clip(lower=0).ewm(14).mean().iloc[-1]
            l = (-d.clip(upper=0)).ewm(14).mean().iloc[-1]
            rsi = round(100 - (100 / (1 + (g / (l + 1e-9)))), 1)
            tr  = pd.concat([hi-lo, (hi-cl.shift()).abs(),
                            (lo-cl.shift()).abs()], axis=1).max(axis=1)
            atr = round(float(tr.ewm(14).mean().iloc[-1]), 2)
            vr  = round(float(vl.iloc[-1]) /
                       float(vl.rolling(20).mean().iloc[-1]), 2)
            price = float(cl.iloc[-1])
            h20 = float(hi.tail(20).max())
            l20 = float(lo.tail(20).min())
            return {
                "price": round(price, 2), "rsi": rsi, "atr": atr,
                "ema9": round(e9, 2), "ema21": round(e21, 2),
                "ema50": round(e50, 2), "volume_ratio": vr,
                "bull_trend": e9 > e21 > e50,
                "bear_trend": e9 < e21 < e50,
                "support": round(l20, 2), "resistance": round(h20, 2),
                "session": _get_session()
            }

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _f)
        return data or {}
    except Exception as e:
        logger.debug(f"Market snapshot error: {e}")
        price = await get_live_price()
        return {
            "price": price, "rsi": 55, "atr": 30,
            "session": _get_session(), "bull_trend": True,
            "bear_trend": False, "volume_ratio": 1.0,
            "ema9": price, "ema21": price - 5, "ema50": price - 15
        }


async def ai_trade_decision(market: dict, api_key: str) -> dict:
    from bot.nvidia_agent import nvidia_call

    price = market.get("price", 0)
    rsi   = market.get("rsi", 50)
    sess  = market.get("session", "")
    bull  = market.get("bull_trend", False)
    bear  = market.get("bear_trend", False)
    atr   = market.get("atr", 30)
    e9    = market.get("ema9", price)
    e21   = market.get("ema21", price)
    sup   = market.get("support", price - atr * 3)
    res   = market.get("resistance", price + atr * 3)
    vr    = market.get("volume_ratio", 1.0)

    # Get brain context
    brain_ctx = ""
    try:
        from bot.master_brain import get_brain
        thoughts = get_brain().get_recent_thoughts(3)
        brain_ctx = " | ".join([t.content[:60] for t in thoughts])
    except Exception:
        pass

    # Get internet brain context
    internet_ctx = ""
    try:
        from bot.internet_brain import get_internet_context
        internet_ctx = get_internet_context()[:300]
    except Exception:
        pass

    prompt = f"""You are an autonomous XAUUSD gold trader. May 2026.
Decide: BUY, SELL, or WAIT right now.

MARKET DATA:
Price: ${price:,.2f} | RSI: {rsi} | ATR: {atr:.2f}
EMA9: {e9:.2f} | EMA21: {e21:.2f}
Volume: {vr:.2f}x | Session: {sess}
Trend: {'BULLISH' if bull else 'BEARISH' if bear else 'SIDEWAYS'}
Support: ${sup:,.2f} | Resistance: ${res:,.2f}

BRAIN INSIGHTS: {brain_ctx}
{internet_ctx}

RULES:
- London/NY overlap = BEST time (active now if session shows it)
- RSI 40-70 = good entry zone
- RSI > 78 = overbought, consider SELL or WAIT
- RSI < 25 = oversold, consider BUY
- Bull trend + price > EMA9 = BUY bias
- Bear trend + price < EMA9 = SELL bias
- Protect capital first — a missed trade is better than a bad trade
- Wallet: $100 paper account

JSON only — no other text:
{{
  "decision": "BUY" or "SELL" or "WAIT",
  "confidence": 0-100,
  "reason": "specific reason max 100 chars",
  "entry": {price:.2f},
  "stop_loss": {round(price - atr*2, 2) if bull else round(price + atr*2, 2):.2f},
  "take_profit": {round(price + atr*6, 2) if bull else round(price - atr*6, 2):.2f}
}}"""

    raw = await nvidia_call(prompt, api_key, max_tokens=200)
    try:
        clean    = raw.replace("```json", "").replace("```", "").strip()
        decision = json.loads(clean)
        logger.info(
            f"AI decision: {decision.get('decision')} "
            f"conf={decision.get('confidence')}% | "
            f"{decision.get('reason','')[:60]}"
        )
        return decision
    except Exception:
        return {"decision": "WAIT", "confidence": 0, "reason": "Parse error"}


async def place_paper_trade(direction: str, price: float,
                             sl: float, tp: float,
                             atr: float, confidence: int,
                             entry_conditions: dict = None) -> dict:
    """
    Place a trade.
    Routes to real Exness MT5 when execution_mode != 'paper'.
    Falls back to paper simulation if bridge is unreachable.
    """
    from config.settings import settings

    ticket       = None
    actual_entry = price
    mode_label   = "PAPER"

    # ── Live / bridge execution ────────────────────────
    if not settings.is_paper:
        try:
            from bot.execution import execute_trade
            from bot.risk_engine import TradeParams, current_session

            params = TradeParams(
                symbol        = settings.mt5_symbol,
                direction     = direction,
                entry         = price,
                stop_loss     = sl,
                take_profit_1 = tp,
                take_profit_2 = tp,
                lot_size      = settings.mt5_default_lot,
                risk_amount   = round(abs(price - sl) * settings.mt5_default_lot * 100, 2),
                rr_ratio      = round(abs(tp - price) / (abs(price - sl) + 1e-9), 2),
                session       = current_session(),
            )
            result = await execute_trade(params)

            if result.success:
                ticket       = result.ticket
                actual_entry = result.entry
                srv          = settings.mt5_server.lower()
                mode_label   = "DEMO" if ("trial" in srv or "demo" in srv) else "LIVE"
                logger.success(
                    f"✅ [{mode_label}] {direction} @ ${actual_entry:,.2f} "
                    f"SL={sl:.2f} TP={tp:.2f} | ticket={ticket}"
                )
            else:
                logger.warning(
                    f"Bridge execution failed ({result.message}) "
                    f"— falling back to paper simulation"
                )
        except Exception as e:
            logger.warning(f"Live execution error: {e} — falling back to paper")

    # ── Paper simulation (or fallback) ────────────────
    if ticket is None:
        ticket     = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        mode_label = "PAPER"
        logger.success(
            f"✅ [PAPER] {direction} @ ${actual_entry:,.2f} "
            f"SL={sl:.2f} TP={tp:.2f} | {ticket}"
        )

    # Register in _paper_trades so position monitor tracks the trade
    try:
        from bot.execution import _paper_trades

        class _Trade:
            def __init__(self):
                self.ticket        = ticket
                self.symbol        = settings.mt5_symbol
                self.direction     = direction
                self.entry         = actual_entry
                self.stop_loss     = sl
                self.take_profit_1 = tp
                self.take_profit_2 = tp          # same as tp1 for paper trades
                self.lot_size      = settings.mt5_default_lot
                self.atr           = atr
                self.pnl           = 0.0
                self.status        = "OPEN"
                self.opened_at     = datetime.now().isoformat()
                self.mode          = mode_label
                # Fields set on close by position_monitor / _close_paper_trade
                self.close_price   = 0.0
                self.close_time    = ""
                self.trail_sl          = None
                self.entry_conditions  = entry_conditions or {}

        _paper_trades[ticket] = _Trade()
    except Exception as e:
        logger.warning(f"Execution store: {e} — trade logged only")

    # ── Register in nexus memory — fixes Trade Stats ──────
    try:
        from bot.nexus_memory import save_trade
        save_trade(
            ticket=ticket,
            direction=direction,
            entry=actual_entry,
            sl=sl,
            tp=tp,
            session=_get_session(),
            rsi=50,
            reason="AI signal",
            confidence=confidence,
        )
    except Exception:
        pass

    # Record in survival pressure
    try:
        from bot.survival_pressure import record_trade
        record_trade()
    except Exception:
        pass

    # Store in brain
    try:
        from bot.master_brain import get_brain
        get_brain().think(
            f"TRADE PLACED [{mode_label}]: {direction} @ ${actual_entry:,.2f} "
            f"conf={confidence}% SL={sl:.2f} TP={tp:.2f}",
            agent="sniper_executor",
            type_="trade_executed",
            icon="✅",
            importance=10
        )
    except Exception:
        pass

    # Update strategy brain
    try:
        from bot.strategy_brain import load_brain, save_brain
        brain = load_brain()
        brain["trade_history"].append({
            "ticket":     ticket,
            "direction":  direction,
            "entry":      actual_entry,
            "sl":         sl,
            "tp":         tp,
            "session":    _get_session(),
            "confidence": confidence,
            "timestamp":  datetime.now().isoformat(),
            "mode":       mode_label,
            "won":        None,
            "pnl":        None,
        })
        save_brain()
    except Exception:
        pass

    return {
        "executed":   True,
        "ticket":     ticket,
        "direction":  direction,
        "entry":      actual_entry,
        "sl":         sl,
        "tp":         tp,
        "lot_size":   settings.mt5_default_lot,
        "confidence": confidence,
        "mode":       mode_label,
    }


async def run_sniper_executor() -> dict:
    """Main executor — AI decides, bot executes."""
    from config.settings import settings
    api_key = settings.grok_api_key or settings.nvidia_api_key or ""

    if not api_key:
        logger.warning("No API key for executor")
        return {"executed": False}

    # Get market
    market = await get_market_snapshot()
    if not market or market.get("price", 0) < 2000:
        logger.warning("Executor: no valid price")
        return {"executed": False}

    # Use live price if available
    live = await get_live_price()
    if live > 2000:
        market["price"] = live

    # Check if already have open trade
    try:
        from bot.execution import _paper_trades
        open_trades = [t for t in _paper_trades.values()
                      if getattr(t, 'status', 'OPEN') == 'OPEN']
        if len(open_trades) >= 2:
            logger.info(f"Executor: {len(open_trades)} open trades — waiting")
            return {"executed": False, "reason": "Max trades reached"}
    except Exception:
        pass

    # AI decision
    decision = await ai_trade_decision(market, api_key)

    if decision.get("decision") not in ["BUY", "SELL"]:
        logger.info(f"Executor: WAIT — {decision.get('reason','')}")
        return {"executed": False, "reason": "AI said WAIT"}

    conf = decision.get("confidence", 0)
    if conf < 70:
        logger.info(f"Executor: confidence too low {conf}% (need 70%+)")
        return {"executed": False, "reason": f"Low confidence: {conf}%"}

    direction = decision["decision"]
    price     = market["price"]
    atr       = market.get("atr", 30)

    # Calculate SL/TP
    if direction == "BUY":
        sl = round(price - atr * 2.0, 2)
        tp = round(price + atr * 6.0, 2)
    else:
        sl = round(price + atr * 2.0, 2)
        tp = round(price - atr * 6.0, 2)

    # Use AI's levels if valid
    ai_sl = decision.get("stop_loss", 0)
    ai_tp = decision.get("take_profit", 0)
    if direction == "BUY" and ai_sl and ai_sl < price:
        sl = ai_sl
    if direction == "BUY" and ai_tp and ai_tp > price:
        tp = ai_tp
    if direction == "SELL" and ai_sl and ai_sl > price:
        sl = ai_sl
    if direction == "SELL" and ai_tp and ai_tp < price:
        tp = ai_tp

    # Execute
    result = await place_paper_trade(direction, price, sl, tp, atr, conf)

    if result.get("executed"):
        # Telegram alert
        try:
            import httpx
            msg = (
                f"🎯 AI TRADE EXECUTED\n"
                f"{'━'*20}\n"
                f"Direction: {direction}\n"
                f"Entry:  ${price:,.2f}\n"
                f"SL:     ${sl:,.2f}\n"
                f"TP:     ${tp:,.2f}\n"
                f"Conf:   {conf}%\n"
                f"Reason: {decision.get('reason','')[:60]}\n"
                f"Ticket: {result.get('ticket','?')}\n"
                f"Session: {market.get('session','?')}"
            )
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": settings.telegram_chat_id, "text": msg}
                )
        except Exception:
            pass

    return result


if __name__ == "__main__":
    async def test():
        print("\n=== SNIPER EXECUTOR TEST ===\n")
        result = await run_sniper_executor()
        print(f"Executed: {result.get('executed')}")
        if result.get('executed'):
            print(f"  {result['direction']} @ ${result['entry']:,.2f}")
            print(f"  SL: ${result['sl']:,.2f} | TP: ${result['tp']:,.2f}")
            print(f"  Confidence: {result['confidence']}%")
        else:
            print(f"  Reason: {result.get('reason','?')}")
    asyncio.run(test())
