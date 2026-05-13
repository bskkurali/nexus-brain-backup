"""
NEXUS Memory System
────────────────────
Every agent saves findings to disk permanently.
Master Brain reads history to make better decisions.
Brain gets smarter every cycle — never forgets.

Storage: data/nexus_memory.json
"""

import json, os
from datetime import datetime
from loguru import logger

MEMORY_FILE = "data/nexus_memory.json"
MAX_ITEMS   = 100  # per category


def _load() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(MEMORY_FILE):
        try:
            return json.load(open(MEMORY_FILE))
        except Exception:
            pass
    return {
        "created":    datetime.now().isoformat(),
        "updated":    datetime.now().isoformat(),

        # Agent findings (last 100 each)
        "news_reports":       [],  # NewsAgent findings
        "technical_reports":  [],  # TechnicalAgent findings
        "risk_reports":       [],  # RiskAgent findings
        "sentiment_reports":  [],  # SentimentAgent findings

        # Trade history with reasons
        "trade_journal":      [],  # every trade + why + result

        # Patterns discovered
        "win_patterns":       [],  # setups that won
        "loss_patterns":      [],  # setups that lost

        # Strategy performance
        "session_stats": {
            "LONDON":            {"trades":0,"wins":0},
            "LONDON_NY_OVERLAP": {"trades":0,"wins":0},
            "NEW_YORK":          {"trades":0,"wins":0},
            "ASIAN":             {"trades":0,"wins":0},
        },

        # RSI entry performance
        "rsi_stats": {},  # "30-40": {"trades":0,"wins":0}

        # Key levels that held
        "key_levels": [],

        # Master Brain decisions
        "brain_decisions": [],  # last 50 decisions with outcome
    }


def _save(mem: dict):
    mem["updated"] = datetime.now().isoformat()
    json.dump(mem, open(MEMORY_FILE, "w"), indent=2)


# ── Save agent reports ────────────────────────────────

def save_news_report(report: dict):
    mem = _load()
    mem["news_reports"].append({
        "time":      datetime.now().isoformat(),
        "sentiment": report.get("sentiment","NEUTRAL"),
        "key_driver":report.get("key_driver","")[:80],
        "headlines": report.get("headlines",[])[:3],
        "blackout":  report.get("blackout",False),
    })
    mem["news_reports"] = mem["news_reports"][-MAX_ITEMS:]
    _save(mem)


def save_technical_report(m5: dict, h1: dict):
    mem = _load()
    price = (m5 or {}).get("price", 0)
    mem["technical_reports"].append({
        "time":       datetime.now().isoformat(),
        "price":      price,
        "rsi_m5":     (m5 or {}).get("rsi",0),
        "rsi_h1":     (h1 or {}).get("rsi",0),
        "adx_m5":     (m5 or {}).get("adx",0),
        "macd_m5":    (m5 or {}).get("macd_dir",""),
        "trend_h1":   "BULL" if (h1 or {}).get("bull") else "BEAR" if (h1 or {}).get("bear") else "SIDE",
        "trend_m5":   "BULL" if (m5 or {}).get("bull") else "BEAR" if (m5 or {}).get("bear") else "SIDE",
        "near_fib":   (m5 or {}).get("near_fib",""),
        "vol_ratio":  (m5 or {}).get("vol_ratio",0),
    })
    mem["technical_reports"] = mem["technical_reports"][-MAX_ITEMS:]
    _save(mem)


def save_brain_decision(decision: dict, market: dict):
    mem = _load()
    mem["brain_decisions"].append({
        "time":       datetime.now().isoformat(),
        "decision":   decision.get("decision","WAIT"),
        "confidence": decision.get("confidence",0),
        "reason":     decision.get("reason","")[:100],
        "price":      market.get("price",0),
        "rsi":        market.get("rsi",0),
        "session":    market.get("session",""),
        "outcome":    None,  # filled when trade closes
    })
    mem["brain_decisions"] = mem["brain_decisions"][-50:]
    _save(mem)


def save_trade(ticket: str, direction: str, entry: float,
               sl: float, tp: float, session: str,
               rsi: float, reason: str, confidence: int):
    """Save trade to journal."""
    mem = _load()
    mem["trade_journal"].append({
        "ticket":     ticket,
        "time":       datetime.now().isoformat(),
        "direction":  direction,
        "entry":      entry,
        "sl":         sl,
        "tp":         tp,
        "session":    session,
        "rsi_entry":  rsi,
        "reason":     reason[:100],
        "confidence": confidence,
        "pnl":        None,  # filled on close
        "won":        None,
    })
    mem["trade_journal"] = mem["trade_journal"][-MAX_ITEMS:]
    _save(mem)


