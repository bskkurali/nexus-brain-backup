"""
Telegram Bot
────────────
Two-way Telegram integration.
Sends alerts + receives commands.
Only responds to your chat ID.
"""

import asyncio
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from loguru import logger

from config.settings import settings
from bot.risk_engine import get_state, resume_bot, circuit_breaker_check
from bot.execution import get_open_paper_trades, get_paper_trades


# ── Auth guard — only respond to your chat ID ──────────
def _auth(update: Update) -> bool:
    if str(update.effective_chat.id) != settings.telegram_chat_id:
        logger.warning(f"Unauthorized Telegram access from {update.effective_chat.id}")
        return False
    return True


# ── Commands ───────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    await update.message.reply_text(
        "🤖 *AiTrader Pro*\n\nBot is running\\.\n\n"
        "Commands:\n"
        "/status — bot state \\& equity\n"
        "/trades — open positions\n"
        "/report — today's P\\&L\n"
        "/pause — pause new entries\n"
        "/resume — re\\-enable bot\n"
        "/kill — close ALL trades\n"
        "/news — latest sentiment\n"
        "/help — all commands",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    state = get_state()
    paused, reason = circuit_breaker_check()
    status_icon = "🔴" if paused else "🟢"
    mode_badge = "📋 PAPER" if settings.is_paper else "💰 LIVE"
    open_trades = get_open_paper_trades()

    text = (
        f"{status_icon} *Bot Status*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Mode:       {mode_badge}\n"
        f"Status:     {'PAUSED — ' + reason if paused else 'Running'}\n"
        f"Equity:     ${state.equity:,.2f}\n"
        f"Daily P&L:  ${state.daily_pnl:+.2f}  ({state.daily_loss_pct:.1f}%)\n"
        f"Open trades: {len(open_trades)}\n"
        f"Trades today: {state.trades_today}/{settings.max_trades_per_day}\n"
        f"Time: {datetime.now().strftime('%H:%M IST')}"
    )
    await update.message.reply_text(text)


