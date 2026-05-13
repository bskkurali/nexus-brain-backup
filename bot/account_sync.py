"""
AiTrader Account Sync
─────────────────────
Fetches real account balance from Exness MT5 via bridge.
Falls back to .env STARTING_EQUITY if bridge unavailable.
Auto-updates risk engine equity on startup and every 30 min.
"""

import asyncio
import httpx
from loguru import logger
from config.settings import settings
from bot.risk_engine import update_equity, get_state


async def fetch_mt5_balance() -> float | None:
    """Fetch real account balance via MT5 bridge on VPS."""
    if not settings.mt5_bridge_url or "YOUR_VPS" in settings.mt5_bridge_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.mt5_bridge_url}/account",
                headers={"Authorization": f"Bearer {settings.mt5_bridge_token}"},
            )
            data = resp.json()
            balance = float(data.get("balance", 0))
            if balance > 0:
                logger.info(f"MT5 account balance fetched: ${balance:.2f}")
                return balance
    except Exception as e:
        logger.debug(f"MT5 bridge not available: {e}")
    return None


async def fetch_exness_balance_direct() -> float | None:
    """
    Direct MT5 connection — only works on Windows VPS with MT5 installed.
    On Mac this will always return None gracefully.
    """
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        # Try login — may return False if terminal already logged in (that's ok)
        mt5.login(int(settings.mt5_login), settings.mt5_password, settings.mt5_server)
        info = mt5.account_info()
        if info:
            balance = float(info.balance)
            equity = float(info.equity)
            logger.info(
                f"Exness account connected: "
                f"Balance=${balance:.2f} Equity=${equity:.2f} "
                f"Login={settings.mt5_login}"
            )
            mt5.shutdown()
            return equity
        mt5.shutdown()
    except ImportError:
        pass  # MT5 not available on Mac — expected
    except Exception as e:
        logger.debug(f"MT5 direct connection failed: {e}")
    return None


def get_starting_equity_from_env() -> float:
    """Read STARTING_EQUITY from .env, fallback to 1000."""
    try:
        from dotenv import dotenv_values
        env = dotenv_values(".env")
        val = env.get("STARTING_EQUITY", "")
        if val:
            return float(val)
    except Exception:
        pass
    return 1000.0


async def sync_account_balance() -> float:
    """
    Main sync function — tries all sources in order:
    1. MT5 direct (Windows VPS only)
    2. MT5 bridge (Mac → VPS REST API)
    3. STARTING_EQUITY from .env
    """
    # 1. Bridge mode � skip direct MT5 to avoid double mt5.initialize() conflict
    if settings.use_mt5_bridge:
        balance = await fetch_mt5_balance()
    else:
        balance = await fetch_exness_balance_direct()
    if balance:
        update_equity(balance)
        logger.success(f"Account synced from bridge: ${balance:.2f}")
        return balance

    # 3. Bridge unavailable — keep current equity, don't reset paper gains
    current = get_state().equity
    if current > 1.0:
        logger.debug(f"Bridge unavailable — keeping current equity ${current:.2f}")
        return current

    # 4. First startup only — nothing set yet, use .env
    balance = get_starting_equity_from_env()
    update_equity(balance)
    logger.info(f"Using STARTING_EQUITY from .env: ${balance:.2f}")
    return balance


async def start_balance_sync_loop():
    """Sync balance on startup then every 30 minutes."""
    balance = await sync_account_balance()
    logger.info(f"Initial balance set: ${balance:.2f}")
    while True:
        await asyncio.sleep(1800)  # 30 minutes
        await sync_account_balance()


if __name__ == "__main__":
    asyncio.run(sync_account_balance())
