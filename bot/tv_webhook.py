"""
AiTrader TradingView Webhook Receiver
──────────────────────────────────────
Receives live data from Pine Script alerts every candle.
Stores in memory + triggers AI Agent analysis.

Setup:
1. Add alert in TradingView with webhook URL
2. Bot receives real price, RSI, EMA from your chart
3. AI Agent uses THIS data instead of yfinance
"""

from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
from loguru import logger
from pydantic import BaseModel
from typing import Optional
import asyncio

router = APIRouter()

# ── Live data store ────────────────────────────────────
_live_data: dict = {}
_candle_history: list = []
MAX_HISTORY = 500  # keep last 500 candles


class TVAlert(BaseModel):
    """Matches Pine Script alert JSON payload."""
    price:      float
    high:       Optional[float] = None
    low:        Optional[float] = None
    open:       Optional[float] = None
    volume:     Optional[float] = None
    rsi:        Optional[float] = None
    ema9:       Optional[float] = None
    ema21:      Optional[float] = None
    ema50:      Optional[float] = None
    atr:        Optional[float] = None
    vwap:       Optional[float] = None
    signal:     Optional[str]   = None   # BUY / SELL / WAIT
    signal_type:Optional[str]   = None   # ELITE / NORMAL
    score:      Optional[float] = None
    bull_score: Optional[float] = None
    bear_score: Optional[float] = None
    symbol:     Optional[str]   = "XAUUSD"
    timeframe:  Optional[str]   = "5"
    token:      Optional[str]   = None   # security token


@router.post("/api/webhook/tradingview")
async def receive_tv_alert(request: Request):
    """
    Receive Pine Script webhook alert.
    TradingView sends this on every candle or signal.
    """
    global _live_data, _candle_history

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Optional token validation
    from config.settings import settings
    expected_token = getattr(settings, 'tv_webhook_token', '')
    if expected_token and body.get('token') != expected_token:
        logger.warning("Webhook: invalid token rejected")
        raise HTTPException(status_code=401, detail="Invalid token")

    # Parse alert
    try:
        alert = TVAlert(**{k: v for k, v in body.items()
                          if k in TVAlert.model_fields})
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Store latest data
    now = datetime.now()
    _live_data = {
        "price":       alert.price,
        "high":        alert.high or alert.price,
        "low":         alert.low  or alert.price,
        "open":        alert.open or alert.price,
        "volume":      alert.volume or 1000,
        "rsi":         alert.rsi,
        "ema9":        alert.ema9,
        "ema21":       alert.ema21,
        "ema50":       alert.ema50,
        "atr":         alert.atr,
        "vwap":        alert.vwap,
        "signal":      alert.signal,
        "signal_type": alert.signal_type,
        "score":       alert.score,
        "bull_score":  alert.bull_score,
        "bear_score":  alert.bear_score,
        "symbol":      alert.symbol,
        "timeframe":   alert.timeframe,
        "source":      "tradingview_live",
        "timestamp":   now.isoformat(),
        "age_seconds": 0,
    }

    # Add to history
    _candle_history.append({**_live_data, "ts": now.timestamp()})
    if len(_candle_history) > MAX_HISTORY:
        _candle_history.pop(0)

    logger.success(
        f"TV Webhook: {alert.symbol} @ ${alert.price:,.2f} "
        f"RSI={alert.rsi} Signal={alert.signal} Score={alert.score}"
    )

    # Trigger AI Agent if signal present
    if alert.signal in ("BUY", "SELL"):
        logger.info(f"TV signal received: {alert.signal} — triggering AI Agent")
        asyncio.create_task(_trigger_agent_on_signal(alert))

    return {
        "received": True,
        "price": alert.price,
        "signal": alert.signal,
        "timestamp": now.isoformat(),
    }


async def _trigger_agent_on_signal(alert: TVAlert):
    """Trigger AI Agent when TradingView fires a signal."""
    await asyncio.sleep(1)  # brief delay
    try:
        from bot.ai_agent import run_agent
        logger.info(f"AI Agent triggered by TV signal: {alert.signal}")
        await run_agent()
    except Exception as e:
        logger.error(f"Agent trigger failed: {e}")


@router.get("/api/webhook/status")
async def webhook_status():
    """Check if webhook is receiving data."""
    if not _live_data:
        return {
            "connected": False,
            "message": "No data received yet — set up TradingView alert",
            "webhook_url": "http://YOUR_IP:8000/api/webhook/tradingview",
        }
    age = (datetime.now() - datetime.fromisoformat(
        _live_data["timestamp"])).seconds
    _live_data["age_seconds"] = age
    return {
        "connected": True,
        "last_price": _live_data.get("price"),
        "last_signal": _live_data.get("signal"),
        "data_age_seconds": age,
        "candles_received": len(_candle_history),
        "source": "tradingview_live",
    }


@router.get("/api/webhook/latest")
async def get_latest():
    """Get latest candle data from TradingView."""
    if not _live_data:
        return {"source": "none", "message": "No TV data yet"}
    age = (datetime.now() - datetime.fromisoformat(
        _live_data["timestamp"])).seconds
    return {**_live_data, "age_seconds": age}


def get_live_data() -> dict:
    """Called by AI Agent to get real TV data."""
    return _live_data


def get_candle_history() -> list:
    """Get candle history for indicator calculation."""
    return _candle_history


def has_live_data() -> bool:
    """Check if webhook is receiving fresh data."""
    if not _live_data:
        return False
    age = (datetime.now() - datetime.fromisoformat(
        _live_data["timestamp"])).seconds
    return age < 600  # data is less than 10 min old
