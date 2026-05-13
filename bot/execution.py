"""
Execution Engine
────────────────
Unified interface for trade execution.
  PAPER mode  → logs virtual trades, tracks P&L internally
  BRIDGE mode → calls MT5 REST bridge on Windows VPS
  NATIVE mode → calls MT5 Python lib directly (VPS only)
"""

import uuid
import json
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, asdict, field
from loguru import logger
import httpx

from config.settings import settings
from bot.risk_engine import TradeParams, record_trade_result


@dataclass
class OrderResult:
    success: bool
    ticket: str
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    lot_size: float
    mode: str           # paper | bridge | native
    message: str = ""
    opened_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class PaperTrade:
    ticket: str
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    lot_size: float
    open_time: str
    current_price: float = 0.0
    pnl: float = 0.0
    status: str = "OPEN"   # OPEN | CLOSED | SL_HIT | TP_HIT
    close_price: float = 0.0
    close_time: str = ""


# ── Paper Trade Store ──────────────────────────────────
_paper_trades: dict[str, PaperTrade] = {}


def get_paper_trades() -> list[PaperTrade]:
    return list(_paper_trades.values())


def get_open_paper_trades() -> list[PaperTrade]:
    return [t for t in _paper_trades.values() if t.status == "OPEN"]


def update_paper_pnl(current_price: float):
    """Update unrealised P&L for open paper trades."""
    for trade in get_open_paper_trades():
        if trade.direction == "BUY":
            trade.pnl = round((current_price - trade.entry) * trade.lot_size * 100, 2)
        else:
            trade.pnl = round((trade.entry - current_price) * trade.lot_size * 100, 2)
        trade.current_price = current_price

        # Auto-close on SL/TP hit
        if trade.direction == "BUY":
            if current_price <= trade.stop_loss:
                _close_paper_trade(trade, current_price, "SL_HIT")
            elif current_price >= trade.take_profit_1:
                _close_paper_trade(trade, current_price, "TP_HIT")
        else:
            if current_price >= trade.stop_loss:
                _close_paper_trade(trade, current_price, "SL_HIT")
            elif current_price <= trade.take_profit_1:
                _close_paper_trade(trade, current_price, "TP_HIT")


def _close_paper_trade(trade, close_price: float, reason: str):
    # Guard: never double-close a trade
    if getattr(trade, "status", "OPEN") != "OPEN":
        logger.debug(f"Skip close {getattr(trade,'ticket','?')} — already {trade.status}")
        return
    trade.status = reason
    trade.close_price = close_price
    trade.close_time = datetime.now().isoformat()
    if trade.direction == "BUY":
        trade.pnl = round((close_price - trade.entry) * trade.lot_size * 100, 2)
    else:
        trade.pnl = round((trade.entry - close_price) * trade.lot_size * 100, 2)
    record_trade_result(trade.pnl)
    logger.info(f"Paper trade {reason}: ticket={trade.ticket} pnl={trade.pnl}")


# ── Paper Execution ────────────────────────────────────

def _execute_paper(params: TradeParams) -> OrderResult:
    ticket = f"PAPER-{str(uuid.uuid4())[:8].upper()}"
    trade = PaperTrade(
        ticket=ticket,
        symbol=params.symbol,
        direction=params.direction,
        entry=params.entry,
        stop_loss=params.stop_loss,
        take_profit_1=params.take_profit_1,
        take_profit_2=params.take_profit_2,
        lot_size=params.lot_size,
        open_time=datetime.now().isoformat(),
        current_price=params.entry,
    )
    _paper_trades[ticket] = trade
    logger.info(
        f"[PAPER] {params.direction} {params.symbol} "
        f"entry={params.entry} sl={params.stop_loss} "
        f"tp={params.take_profit_1} lot={params.lot_size}"
    )
    return OrderResult(
        success=True,
        ticket=ticket,
        symbol=params.symbol,
        direction=params.direction,
        entry=params.entry,
        stop_loss=params.stop_loss,
        take_profit=params.take_profit_1,
        lot_size=params.lot_size,
        mode="paper",
        message="Paper trade opened",
    )


# ── Bridge Execution (Mac → VPS MT5) ──────────────────

async def _execute_bridge(params: TradeParams) -> OrderResult:
    """Send order to MT5 REST bridge running on Windows VPS."""
    payload = {
        "symbol": params.symbol,
        "direction": params.direction,
        "lot": params.lot_size,
        "sl": params.stop_loss,
        "tp": params.take_profit_1,
        "comment": f"AiBot_{params.session}",
    }
    headers = {"Authorization": f"Bearer {settings.mt5_bridge_token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.mt5_bridge_url}/place_order",
                json=payload,
                headers=headers,
            )
            data = resp.json()
            if data.get("success"):
                ticket = str(data.get("order") or data.get("ticket") or "")
                logger.info(f"[BRIDGE] Order placed: ticket={ticket}")
                return OrderResult(
                    success=True,
                    ticket=ticket,
                    symbol=params.symbol,
                    direction=params.direction,
                    entry=data.get("price", params.entry),
                    stop_loss=params.stop_loss,
                    take_profit=params.take_profit_1,
                    lot_size=params.lot_size,
                    mode="bridge",
                    message="Live order via MT5 bridge",
                )
            else:
                err = data.get("error", "Unknown bridge error")
                logger.error(f"[BRIDGE] Order failed: {err}")
                return OrderResult(
                    success=False, ticket="", symbol=params.symbol,
                    direction=params.direction, entry=params.entry,
                    stop_loss=params.stop_loss, take_profit=params.take_profit_1,
                    lot_size=params.lot_size, mode="bridge", message=err,
                )
    except Exception as e:
        logger.error(f"[BRIDGE] Connection error: {e}")
        return OrderResult(
            success=False, ticket="", symbol=params.symbol,
            direction=params.direction, entry=params.entry,
            stop_loss=params.stop_loss, take_profit=params.take_profit_1,
            lot_size=params.lot_size, mode="bridge",
            message=f"Bridge unreachable: {e}",
        )