def record_trade_close(ticket: str, pnl: float, won: bool):
    """Record trade result — used for self-training."""
    mem = _load()

    # Find and update trade
    for trade in mem["trade_journal"]:
        if trade.get("ticket") == ticket:
            trade["pnl"]  = pnl
            trade["won"]  = won
            trade["closed_at"] = datetime.now().isoformat()

            # Update session stats
            sess = trade.get("session","UNKNOWN")
            if sess in mem["session_stats"]:
                mem["session_stats"][sess]["trades"] += 1
                if won:
                    mem["session_stats"][sess]["wins"] += 1

            # Update RSI stats
            rsi = trade.get("rsi_entry",50)
            bucket = f"{int(rsi//10)*10}-{int(rsi//10)*10+10}"
            if bucket not in mem["rsi_stats"]:
                mem["rsi_stats"][bucket] = {"trades":0,"wins":0}
            mem["rsi_stats"][bucket]["trades"] += 1
            if won:
                mem["rsi_stats"][bucket]["wins"] += 1

            # Store pattern
            pattern = {
                "direction":  trade.get("direction"),
                "session":    sess,
                "rsi":        rsi,
                "confidence": trade.get("confidence",0),
                "reason":     trade.get("reason",""),
                "pnl":        pnl,
            }
            if won:
                mem["win_patterns"].append(pattern)
                mem["win_patterns"] = mem["win_patterns"][-50:]
            else:
                mem["loss_patterns"].append(pattern)
                mem["loss_patterns"] = mem["loss_patterns"][-50:]
            break

    _save(mem)
    logger.info(f"Memory: trade {ticket} recorded ({'WIN' if won else 'LOSS'} ${pnl:+.2f})")


def get_brain_summary() -> str:
    """
    Returns a summary of everything the brain has learned.
    Injected into Master Brain's Claude prompt.
    """
    mem = _load()

    lines = ["═══ NEXUS BRAIN MEMORY ═══"]

    # Session performance
    sess_stats = mem.get("session_stats",{})
    best_sess = None; best_wr = 0
    for sess, stats in sess_stats.items():
        t = stats.get("trades",0)
        w = stats.get("wins",0)
        if t > 0:
            wr = w/t*100
            lines.append(f"Session {sess}: {w}/{t} trades ({wr:.0f}% WR)")
            if wr > best_wr:
                best_wr = wr; best_sess = sess

    if best_sess:
        lines.append(f"Best session: {best_sess} ({best_wr:.0f}% WR)")

    # RSI performance
    rsi_stats = mem.get("rsi_stats",{})
    best_rsi = None; best_rsi_wr = 0
    for bucket, stats in sorted(rsi_stats.items()):
        t = stats.get("trades",0)
        w = stats.get("wins",0)
        if t >= 2:
            wr = w/t*100
            lines.append(f"RSI {bucket}: {wr:.0f}% WR ({t} trades)")
            if wr > best_rsi_wr:
                best_rsi_wr = wr; best_rsi = bucket

    if best_rsi:
        lines.append(f"Best RSI zone: {best_rsi} ({best_rsi_wr:.0f}% WR)")

    # Recent win patterns
    wins = mem.get("win_patterns",[])[-3:]
    if wins:
        lines.append("Recent wins:")
        for w in wins:
            lines.append(f"  {w.get('direction')} {w.get('session')} RSI:{w.get('rsi',0):.0f} → +${w.get('pnl',0):.2f}")

    # Recent loss patterns
    losses = mem.get("loss_patterns",[])[-3:]
    if losses:
        lines.append("Recent losses:")
        for l in losses:
            lines.append(f"  {l.get('direction')} {l.get('session')} RSI:{l.get('rsi',0):.0f} → ${l.get('pnl',0):.2f}")

    # Total stats
    journal = mem.get("trade_journal",[])
    closed = [t for t in journal if t.get("won") is not None]
    if closed:
        wins_count = sum(1 for t in closed if t.get("won"))
        wr = wins_count/len(closed)*100
        total_pnl = sum(t.get("pnl",0) for t in closed)
        lines.append(f"Overall: {wins_count}/{len(closed)} trades ({wr:.0f}% WR) ${total_pnl:+.2f}")

    return "\n".join(lines)


def get_stats() -> dict:
    """Quick stats for dashboard."""
    mem = _load()
    journal = mem.get("trade_journal",[])
    closed  = [t for t in journal if t.get("won") is not None]
    wins    = sum(1 for t in closed if t.get("won"))
    total_pnl = sum(t.get("pnl",0) for t in closed)

    return {
        "total_trades":    len(journal),
        "closed_trades":   len(closed),
        "wins":            wins,
        "losses":          len(closed)-wins,
        "win_rate":        round(wins/len(closed)*100,1) if closed else 0,
        "total_pnl":       round(total_pnl,2),
        "news_reports":    len(mem.get("news_reports",[])),
        "tech_reports":    len(mem.get("technical_reports",[])),
        "win_patterns":    len(mem.get("win_patterns",[])),
        "loss_patterns":   len(mem.get("loss_patterns",[])),
        "brain_decisions": len(mem.get("brain_decisions",[])),
    }
