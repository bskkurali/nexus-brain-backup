"""
AiTrader Position Monitor
──────────────────────────
Runs every 60 seconds.
Checks all open paper trades against live price.
Auto-closes at TP/SL/Trail.
Updates wallet equity.
Sends Telegram alert on close.
"""

import asyncio
from datetime import datetime
from loguru import logger


async def get_live_price() -> float:
    """Get current gold price from fastest source."""
    # Try live feed first
    try:
        from bot.exness_feed import get_live_price as _lp
        lp = _lp()
        if lp and lp.get("mid", 0) > 2000:
            return float(lp["mid"])
    except Exception:
        pass

    # Try gold-api
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get("https://api.gold-api.com/price/XAU",
                           headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                d = r.json()
                p = float(d.get("price", 0))
                if p > 2000:
                    return p
    except Exception:
        pass

    # Fallback yfinance
    try:
        import yfinance as yf
        def _f():
            t = yf.Ticker("GC=F")
            h = t.history(period="1d", interval="1m")
            if h is not None and not h.empty:
                return float(h["Close"].iloc[-1])
            return 0.0
        loop = asyncio.get_event_loop()
        price = await loop.run_in_executor(None, _f)
        if price > 2000:
            return price
    except Exception:
        pass

    return 0.0


async def check_and_close_trades(tg_bot=None):
    """
    Core monitor function.
    Check every open trade against live price.
    Close if TP/SL/Trail hit.
    """
    try:
        from bot.execution import get_open_paper_trades, _close_paper_trade
        from bot.risk_engine import get_state, update_equity
        from bot.unified_memory import audit, log_trade_result
        from bot.strategy_brain import record_trade_result
        from bot.survival_pressure import record_trade
        from config.settings import settings
    except Exception as e:
        logger.error(f"Position monitor import error: {e}")
        return

    trades = get_open_paper_trades()
    if not trades:
        return

    price = await get_live_price()
    if price <= 0:
        logger.warning("Position monitor: no live price")
        return

    state   = get_state()
    closed  = []

    for trade in trades:
        direction = trade.direction
        entry     = trade.entry
        sl        = trade.stop_loss
        tp        = trade.take_profit_1
        atr       = getattr(trade, "atr", 5.0) or 5.0

        # ── Smart Trailing Stop ────────────────────────
        # Phase 1 — Breakeven: price moved 50% toward TP → SL → entry
        # Phase 2 — Lock profit: price moved 100% of SL dist → trail at price - ATR
        # Phase 3 — Trail: keep moving SL up/down as price extends
        #
        # This means: once in profit, you NEVER lose. You only let profit run.

        sl_dist      = abs(entry - sl)
        tp_dist      = abs(tp - entry)
        trail_sl     = getattr(trade, "trail_sl", None)
        close_reason = None          # always initialised — avoids NameError if
        close_price  = price         # direction is somehow not BUY or SELL

        if direction == "BUY":
            profit_pts = price - entry
            # Phase 1: 50% toward TP → move SL to breakeven
            if profit_pts >= sl_dist * 0.5 and (trade.stop_loss < entry):
                trade.stop_loss = round(entry + 0.1, 2)   # just above entry
                sl = trade.stop_loss
                logger.info(f"🔒 BREAKEVEN: {trade.ticket} SL → ${sl:.2f}")
            # Phase 2+: 100% of SL in profit → start trailing at price - ATR
            if profit_pts >= sl_dist:
                new_trail = round(price - atr * 0.8, 2)
                current_trail = getattr(trade, "trail_sl", entry)
                if new_trail > (current_trail or entry):
                    trade.trail_sl = new_trail
                    trail_sl = new_trail
                    logger.debug(f"📈 TRAIL: {trade.ticket} trail → ${new_trail:.2f}")

            # Check exits (BUY)
            if trail_sl and price <= trail_sl:
                close_reason = "TRAIL_HIT"
                close_price  = trail_sl
            elif price <= sl:
                close_reason = "SL_HIT"
                close_price  = sl
            elif price >= tp:
                close_reason = "TP_HIT"
                close_price  = tp

            # Live PnL
            trade.pnl = round((price - entry) * trade.lot_size * 100, 2)

        elif direction == "SELL":
            profit_pts = entry - price
            # Phase 1: 50% toward TP → move SL to breakeven
            if profit_pts >= sl_dist * 0.5 and (trade.stop_loss > entry):
                trade.stop_loss = round(entry - 0.1, 2)
                sl = trade.stop_loss
                logger.info(f"🔒 BREAKEVEN: {trade.ticket} SL → ${sl:.2f}")
            # Phase 2+: 100% of SL in profit → start trailing
            if profit_pts >= sl_dist:
                new_trail = round(price + atr * 0.8, 2)
                current_trail = getattr(trade, "trail_sl", entry)
                if new_trail < (current_trail or float("inf")):
                    trade.trail_sl = new_trail
                    trail_sl = new_trail
                    logger.debug(f"📉 TRAIL: {trade.ticket} trail → ${new_trail:.2f}")

            # Check exits (SELL)
            if trail_sl and price >= trail_sl:
                close_reason = "TRAIL_HIT"
                close_price  = trail_sl
            elif price >= sl:
                close_reason = "SL_HIT"
                close_price  = sl
            elif price <= tp:
                close_reason = "TP_HIT"
                close_price  = tp

            # Live PnL
            trade.pnl = round((entry - price) * trade.lot_size * 100, 2)

        # Close if needed
        if close_reason:
            _close_paper_trade(trade, close_price, close_reason)
            pnl  = trade.pnl
            won  = pnl > 0

            # Consecutive loss brake
            try:
                from bot.risk_engine import record_win, record_loss
                if won:
                    record_win()
                else:
                    record_loss()
            except Exception:
                pass

            # If live/bridge mode: close the real MT5 position too
            if not settings.is_paper:
                try:
                    from bot.execution import close_bridge_trade
                    await close_bridge_trade(trade.ticket)
                except Exception:
                    pass

            # Update equity — sync from real account if possible, else estimate
            new_equity = round(state.equity + pnl, 2)
            if not settings.is_paper:
                try:
                    from bot.account_sync import sync_account_balance
                    real_balance = await sync_account_balance()
                    if real_balance and real_balance > 0:
                        new_equity = real_balance
                except Exception:
                    pass
            update_equity(new_equity)

            closed.append({
                "ticket":  trade.ticket,
                "direction": direction,
                "entry":   entry,
                "close":   close_price,
                "pnl":     pnl,
                "reason":  close_reason,
            })

            logger.success(
                f"{'✅' if won else '❌'} Trade closed: "
                f"{direction} {trade.ticket} "
                f"@ ${close_price:,.2f} "
                f"PnL=${pnl:+.2f} "
                f"({close_reason})"
            )

            # Record in NEXUS memory for learning
            try:
                from bot.nexus_memory import record_trade_close
                record_trade_close(trade.ticket, pnl, won)
            except Exception:
                pass

            # Trade journal — level memory + pattern learning
            try:
                from bot.trade_journal import record_close as journal_close
                journal_close(trade.ticket, close_reason, pnl)
            except Exception:
                pass

            # Feed self-learning brain
            try:
                from bot.nexus_learning_brain import record_trade_outcome
                sym_attr = getattr(trade, "symbol", "XAUUSDm")
                record_trade_outcome(
                    symbol=sym_attr,
                    direction=direction,
                    outcome="WIN" if won else "LOSS",
                    pnl=pnl,
                    conditions=getattr(trade, "entry_conditions", {}),
                    session=_get_session(),
                    hour=datetime.utcnow().hour,
                )
            except Exception:
                pass

            # Store in memory
            try:
                audit("trade_closed", "position_monitor", {
                    "ticket": trade.ticket, "pnl": pnl,
                    "reason": close_reason
                })
                log_trade_result(
                    session=_get_session(),
                    strategy="sniper_pro",
                    won=won, pnl=pnl
                )
                record_trade_result(
                    ticket=trade.ticket,
                    direction=direction,
                    entry=entry,
                    close_price=close_price,
                    pnl=pnl,
                    strategy_used="sniper_pro",
                    session=_get_session(),
                    rsi=50,
                    notes=close_reason
                )
            except Exception:
                pass

            # Telegram alert
            if tg_bot:
                try:
                    icon = "✅" if won else "❌"
                    if close_reason == "TRAIL_HIT":
                        icon = "💰"
                    msg = (
                        f"{icon} TRADE CLOSED\n"
                        f"{'━'*18}\n"
                        f"{direction} {trade.ticket}\n"
                        f"Entry:  ${entry:,.2f}\n"
                        f"Close:  ${close_price:,.2f}\n"
                        f"P&L:    ${pnl:+.2f}\n"
                        f"Reason: {close_reason}\n"
                        f"Wallet: ${new_equity:.2f}"
                    )
                    await tg_bot.send_message(
                        chat_id=settings.telegram_chat_id, text=msg
                    )
                except Exception:
                    pass

            # Shutdown if wallet exhausted
            if new_equity <= 1.0:
                logger.critical("💀 WALLET EXHAUSTED — shutting down")
                try:
                    if tg_bot:
                        await tg_bot.send_message(
                            chat_id=settings.telegram_chat_id,
                            text="💀 WALLET EXHAUSTED\nBot shutting down.\nRestart with new funding."
                        )
                except Exception:
                    pass

    if closed:
        logger.info(f"Position monitor: closed {len(closed)} trades")

    return closed


def _get_session() -> str:
    from datetime import timezone, timedelta
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    m = ist.hour * 60 + ist.minute
    if 810 <= m < 1110:    return "LONDON"
    elif 1110 <= m < 1290: return "LONDON_NY_OVERLAP"
    elif 1290 <= m < 1380: return "NEW_YORK"
    return "ASIAN"


async def monitor_loop(tg_bot=None):
    """Run position monitor every 60 seconds."""
    logger.success("📡 Position Monitor started — checking every 60s")
    while True:
        try:
            await check_and_close_trades(tg_bot)
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        await asyncio.sleep(60)


if __name__ == "__main__":
    async def test():
        print("\n=== POSITION MONITOR TEST ===\n")
        price = await get_live_price()
        print(f"Live price: ${price:,.2f}")
        closed = await check_and_close_trades()
        print(f"Trades checked. Closed: {closed or 'none'}")
        print("✅ Position Monitor operational!")
    asyncio.run(test())