async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    trades = get_open_paper_trades()
    if not trades:
        await update.message.reply_text("📭 No open positions")
        return

    lines = ["📊 *Open Positions*\n━━━━━━━━━━━━━━━"]
    for t in trades:
        pnl_icon = "🟢" if t.pnl >= 0 else "🔴"
        lines.append(
            f"{pnl_icon} {t.direction} {t.symbol}\n"
            f"   Entry: ${t.entry:,.2f}  SL: ${t.stop_loss:,.2f}\n"
            f"   P&L: ${t.pnl:+.2f}  Lot: {t.lot_size}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    state = get_state()
    all_trades = get_paper_trades()
    closed = [t for t in all_trades if t.status != "OPEN"]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0

    text = (
        f"📊 *Daily Report — {datetime.now().strftime('%d %b %Y')}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Trades:    {len(closed)}  |  W: {len(wins)}  L: {len(losses)}\n"
        f"Win rate:  {win_rate}%\n"
        f"Net P&L:   ${state.daily_pnl:+.2f}\n"
        f"Daily DD:  {state.daily_loss_pct:.1f}%\n"
        f"Equity:    ${state.equity:,.2f}\n"
        f"Mode:      {'PAPER' if settings.is_paper else 'LIVE'}"
    )
    await update.message.reply_text(text)


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    from bot.risk_engine import _trigger_pause
    _trigger_pause("Manual pause via Telegram")
    await update.message.reply_text("⏸ Bot paused — no new entries\nSend /resume to re-enable")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    resume_bot()
    await update.message.reply_text("▶️ Bot resumed — watching for setups")


async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    from bot.risk_engine import _trigger_pause
    open_trades = get_open_paper_trades()
    count = len(open_trades)

    # Close all paper trades
    for trade in open_trades:
        trade.status = "CLOSED"
        trade.close_time = datetime.now().isoformat()

    _trigger_pause("Kill switch activated via Telegram")
    await update.message.reply_text(
        f"🛑 *KILL SWITCH ACTIVATED*\n"
        f"Closed {count} position(s)\n"
        f"Bot paused — send /resume to restart"
    )


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    await update.message.reply_text("📰 Fetching latest news signal...")
    try:
        from bot.news_engine import get_news_signal
        signal = await get_news_signal()
        icon = "🟢" if signal.bias == "BUY" else ("🔴" if signal.bias == "SELL" else "⚪")
        blackout_line = f"\n⚠️ BLACKOUT: {signal.blackout_event}" if signal.blackout else ""
        text = (
            f"{icon} *News Signal*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Sentiment: {signal.sentiment}  Score: {signal.score}\n"
            f"Bias:      {signal.bias}\n"
            f"Driver:    {signal.key_driver}\n"
            f"Confidence: {signal.confidence}/100\n"
            f"Trade OK:  {'Yes' if signal.trade_ok else 'No'}"
            f"{blackout_line}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ News fetch failed: {e}")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = (
        "🤖 *AiTrader Pro — Commands*\n"
        "━━━━━━━━━━━━━━━\n"
        "/status       Bot state, equity, P&L\n"
        "/trades       Open positions\n"
        "/report       Today's performance\n"
        "/news         Latest news sentiment\n"
        "/pause        Stop new entries\n"
        "/resume       Re-enable bot\n"
        "/kill         Close all trades + pause\n"
        "/help         This message"
    )
    await update.message.reply_text(text)


# ── Alert Senders ──────────────────────────────────────

async def send_alert(text: str, bot: Bot):
    """Send any alert message to your Telegram chat."""
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


async def alert_trade_opened(result, params, signal, bot: Bot):
    mode = "📋 PAPER" if settings.is_paper else "💰 LIVE"
    icon = "🟢" if params.direction == "BUY" else "🔴"
    text = (
        f"{icon} *TRADE OPENED — {params.symbol} {params.direction}* {mode}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Entry:      ${params.entry:,.2f}\n"
        f"SL:         ${params.stop_loss:,.2f}\n"
        f"TP1:        ${params.take_profit_1:,.2f}\n"
        f"TP2:        ${params.take_profit_2:,.2f}\n"
        f"Lot:        {params.lot_size}\n"
        f"R:R         1:{params.rr_ratio}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Confidence: {signal.confidence}/100\n"
        f"Driver:     {signal.key_driver}\n"
        f"Session:    {params.session}\n"
        f"Ticket:     {result.ticket}"
    )
    await send_alert(text, bot)


async def alert_circuit_breaker(reason: str, bot: Bot):
    text = (
        f"🛑 *CIRCUIT BREAKER TRIGGERED*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Reason: {reason}\n"
        f"Bot paused — send /resume to restart"
    )
    await send_alert(text, bot)


async def alert_blackout(event: str, minutes: int, bot: Bot):
    text = (
        f"⚠️ *BLACKOUT ACTIVE*\n"
        f"Event: {event}\n"
        f"In: {minutes} minutes\n"
        f"All entries paused"
    )
    await send_alert(text, bot)


async def send_daily_report(bot: Bot):
    state = get_state()
    all_trades = get_paper_trades()
    closed = [t for t in all_trades if t.status != "OPEN"]
    wins = [t for t in closed if t.pnl > 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0.0

    text = (
        f"📊 *Daily Report — {datetime.now().strftime('%d %b %Y')}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Trades:   {len(closed)}  W:{len(wins)} L:{len(closed)-len(wins)}\n"
        f"Win rate: {win_rate}%\n"
        f"Net P&L:  ${state.daily_pnl:+.2f}\n"
        f"Equity:   ${state.equity:,.2f}\n"
        f"DD today: {state.daily_loss_pct:.1f}%"
    )
    await send_alert(text, bot)


# ── Bot Runner ─────────────────────────────────────────

def build_application() -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("help", cmd_help))
    return app
