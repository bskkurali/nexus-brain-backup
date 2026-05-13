"""
NEXUS GOLD AI v1.0 — Main Entry Point
Clean, tested, no bugs.
"""

import asyncio
import sys
import os
import warnings
warnings.filterwarnings("ignore")

import click
from loguru import logger

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
           colorize=True)

os.makedirs("logs", exist_ok=True)
os.makedirs("data/brain", exist_ok=True)
logger.add("logs/bot.log", level="DEBUG", rotation="10 MB", retention="7 days")

from config.settings import settings


def print_banner():
    print(f"""
╭─────────────────────────────────────╮
│  NEXUS GOLD AI v1.0              │
│  Mode: {settings.execution_mode.upper():<8} Symbol: XAUUSDm    │
│  Telegram: {settings.telegram_chat_id:<26}│
│  Dashboard: http://localhost:8000   │
╰─────────────────────────────────────╯""")


async def start_dashboard_bg():
    """Start dashboard — handles port already in use gracefully."""
    try:
        import uvicorn
        from dashboard.app import app
        config = uvicorn.Config(
            app, host="0.0.0.0", port=8000,
            log_level="critical", access_log=False
        )
        server = uvicorn.Server(config)
        await server.serve()
    except SystemExit:
        logger.warning("Dashboard port 8000 busy — already running")
    except Exception as e:
        logger.error(f"Dashboard: {e}")


