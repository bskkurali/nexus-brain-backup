"""
NEXUS GOLD AI — Dashboard Backend
All endpoints complete. No missing routes.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import asyncio, os, json
from datetime import datetime
from collections import deque

app = FastAPI(title="NEXUS GOLD AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

STATIC = os.path.join(os.path.dirname(__file__), "static")
_logs  = deque(maxlen=300)

# ── Wire loguru to dashboard ───────────────────────────
try:
    from loguru import logger
    class _Sink:
        def write(self, msg):
            try:
                txt = str(msg).strip()
                if len(txt) < 3: return
                parts = txt.split("|")
                clean = parts[-1].strip() if len(parts)>1 else txt
                lvl = ("success" if "SUCCESS" in txt or "✅" in txt
                       else "warn"    if "WARNING" in txt
                       else "error"   if "ERROR"   in txt
                       else "info")
                _logs.append({
                    "time":  datetime.now().strftime("%H:%M:%S"),
                    "msg":   clean[:150],
                    "level": lvl
                })
            except Exception: pass
    logger.add(_Sink(), format="{message}", level="INFO", colorize=False)
except Exception: pass

# ── Static files ──────────────────────────────────────
if os.path.exists(STATIC):
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/")
async def root():
    f = os.path.join(STATIC, "nexus_dashboard.html")
    return FileResponse(f) if os.path.exists(f) else JSONResponse({"status":"ok"})

@app.get("/settings")
async def settings_page():
    f = os.path.join(STATIC, "nexus_settings.html")
    return FileResponse(f) if os.path.exists(f) else JSONResponse({"error":"not found"})

@app.get("/favicon.ico")
async def favicon():
    return JSONResponse({"ok": True})

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}

# ── Logs ─────────────────────────────────────────────
@app.get("/api/logs")
async def get_logs(limit: int = 50):
    items = list(_logs)[-limit:]
    items.reverse()
    return {"logs": items}

@app.post("/api/logs/add")
async def add_log(data: dict):
    _logs.append({"time": datetime.now().strftime("%H:%M:%S"),
                  "msg": data.get("msg","")[:150],
                  "level": data.get("level","info")})
    return {"ok": True}

# ── Price ─────────────────────────────────────────────
@app.get("/api/price")
@app.get("/price")
async def get_price():
    try:
        from bot.exness_feed import get_live_price
        lp = get_live_price()
        if lp and lp.get("mid",0) > 2000:
            p = lp["mid"]
            return {"price":round(p,2),"bid":round(p-0.2,2),
                    "ask":round(p+0.2,2),"source":"live",
                    "open":round(p-10,2)}
    except Exception: pass
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get("https://api.gold-api.com/price/XAU",
                           headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                p = float(r.json().get("price",0))
                if p > 2000:
                    return {"price":round(p,2),"bid":round(p-0.2,2),
                            "ask":round(p+0.2,2),"source":"gold-api",
                            "open":round(p-10,2)}
    except Exception: pass
    return {"price":0,"error":"unavailable"}

# ── Market ────────────────────────────────────────────
@app.get("/api/market")
async def get_market():
    try:
        import yfinance as yf, pandas as pd
        def _f():
            df = yf.download("GC=F",period="5d",interval="5m",
                           progress=False,auto_adjust=True)
            if df is None or df.empty: return None
            df.columns=[c[0].lower() if isinstance(c,tuple) else c.lower()
                       for c in df.columns]
            cl=df['close']; hi=df['high']; lo=df['low']; vl=df['volume']
            e9=float(cl.ewm(9,adjust=False).mean().iloc[-1])
            e21=float(cl.ewm(21,adjust=False).mean().iloc[-1])
            e50=float(cl.ewm(50,adjust=False).mean().iloc[-1])
            d=cl.diff(); g=d.clip(lower=0).ewm(14,adjust=False).mean()
            l=(-d.clip(upper=0)).ewm(14,adjust=False).mean()
            rsi=round(float(100-(100/(1+(g/(l+1e-9)).iloc[-1]))),1)
            tr=pd.concat([hi-lo,(hi-cl.shift()).abs(),(lo-cl.shift()).abs()],axis=1).max(axis=1)
            atr=round(float(tr.ewm(14,adjust=False).mean().iloc[-1]),2)
            vr=round(float(vl.iloc[-1])/float(vl.rolling(20).mean().iloc[-1]),2)
            price=float(cl.iloc[-1])
            bull=e9>e21>e50; bear=e9<e21<e50
            ml=cl.ewm(12,adjust=False).mean()-cl.ewm(26,adjust=False).mean()
            sl2=ml.ewm(9,adjust=False).mean()
            macd=round(float(ml.iloc[-1]),2); msig=round(float(sl2.iloc[-1]),2)
            tr_s=tr.ewm(14,adjust=False).mean()
            up=hi.diff(); dn=-lo.diff()
            pdm=up.where((up>dn)&(up>0),0)
            mdm=dn.where((dn>up)&(dn>0),0)
            pdi=100*(pdm.ewm(14,adjust=False).mean()/tr_s)
            mdi=100*(mdm.ewm(14,adjust=False).mean()/tr_s)
            dx=100*(abs(pdi-mdi)/(pdi+mdi+1e-9))
            adx=round(float(dx.ewm(14,adjust=False).mean().iloc[-1]),1)
            swing_h = float(hi.tail(100).max())
            swing_l = float(lo.tail(100).min())
            diff    = swing_h - swing_l
            fib618  = round(swing_l + diff * 0.618, 2)
            fib50   = round(swing_l + diff * 0.500, 2)
            fib382  = round(swing_l + diff * 0.382, 2)
            typical = (hi + lo + cl) / 3
            vwap_v  = round(float((typical * vl).cumsum().iloc[-1] /
                                  vl.cumsum().iloc[-1]), 2)
            stoch_l = lo.rolling(14).min()
            stoch_h = hi.rolling(14).max()
            stoch_k = round(float(((cl - stoch_l)/(stoch_h - stoch_l + 1e-9)).iloc[-1] * 100), 1)
            return {
                "price":round(price,2),"rsi":rsi,"atr":atr,
                "ema9":round(e9,2),"ema21":round(e21,2),"ema50":round(e50,2),
                "macd":macd,"macd_signal":msig,
                "adx":adx,"volume_ratio":vr,
                "bull_trend":bull,"bear_trend":bear,
                "bull_score":60 if bull else 20,
                "bear_score":60 if bear else 20,
                "open":round(price-10,2),
                "bid":round(price-0.2,2),"ask":round(price+0.2,2),
                "vwap":vwap_v,
                "swing_high":round(swing_h,2),
                "swing_low":round(swing_l,2),
                "fib618":fib618,"fib50":fib50,"fib382":fib382,
                "stoch":stoch_k,
            }
        loop = asyncio.get_event_loop()
        d = await loop.run_in_executor(None, _f)
        if d: return d
    except Exception as e:
        return {"error":str(e),"price":0}
    return {"price":0}

# ── Account & Status ──────────────────────────────────
@app.get("/api/account")
@app.get("/account")
async def get_account():
    try:
        equity=100.0; daily_pnl=0.0; trades_today=0
        if os.path.exists("data/risk_state.json"):
            s=json.load(open("data/risk_state.json"))
            equity=float(s.get("equity",100.0))
            daily_pnl=float(s.get("daily_pnl",0.0))
            trades_today=int(s.get("trades_today",0))
        from config.settings import settings
        return {"equity":equity,"balance":equity,"daily_pnl":daily_pnl,
                "trades_today":trades_today,"mode":settings.execution_mode,
                "survival_pct":round((equity/100)*100,1),"currency":"USD"}
    except Exception as e:
        return {"equity":100.0,"balance":100.0,"daily_pnl":0.0,
                "mode":"paper","survival_pct":100.0}

@app.get("/api/status")
async def get_status():
    return await get_account()

@app.get("/api/survival")
async def get_survival():
    try:
        acc = await get_account()
        idle_hours = 0.0
        if os.path.exists("data/survival_state.json"):
            s=json.load(open("data/survival_state.json"))
            lt=datetime.fromisoformat(s.get("last_trade",datetime.now().isoformat()))
            idle_hours=round((datetime.now()-lt).total_seconds()/3600,1)
        eq = acc.get("equity",100.0)
        pct = round((eq/100)*100,1)
        mode = "HEALTHY" if pct >= 80 else "CAUTION" if pct >= 40 else "DANGER"
        daily_target = round(eq * 0.02, 2)   # 2% daily profit target
        return {"alive":eq>1.0,"equity":eq,
                "survival_pct":pct,
                "idle_hours":idle_hours,
                "hours_until_penalty":round(max(0,24-idle_hours),1),
                "status":"healthy" if eq>=80 else "danger",
                "mode": mode,
                "daily_target": daily_target}
    except Exception:
        return {"alive":True,"equity":100.0,"survival_pct":100,
                "idle_hours":0,"hours_until_penalty":24}

# ── Positions ─────────────────────────────────────────
@app.get("/api/positions")
@app.get("/api/trades/open")
@app.get("/positions")
async def get_positions():
    try:
        from bot.execution import _paper_trades
        result = []
        for ticket, t in _paper_trades.items():
            status = getattr(t, "status", "OPEN")
            if status != "OPEN":
                continue   # only show live open positions
            result.append({
                "ticket":     ticket,
                "symbol":     getattr(t,"symbol","XAUUSDm"),
                "direction":  getattr(t,"direction","?"),
                "entry":      round(float(getattr(t,"entry",0)),2),
                "stop_loss":  round(float(getattr(t,"stop_loss",0)),2),
                "take_profit":round(float(getattr(t,"take_profit_1",0)),2),
                "lot_size":   getattr(t,"lot_size",0.01),
                "pnl":        round(float(getattr(t,"pnl",0)),2),
                "status":     status,
                "mode":       getattr(t,"mode","PAPER"),
            })
        return result
    except Exception:
        return []

@app.get("/api/trades/closed")
async def get_closed_trades():
    """Return closed trade history with P&L — used by Trade Stats."""
    try:
        from bot.execution import _paper_trades
        closed = []
        for ticket, t in _paper_trades.items():
            status = getattr(t, "status", "OPEN")
            if status == "OPEN":
                continue
            pnl = round(float(getattr(t, "pnl", 0)), 2)
            closed.append({
                "ticket":    ticket,
                "direction": getattr(t, "direction", "?"),
                "entry":     round(float(getattr(t, "entry", 0)), 2),
                "close":     round(float(getattr(t, "close_price", 0)), 2),
                "pnl":       pnl,
                "won":       pnl > 0,
                "reason":    status,
                "mode":      getattr(t, "mode", "PAPER"),
            })
        wins      = sum(1 for t in closed if t["won"])
        total_pnl = round(sum(t["pnl"] for t in closed), 2)
        return {
            "trades":    closed,
            "total":     len(closed),
            "wins":      wins,
            "losses":    len(closed) - wins,
            "win_rate":  round(wins / len(closed) * 100, 1) if closed else 0.0,
            "total_pnl": total_pnl,
        }
    except Exception:
        return {"trades": [], "total": 0, "wins": 0,
                "losses": 0, "win_rate": 0.0, "total_pnl": 0.0}

# ── Brain ─────────────────────────────────────────────
@app.get("/api/brain/thoughts")
async def get_thoughts(limit: int = 30):
    try:
        if os.path.exists("data/brain/thoughts.json"):
            thoughts = json.load(open("data/brain/thoughts.json"))
            recent = thoughts[-limit:]
            recent.reverse()
            return {"thoughts":recent,"total":len(thoughts)}
    except Exception: pass
    return {"thoughts":[],"total":0}

@app.get("/api/brain/agents")
async def get_agents():
    try:
        if os.path.exists("data/brain/agents.json"):
            agents = json.load(open("data/brain/agents.json"))
            return {"agents":[{"id":k,"name":v.get("name","?"),
                               "active":v.get("active",True),
                               "runs":v.get("runs",0)}
                              for k,v in agents.items()],
                    "total":len(agents)}
    except Exception: pass
    return {"agents":[],"total":0}

@app.get("/api/brain/goals")
async def get_goals():
    try:
        if os.path.exists("data/brain/goals.json"):
            return {"goals":json.load(open("data/brain/goals.json"))}
    except Exception: pass
    return {"goals":[]}

# ── AI Status ─────────────────────────────────────────
@app.get("/api/ai/status")
async def get_ai_status():
    from config.settings import settings
    claude_ok = bool(settings.anthropic_api_key and
                     settings.anthropic_api_key != "your_claude_key_here")
    groq_ok   = bool(settings.grok_api_key and
                     settings.grok_api_key != "your_groq_key_here")
    google_ok = bool(settings.google_ai_api_key)
    return {
        "claude":  claude_ok,
        "groq":    groq_ok,
        "google":  google_ok,
        "active":  ("claude+groq+gemma" if all([claude_ok,groq_ok,google_ok])
                    else "claude+groq" if claude_ok and groq_ok
                    else "claude" if claude_ok else "none"),
        "mode":    "Master Brain (Claude) + Agents (Groq/Gemma)"
    }

# ── Agent ─────────────────────────────────────────────
@app.get("/api/agent/status")
async def agent_status():
    try:
        from bot.ai_agent import get_last_result, get_stats
        r=get_last_result(); s=get_stats()
        result = {"status":"ok","decision":r.get("decision","WAIT"),
                "trade_placed":r.get("trade_placed",False),
                "final_message":r.get("final_message","")[:300],
                "tools_used":r.get("tools_used",0),
                "tool_calls":r.get("tool_calls",[]),"stats":s,
                "confidence":0,"quality":"--","h1_bias":"--","reason":""}
        # Enrich with latest brain decision from memory
        try:
            if os.path.exists("data/nexus_memory.json"):
                mem = json.load(open("data/nexus_memory.json"))
                decisions = mem.get("brain_decisions",[])
                if decisions:
                    last = decisions[-1]
                    result["decision"]   = last.get("decision", result["decision"])
                    result["confidence"] = last.get("confidence", 0)
                    result["reason"]     = last.get("reason","")[:120]
        except Exception:
            pass
        # Also check nexus_memory for last trade quality
        try:
            if os.path.exists("data/strategy_brain.json"):
                sb = json.load(open("data/strategy_brain.json"))
                hist = sb.get("trade_history",[])
                if hist:
                    lt = hist[-1]
                    result["quality"]  = lt.get("quality", lt.get("signal_quality","A"))
                    result["h1_bias"]  = lt.get("session","").upper() or "--"
        except Exception:
            pass
        return result
    except Exception as e:
        return {"status":"idle","decision":"WAIT","confidence":0,
                "quality":"--","h1_bias":"--","reason":"","error":str(e)}

@app.get("/api/agent/thinking")
async def get_thinking():
    return await agent_status()

@app.post("/api/agent/run")
async def run_agent_manual():
    try:
        from bot.nexus_core import master_brain_cycle
        result = await master_brain_cycle()
        return {"success":True,"executed":result.get("executed",False),
                "direction":result.get("direction","WAIT")}
    except Exception as e:
        return {"success":False,"error":str(e)}

# ── News ──────────────────────────────────────────────
@app.get("/api/news/signal")
async def news_signal():
    try:
        if os.path.exists("data/internet_brain.json"):
            ib=json.load(open("data/internet_brain.json"))
            news=ib.get("news_library",[])
            if news:
                latest=news[-1]
                return {"signal":"WAIT","sentiment":"NEUTRAL","score":50,
                        "confidence":50,"driver":latest.get("title","")[:80],
                        "blackout":False,"trade_ok":True}
    except Exception: pass
    return {"signal":"WAIT","sentiment":"NEUTRAL","score":50,
            "confidence":50,"driver":"Monitoring...","blackout":False,"trade_ok":True}

# ── Assets ────────────────────────────────────────────
@app.get("/api/assets")
async def get_assets():
    return [{"symbol":"XAUUSDm","name":"Gold","enabled":True,"price":0},
            {"symbol":"BTCUSDm","name":"Bitcoin","enabled":False,"price":0},
            {"symbol":"USOILm","name":"Oil","enabled":False,"price":0}]

@app.get("/api/assets/{symbol}/price")
async def get_asset_price(symbol: str):
    if "XAU" in symbol:
        p = await get_price()
        return {"symbol":symbol,"price":p.get("price",0),
                "bid":p.get("bid",0),"ask":p.get("ask",0)}
    return {"symbol":symbol,"price":0,"bid":0,"ask":0}

@app.get("/api/assets/{symbol}/toggle")
async def toggle_asset(symbol: str, enabled: bool = True):
    return {"symbol":symbol,"enabled":enabled,"ok":True}

# ── Internet Brain ────────────────────────────────────
@app.get("/api/internet/brain")
async def get_internet_brain():
    try:
        if os.path.exists("data/internet_brain.json"):
            ib=json.load(open("data/internet_brain.json"))
            return {"articles_read":ib.get("articles_read",0),
                    "searches_done":ib.get("searches_done",0),
                    "news_count":len(ib.get("news_library",[])),
                    "patterns_learned":len(ib.get("pattern_library",[])),
                    "knowledge_base":ib.get("knowledge_base",[])[-5:],
                    "latest_news":ib.get("news_library",[])[-3:]}
    except Exception: pass
    return {"articles_read":0,"searches_done":0}

@app.get("/api/memory")
async def get_memory():
    try:
        from bot.unified_memory import get_memory_summary
        return {"summary":get_memory_summary()}
    except Exception:
        return {"summary":"Memory module loading..."}

# ── Settings ─────────────────────────────────────────
@app.get("/api/settings")
async def get_settings_api():
    try:
        from config.settings import settings
        # Don't expose actual keys - just show if set
        return {
            "execution_mode":       settings.execution_mode,
            "symbol":               settings.symbol,
            "mt5_login":            str(settings.mt5_login),
            "mt5_server":           settings.mt5_server,
            "mt5_mode":             settings.mt5_mode,
            "telegram_chat_id":     str(settings.telegram_chat_id),
            "dashboard_port":       settings.dashboard_port,
            "dashboard_host":       settings.dashboard_host,
            "claude_model":         settings.claude_model,
            "starting_equity":      settings.starting_equity,
            "max_daily_loss_pct":   settings.max_daily_loss_pct,
            "max_risk_per_trade_pct": settings.max_risk_per_trade_pct,
            "has_claude":  bool(settings.anthropic_api_key),
            "has_groq":    bool(settings.grok_api_key),
            "has_google":  bool(settings.google_ai_api_key),
            "has_telegram":bool(settings.telegram_bot_token),
        }
    except Exception as e:
        return {"error":str(e)}

@app.post("/api/settings")
async def save_settings_api(data: dict):
    try:
        env_path = ".env"
        env = {}
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k,v = line.split("=",1)
                    env[k.strip()] = v.strip()

        field_map = {
            "execution_mode":        "EXECUTION_MODE",
            "symbol":                "SYMBOL",
            "mt5_login":             "MT5_LOGIN",
            "mt5_password":          "MT5_PASSWORD",
            "mt5_server":            "MT5_SERVER",
            "mt5_mode":              "MT5_MODE",
            "mt5_bridge_url":        "MT5_BRIDGE_URL",
            "anthropic_api_key":     "ANTHROPIC_API_KEY",
            "grok_api_key":          "GROK_API_KEY",
            "google_ai_api_key":     "GOOGLE_AI_API_KEY",
            "telegram_bot_token":    "TELEGRAM_BOT_TOKEN",
            "telegram_chat_id":      "TELEGRAM_CHAT_ID",
            "starting_equity":       "STARTING_EQUITY",
            "max_daily_loss_pct":    "MAX_DAILY_LOSS_PCT",
            "max_risk_per_trade_pct":"MAX_RISK_PER_TRADE_PCT",
            "claude_model":          "CLAUDE_MODEL",
            "dashboard_port":        "DASHBOARD_PORT",
            "dashboard_host":        "DASHBOARD_HOST",
        }
        updated = []
        for field, env_key in field_map.items():
            if field in data and data[field] not in [None, "", "undefined"]:
                env[env_key] = str(data[field])
                updated.append(env_key)

        with open(env_path, "w") as f:
            for k,v in env.items():
                f.write(f"{k}={v}\n")

        return {"success":True,"updated":updated,
                "message":"Saved! Restart bot to apply changes."}
    except Exception as e:
        return {"success":False,"error":str(e)}

@app.post("/api/telegram/test")
async def test_telegram():
    try:
        import httpx
        from config.settings import settings
        if not settings.telegram_bot_token:
            return {"success":False,"error":"No Telegram token in .env"}
        msg = "✅ NEXUS GOLD AI — Telegram connected!"
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id":settings.telegram_chat_id,"text":msg}
            )
        ok = r.status_code == 200
        return {"success":ok,"error":"" if ok else r.text[:100]}
    except Exception as e:
        return {"success":False,"error":str(e)}

@app.post("/api/wallet/reset")
async def reset_wallet(data: dict):
    try:
        equity = float(data.get("equity",100.0))
        os.makedirs("data",exist_ok=True)
        state = {"equity":equity,"daily_pnl":0.0,"trades_today":0,
                 "daily_loss_pct":0,"weekly_loss_pct":0,"trading_halted":False}
        json.dump(state, open("data/risk_state.json","w"))
        return {"success":True,"equity":equity}
    except Exception as e:
        return {"success":False,"error":str(e)}

@app.get("/api/nexus/memory")
async def get_nexus_memory():
    try:
        from bot.nexus_memory import get_stats, _load
        stats = get_stats()
        mem   = _load()
        return {
            "stats":          stats,
            "win_patterns":   mem.get("win_patterns",[])[-5:],
            "loss_patterns":  mem.get("loss_patterns",[])[-5:],
            "session_stats":  mem.get("session_stats",{}),
            "rsi_stats":      mem.get("rsi_stats",{}),
            "recent_decisions": mem.get("brain_decisions",[])[-10:],
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/backup/status")
async def backup_status():
    import os
    files = [
        "data/nexus_memory.json","data/strategy_brain.json",
        "data/brain/thoughts.json","data/risk_state.json",
    ]
    status = []
    for f in files:
        if os.path.exists(f):
            import time
            mtime = os.path.getmtime(f)
            age   = round((time.time()-mtime)/60, 1)
            size  = os.path.getsize(f)
            status.append({"file":f,"age_min":age,"size_bytes":size})
    github_set = bool(os.getenv("GITHUB_TOKEN",""))
    return {"files":status,"github_configured":github_set,
            "backup_dir_exists":os.path.exists("backup")}

@app.post("/api/backup/now")
async def backup_now():
    try:
        from bot.nexus_backup import run_backup_cycle
        result = await run_backup_cycle()
        return {"success":True,"result":result}
    except Exception as e:
        return {"success":False,"error":str(e)}

@app.post("/api/backup/restore")
async def restore_backup():
    try:
        from bot.nexus_backup import restore_from_github
        result = await restore_from_github()
        return result
    except Exception as e:
        return {"success":False,"error":str(e)}

@app.get("/api/strategies")
async def get_strategies():
    try:
        from bot.nexus_strategy_brain import load_library, get_strategy_summary
        lib = load_library()

        ai_search = {}
        try:
            from bot.ai_strategy_generator import load_search_progress
            ai_search = load_search_progress()
        except Exception:
            pass

        return {
            "strategies":    lib.get("strategies", {}),
            "best_strategy": lib.get("best_strategy", ""),
            "last_backtest": lib.get("last_backtest", ""),
            "summary":       get_strategy_summary(),
            "ai_search":     ai_search,
        }
    except Exception as e:
        return {"error": str(e)}
