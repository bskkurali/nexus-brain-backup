"""
MT5 Bridge Server — Nexus AI Trader
Runs on Windows VPS alongside MetaTrader5 terminal.
Exposes REST API so Mac bot can execute trades and fetch balance.
"""

from flask import Flask, request, jsonify
from functools import wraps
import MetaTrader5 as mt5
import os, threading

# ── Load .env ─────────────────────────────────────────────────────
try:
    from dotenv import dotenv_values
    _env = dotenv_values(".env")
except Exception:
    _env = {}

BRIDGE_TOKEN = _env.get("MT5_BRIDGE_TOKEN") or os.environ.get("MT5_BRIDGE_TOKEN", "nexus_bridge_2026")
MT5_LOGIN    = int(_env.get("MT5_LOGIN", 0) or 0)
MT5_PASSWORD = _env.get("MT5_PASSWORD", "")
MT5_SERVER   = _env.get("MT5_SERVER", "")

# ── Initialize MT5 ONCE at startup ────────────────────────────────
print("Connecting to MT5 terminal...")
_mt5_ok = mt5.initialize()
if not _mt5_ok:
    print(f"  initialize() failed: {mt5.last_error()} — trying with credentials...")
    _mt5_ok = mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
if _mt5_ok:
    _info = mt5.account_info()
    if _info:
        print(f"  MT5 connected: Login={_info.login} Balance=${_info.balance:.2f}")
    else:
        print(f"  MT5 initialized but account_info=None: {mt5.last_error()}")
else:
    print(f"  MT5 init FAILED: {mt5.last_error()}")

_lock = threading.Lock()
app = Flask(__name__)

# ── Auth ──────────────────────────────────────────────────────────
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.headers.get("Authorization", "") != f"Bearer {BRIDGE_TOKEN}":
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Ensure MT5 alive (re-init if dropped) ─────────────────────────
def _ensure():
    global _mt5_ok
    # Always probe — account_info() returns None when MT5 disconnects
    try:
        info = mt5.account_info()
        if info is not None:
            _mt5_ok = True
            return True
    except Exception:
        pass
    # Disconnected — re-initialize
    _mt5_ok = False
    print("MT5 disconnected — re-initializing...")
    _mt5_ok = mt5.initialize()
    if not _mt5_ok:
        _mt5_ok = mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if _mt5_ok:
        info = mt5.account_info()
        print(f"MT5 reconnected: Balance=${info.balance:.2f}" if info else "MT5 reconnected (no account info)")
    else:
        print(f"MT5 re-init FAILED: {mt5.last_error()}")
    return _mt5_ok

# ── Routes ────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "mt5_connected": _mt5_ok})

@app.route("/account")
@require_auth
def account():
    with _lock:
        if not _ensure():
            return jsonify({"error": f"MT5 not available: {mt5.last_error()}"}), 500
        info = mt5.account_info()
        if info is None:
            return jsonify({"error": "account_info returned None", "detail": str(mt5.last_error())}), 500
        return jsonify({
            "balance":      float(info.balance),
            "equity":       float(info.equity),
            "margin":       float(info.margin),
            "free_margin":  float(info.margin_free),
            "login":        info.login,
            "server":       info.server,
        })

@app.route("/place_order", methods=["POST"])
@app.route("/order", methods=["POST"])
@require_auth
def place_order():
    with _lock:
        if not _ensure():
            return jsonify({"error": f"MT5 not available: {mt5.last_error()}"}), 500
        try:
            data      = request.json or {}
            symbol    = data.get("symbol", "XAUUSDm")
            direction = data.get("direction", "BUY").upper()
            volume    = float(data.get("volume") or data.get("lot") or 0.01)
            sl        = float(data.get("sl", 0))
            tp        = float(data.get("tp", 0))
            comment   = data.get("comment", "NEXUS")

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return jsonify({"error": f"No tick for {symbol}"}), 400

            order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
            price      = tick.ask if direction == "BUY" else tick.bid

            req = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       volume,
                "type":         order_type,
                "price":        price,
                "sl":           sl,
                "tp":           tp,
                "deviation":    20,
                "magic":        234000,
                "comment":      comment,
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(req)
            if result is None:
                return jsonify({"error": "order_send=None", "detail": str(mt5.last_error())}), 500
            return jsonify({
                "retcode": result.retcode,
                "order":   result.order,
                "volume":  result.volume,
                "price":   result.price,
                "comment": result.comment,
                "success": result.retcode == mt5.TRADE_RETCODE_DONE,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route("/close", methods=["POST"])
@require_auth
def close_position():
    with _lock:
        if not _ensure():
            return jsonify({"error": f"MT5 not available: {mt5.last_error()}"}), 500
        try:
            ticket    = int((request.json or {}).get("ticket", 0))
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                return jsonify({"error": f"Position {ticket} not found"}), 404
            pos        = positions[0]
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick       = mt5.symbol_info_tick(pos.symbol)
            price      = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            req = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       pos.symbol,
                "volume":       pos.volume,
                "type":         close_type,
                "position":     ticket,
                "price":        price,
                "deviation":    20,
                "magic":        234000,
                "comment":      "NEXUS_CLOSE",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(req)
            if result is None:
                return jsonify({"error": "order_send=None"}), 500
            return jsonify({"retcode": result.retcode, "success": result.retcode == mt5.TRADE_RETCODE_DONE, "comment": result.comment})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route("/positions")
@require_auth
def positions():
    with _lock:
        if not _ensure():
            return jsonify({"error": f"MT5 not available: {mt5.last_error()}"}), 500
        pos = mt5.positions_get() or []
        return jsonify([{
            "ticket": p.ticket, "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": p.volume, "price_open": p.price_open,
            "sl": p.sl, "tp": p.tp, "profit": p.profit, "comment": p.comment,
        } for p in pos])

if __name__ == "__main__":
    print(f"Bridge token: {BRIDGE_TOKEN[:8]}...")
    app.run(host="0.0.0.0", port=5000, threaded=False)