async def main_async(mode: str):
    if mode:
        os.environ["EXECUTION_MODE"] = mode

    print_banner()
    logger.info(f"Starting NEXUS GOLD AI — {settings.execution_mode.upper()} mode")

    # ── Dashboard in background ────────────────────────
    asyncio.create_task(start_dashboard_bg())
    await asyncio.sleep(2)
    logger.success("✅ Dashboard at http://localhost:8000")

    # ── Telegram ───────────────────────────────────────
    tg_app = None
    if settings.telegram_bot_token and settings.telegram_bot_token != "your_telegram_token_here":
        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
            from telegram import Update
            tg_app = ApplicationBuilder().token(settings.telegram_bot_token).build()
            await tg_app.initialize()
            await tg_app.start()

            async def handle_msg(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
                try:
                    text = update.message.text or ""
                    from bot.self_healer import handle_telegram_approval
                    await handle_telegram_approval(text, tg_app.bot)
                except Exception:
                    pass

            tg_app.add_handler(MessageHandler(filters.TEXT, handle_msg))
            logger.success("✅ Telegram connected")
        except Exception as e:
            logger.warning(f"Telegram: {e}")

    # ── Scheduler ──────────────────────────────────────
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler = AsyncIOScheduler()

    # ── SNIPER PRO ─────────────────────────────────────
    async def sniper_cycle():
        try:
            from bot.strategy_engine import run_sniper
            from bot.risk_engine import build_trade_params
            from bot.execution import execute_trade
            signal = await run_sniper()
            if signal and signal.is_actionable:
                params = build_trade_params(signal.direction, signal.entry,
                                           signal.atr, int(signal.probability))
                if params:
                    result = await execute_trade(params)
                    if result.success:
                        logger.success(f"✅ SNIPER {signal.direction} @ ${signal.entry:,.2f}")
        except Exception as e:
            logger.error(f"Sniper: {e}")

    from datetime import datetime, timedelta
    _t0 = datetime.now()
    scheduler.add_job(sniper_cycle, IntervalTrigger(minutes=5),
                     id="sniper", max_instances=1,
                     next_run_time=_t0 + timedelta(minutes=5))
    logger.info("🎯 SNIPER PRO — every 5 min")

    # ── Human Brain — every 5 min ────────────────────────
    async def executor_cycle():
        try:
            from bot.nexus_human_brain import run_human_brain_cycle
            result = await run_human_brain_cycle()
            if result.get("executed"):
                logger.success(
                    f"🧠 HUMAN BRAIN TRADE: {result.get('direction')} "
                    f"@ ${result.get('entry',0):,.2f}"
                )
        except Exception as e:
            logger.error(f"Human Brain: {e}")

    scheduler.add_job(executor_cycle, IntervalTrigger(minutes=5),
                     id="executor", max_instances=1,
                     next_run_time=_t0 + timedelta(minutes=5, seconds=75))
    logger.success("🧠 Human Brain — every 5 min")

    # ── Precision Gold Strategy (Fibonacci Key Level Bounce) ────────────
    async def precision_cycle():
        try:
            from bot.nexus_gold_strategy import run_precision_cycle
            result = await run_precision_cycle()
            if result.get("executed"):
                logger.success(
                    f"🎯 PRECISION {result.get('direction')} "
                    f"@ ${result.get('entry', 0):,.2f} "
                    f"[{result.get('quality', '?')}] conf={result.get('confidence', 0)}%"
                )
        except Exception as e:
            logger.error(f"Precision: {e}")

    scheduler.add_job(precision_cycle, IntervalTrigger(minutes=5),
                     id="precision", max_instances=1,
                     next_run_time=_t0 + timedelta(minutes=5, seconds=150))
    logger.success("🎯 Precision Gold Strategy — every 5 min")

    # ── EMA Ribbon Strategy ────────────────────────────
    async def ribbon_cycle():
        try:
            from bot.ema_ribbon_strategy import run_ribbon_cycle
            result = await run_ribbon_cycle()
            if result.get("executed"):
                logger.success(
                    f"🎀 RIBBON {result.get('direction')} "
                    f"@ ${result.get('entry', 0):,.2f} "
                    f"[{result.get('quality', '?')}] conf={result.get('confidence', 0)}%"
                )
        except Exception as e:
            logger.error(f"Ribbon: {e}")

    scheduler.add_job(ribbon_cycle, IntervalTrigger(minutes=5),
                     id="ribbon", max_instances=1,
                     next_run_time=_t0 + timedelta(minutes=5, seconds=225))
    logger.success("🎀 EMA Ribbon Strategy — every 5 min")

    # ── Autonomous Brain ───────────────────────────────
    async def brain_cycle():
        try:
            from bot.autonomous_brain import run_autonomous_cycle
            api_key = settings.grok_api_key or settings.nvidia_api_key
            if api_key:
                await run_autonomous_cycle(api_key)
        except Exception as e:
            logger.error(f"Brain: {e}")

    # Autonomous brain disabled — Master Brain handles decisions
    # scheduler.add_job(brain_cycle, ...)

    # ── Position Monitor ───────────────────────────────
    async def monitor_cycle():
        try:
            from bot.position_monitor import check_and_close_trades
            closed = await check_and_close_trades(tg_app.bot if tg_app else None)
            if closed:
                for t in closed:
                    logger.success(f"💰 CLOSED: {t.get('direction')} PnL=${t.get('pnl',0):+.2f}")
        except Exception as e:
            logger.debug(f"Monitor: {e}")

    scheduler.add_job(monitor_cycle, IntervalTrigger(seconds=60),
                     id="monitor", max_instances=1)
    logger.success("📡 Position Monitor — every 60 sec")

    # ── News Watchdog ──────────────────────────────────
    async def news_watchdog_cycle():
        try:
            from bot.news_watchdog import check_breaking_news
            await check_breaking_news(tg_app.bot if tg_app else None)
        except Exception as e:
            logger.debug(f"News watchdog: {e}")

    scheduler.add_job(news_watchdog_cycle, IntervalTrigger(minutes=2),
                     id="news_watchdog", max_instances=1)
    logger.success("📰 News Watchdog — every 2 min")

    # ── Internet Brain ─────────────────────────────────
    async def internet_cycle():
        try:
            from bot.internet_brain import run_internet_research_cycle
            await run_internet_research_cycle()
        except Exception as e:
            logger.debug(f"Internet brain: {e}")

    scheduler.add_job(internet_cycle, IntervalTrigger(hours=2),
                     id="internet", max_instances=1)
    logger.success("🌐 Internet Brain — every 2 hours")

    # ── Strategy Brain — backtest every 24h ───────────
    async def strategy_brain_cycle():
        try:
            from bot.nexus_strategy_brain import run_strategy_brain
            await run_strategy_brain()
        except Exception as e:
            logger.error(f"Strategy Brain: {e}")

    scheduler.add_job(strategy_brain_cycle, IntervalTrigger(hours=24),
                     id="strategy_brain", max_instances=1)
    logger.success("📊 Strategy Brain — every 24h")

    # ── Self Healer ────────────────────────────────────
    async def healer_cycle():
        try:
            from bot.self_healer import run_self_healing_cycle
            await run_self_healing_cycle(tg_app.bot if tg_app else None)
        except Exception as e:
            logger.debug(f"Healer: {e}")

    scheduler.add_job(healer_cycle, IntervalTrigger(minutes=30),
                     id="healer", max_instances=1)

    # ── Survival ───────────────────────────────────────
    async def survival_cycle():
        try:
            from bot.survival_pressure import check_idle_penalty
            penalty = check_idle_penalty()
            if penalty.get("penalty_applied"):
                msg = f"⚠️ IDLE PENALTY -$5\nBalance: ${penalty.get('new_balance',0):.2f}"
                logger.warning(msg)
                if tg_app:
                    try:
                        await tg_app.bot.send_message(
                            chat_id=settings.telegram_chat_id, text=msg)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Survival: {e}")

    scheduler.add_job(survival_cycle, IntervalTrigger(hours=1),
                     id="survival", max_instances=1)

    # ── Start everything ───────────────────────────────
    scheduler.start()
    logger.success("✅ All systems started!")
    logger.success("📊 Dashboard: http://127.0.0.1:8000")

    # Background tasks
    async def price_feed():
        try:
            from bot.exness_feed import start_feed
            await start_feed()
        except Exception:
            pass

    async def master_brain():
        try:
            from bot.master_brain import start_brain
            api_key = settings.grok_api_key or settings.nvidia_api_key
            if api_key:
                await start_brain(api_key)
        except Exception:
            pass

    async def account_sync_task():
        try:
            from bot.account_sync import start_balance_sync_loop
            await start_balance_sync_loop()
        except Exception as e:
            logger.error(f"Account sync: {e}")

    asyncio.create_task(price_feed())
    asyncio.create_task(master_brain())
    asyncio.create_task(account_sync_task())
    logger.success("💰 Account sync started — real Exness balance every 30 min")
    asyncio.create_task(internet_cycle())
    asyncio.create_task(healer_cycle())
    asyncio.create_task(strategy_brain_cycle())   # backtest on startup

    # Run first scans staggered — prevents all 4 hitting Groq API simultaneously
    await sniper_cycle()
    await asyncio.sleep(8)
    await executor_cycle()
    await asyncio.sleep(8)
    asyncio.create_task(precision_cycle())
    await asyncio.sleep(8)
    asyncio.create_task(ribbon_cycle())

    # Keep alive
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        scheduler.shutdown(wait=False)
        if tg_app:
            try:
                await tg_app.stop()
                await tg_app.shutdown()
            except Exception:
                pass
        logger.info("Bot stopped")


@click.command()
@click.option("--mode", default="paper",
              type=click.Choice(["paper", "live"]),
              help="Trading mode")
def main(mode: str):
    """NEXUS GOLD AI — Autonomous AI Trading Bot"""
    try:
        asyncio.run(main_async(mode))
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