async def close_bridge_trade(ticket: str) -> bool:
    """Close a live trade via bridge."""
    headers = {"Authorization": f"Bearer {settings.mt5_bridge_token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.mt5_bridge_url}/close_order",
                json={"ticket": ticket},
                headers=headers,
            )
            return resp.json().get("success", False)
    except Exception as e:
        logger.error(f"Bridge close failed: {e}")
        return False


async def get_bridge_positions() -> list[dict]:
    """Get open positions via bridge."""
    headers = {"Authorization": f"Bearer {settings.mt5_bridge_token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.mt5_bridge_url}/positions",
                headers=headers,
            )
            return resp.json().get("positions", [])
    except Exception as e:
        logger.error(f"Bridge positions failed: {e}")
        return []


# ── Unified Execute ────────────────────────────────────

def _validate_xauusd(params: TradeParams) -> str:
    """
    Returns error string if params are invalid, empty string if OK.
    Catches: BTC prices used for gold (~$80k), tiny values (~$2), wrong direction stops.
    """
    p, sl, tp = params.entry, params.stop_loss, params.take_profit_1
    # XAUUSD valid price range — gold won't be below $1000 or above $15000 anytime soon
    for label, val in [("entry", p), ("SL", sl), ("TP", tp)]:
        if not (1000 < val < 15000):
            return f"Invalid {label}=${val:.2f} — outside XAUUSD range (got BTC/other price?)"
    # SL must be on the correct side of entry
    if params.direction == "BUY" and sl >= p:
        return f"BUY SL=${sl:.2f} >= entry=${p:.2f}"
    if params.direction == "SELL" and sl <= p:
        return f"SELL SL=${sl:.2f} <= entry=${p:.2f}"
    # TP must be on the correct side
    if params.direction == "BUY" and tp <= p:
        return f"BUY TP=${tp:.2f} <= entry=${p:.2f}"
    if params.direction == "SELL" and tp >= p:
        return f"SELL TP=${tp:.2f} >= entry=${p:.2f}"
    # Minimum SL distance (0.5 points — MT5 requires min distance from price)
    if abs(p - sl) < 0.5:
        return f"SL too close: {abs(p-sl):.2f} pts (min 0.5)"
    return ""


def _is_gold_market_open() -> bool:
    """Gold trades Sun 22:00 UTC → Fri 21:00 UTC. Returns False on weekends."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    wd = now.weekday()   # 0=Mon … 6=Sun
    h  = now.hour
    # Saturday all day = closed
    if wd == 5:
        return False
    # Sunday before 22:00 UTC = closed
    if wd == 6 and h < 22:
        return False
    # Friday after 21:00 UTC = closed
    if wd == 4 and h >= 21:
        return False
    return True


async def execute_trade(params: TradeParams) -> OrderResult:
    """
    Main entry point — routes to paper/bridge/native based on config.
    """
    # ── Sanity checks before any execution ────────────
    err = _validate_xauusd(params)
    if err:
        logger.error(f"Trade REJECTED — invalid params: {err}")
        return OrderResult(
            success=False, ticket="", symbol=params.symbol,
            direction=params.direction, entry=params.entry,
            stop_loss=params.stop_loss, take_profit=params.take_profit_1,
            lot_size=params.lot_size, mode="rejected", message=err,
        )

    if not settings.is_paper and not _is_gold_market_open():
        logger.warning("Trade REJECTED — gold market is closed (weekend/after-hours)")
        return OrderResult(
            success=False, ticket="", symbol=params.symbol,
            direction=params.direction, entry=params.entry,
            stop_loss=params.stop_loss, take_profit=params.take_profit_1,
            lot_size=params.lot_size, mode="rejected", message="Market closed",
        )

    if settings.is_paper:
        return _execute_paper(params)
    elif settings.use_mt5_bridge:
        return await _execute_bridge(params)
    else:
        # Native MT5 — only available on Windows VPS
        try:
            import MetaTrader5 as mt5
            return await _execute_native_mt5(params, mt5)
        except ImportError:
            logger.error("MetaTrader5 library not available on Mac. Use paper or bridge mode.")
            return OrderResult(
                success=False, ticket="", symbol=params.symbol,
                direction=params.direction, entry=params.entry,
                stop_loss=params.stop_loss, take_profit=params.take_profit_1,
                lot_size=params.lot_size, mode="native",
                message="MT5 not available — switch to bridge mode on Mac",
            )


async def _execute_native_mt5(params: TradeParams, mt5) -> OrderResult:
    """Direct MT5 execution — Windows VPS only."""
    action = mt5.ORDER_TYPE_BUY if params.direction == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": params.symbol,
        "volume": params.lot_size,
        "type": action,
        "sl": params.stop_loss,
        "tp": params.take_profit_1,
        "comment": f"AiBot_{params.session}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return OrderResult(
            success=True, ticket=str(result.order),
            symbol=params.symbol, direction=params.direction,
            entry=result.price, stop_loss=params.stop_loss,
            take_profit=params.take_profit_1, lot_size=params.lot_size,
            mode="native", message="Live MT5 order placed",
        )
    err = f"MT5 error: {result.retcode if result else 'no result'}"
    return OrderResult(
        success=False, ticket="", symbol=params.symbol,
        direction=params.direction, entry=params.entry,
        stop_loss=params.stop_loss, take_profit=params.take_profit_1,
        lot_size=params.lot_size, mode="native", message=err,
    )
