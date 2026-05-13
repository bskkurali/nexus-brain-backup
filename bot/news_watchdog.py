"""
NEXUS News Watchdog
────────────────────
Runs every 2 minutes — independent of the 5-min trading cycle.
Catches breaking news BETWEEN trading decisions.

When news breaks:
  → HIGH  : pauses new trades + moves open SLs to break-even + Telegram alert
  → MEDIUM: logs warning + tells Claude to be cautious (does NOT block trades)
  → NONE  : clear to trade

Alert auto-expires after 30 minutes so the bot doesn't stay frozen forever.

Sources checked every cycle:
  - Kitco RSS (gold-specific)
  - Yahoo Finance GC=F RSS (gold futures news)
  - Reuters business RSS (filtered for gold/Fed keywords)
"""

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from loguru import logger

WATCHDOG_FILE = "data/news_watchdog.json"
ALERT_DURATION_MINUTES = 30   # auto-clear after this long

# ── Keywords that mean "stop trading NOW" ─────────────
HIGH_KEYWORDS = [
    "emergency rate", "surprise cut", "surprise hike",
    "flash crash", "circuit breaker", "market halt", "trading halt",
    "bank collapse", "bank failure", "bank run", "bank crisis",
    "sovereign default", "credit downgrade",
    "war declared", "missile strike", "invasion begins",
    "assassination", "nuclear", "coup",
    "fed emergency", "emergency meeting", "emergency session",
    "black swan", "market crash",
]

# ── Keywords that mean "be careful, check before trading" ──
MEDIUM_KEYWORDS = [
    "breaking:", "breaking news", "alert:", "flash:", "just in:",
    "unexpected", "surprise", "shock",
    "falls sharply", "spikes sharply", "surges", "plunges",
    "rate decision", "rate hike", "rate cut",
    "nfp", "non-farm", "cpi report", "inflation data",
]

# ── Module-level state ─────────────────────────────────
_alert_level: str  = "NONE"
_alert_reason: str = ""
_alert_time: datetime = datetime.now() - timedelta(hours=2)
_seen_hashes: set  = set()


def _load_state():
    global _alert_level, _alert_reason, _alert_time, _seen_hashes
    try:
        if os.path.exists(WATCHDOG_FILE):
            s = json.load(open(WATCHDOG_FILE))
            _seen_hashes = set(s.get("seen_hashes", []))
            _alert_level = s.get("alert_level", "NONE")
            _alert_reason = s.get("alert_reason", "")
            at = s.get("alert_time", "")
            if at:
                _alert_time = datetime.fromisoformat(at)
    except Exception:
        pass


def _save_state():
    try:
        os.makedirs("data", exist_ok=True)
        json.dump({
            "seen_hashes":  list(_seen_hashes)[-300:],
            "alert_level":  _alert_level,
            "alert_reason": _alert_reason,
            "alert_time":   _alert_time.isoformat(),
            "updated":      datetime.now().isoformat(),
        }, open(WATCHDOG_FILE, "w"), indent=2)
    except Exception:
        pass


# Load persisted state on import
_load_state()


# ─────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────

def get_news_alert() -> dict:
    """
    Sync — safe to call from risk_agent() every cycle.
    Returns current alert status; auto-clears stale alerts.
    """
    global _alert_level, _alert_reason, _alert_time

    if _alert_level != "NONE":
        age_min = (datetime.now() - _alert_time).total_seconds() / 60
        if age_min > ALERT_DURATION_MINUTES:
            _alert_level  = "NONE"
            _alert_reason = ""
            _save_state()
            logger.info("📰 News alert expired (30 min)")

    return {
        "level":  _alert_level,
        "reason": _alert_reason,
        "active": _alert_level != "NONE",
    }


def _classify_headline(headline: str) -> str:
    """Return 'HIGH', 'MEDIUM', or 'NONE' for a single headline."""
    h = headline.lower()
    for kw in HIGH_KEYWORDS:
        if kw in h:
            return "HIGH"
    for kw in MEDIUM_KEYWORDS:
        if kw in h:
            return "MEDIUM"
    return "NONE"


def _find_new_headlines(raw: list) -> list:
    """Return only headlines not seen before (deduplicated by content hash)."""
    new = []
    for h in raw:
        hsh = hashlib.md5(h.lower().strip().encode()).hexdigest()[:12]
        if hsh not in _seen_hashes:
            _seen_hashes.add(hsh)
            new.append(h)
    return new


def _tighten_open_trades():
    """
    HIGH news: move SL to break-even on all open paper trades.
    Protects profit (or limits loss) during volatile period.
    """
    try:
        from bot.execution import _paper_trades
        moved = 0
        for trade in _paper_trades.values():
            if getattr(trade, "status", "OPEN") != "OPEN":
                continue
            entry = trade.entry
            direction = trade.direction
            if direction == "BUY" and trade.stop_loss < entry:
                trade.stop_loss = round(entry - 1.0, 2)   # 1pt below entry (not exact BE to avoid noise)
                moved += 1
            elif direction == "SELL" and trade.stop_loss > entry:
                trade.stop_loss = round(entry + 1.0, 2)
                moved += 1
        if moved:
            logger.warning(f"📰 HIGH NEWS: {moved} trade SL(s) moved to near break-even")
    except Exception:
        pass


async def check_breaking_news(tg_bot=None) -> dict:
    """
    Main watchdog coroutine — called every 2 minutes by scheduler.
    Scrapes 3 sources, checks for new breaking headlines.
    """
    global _alert_level, _alert_reason, _alert_time

    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    raw_headlines: list[str] = []

    # ── Source 1: Kitco gold RSS ───────────────────────
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers) as c:
            r = await c.get("https://www.kitco.com/rss/")
            if r.status_code == 200:
                titles = re.findall(r'<title>(.*?)</title>', r.text)
                raw_headlines += [t for t in titles[1:8] if len(t) > 10]
    except Exception:
        pass

    # ── Source 2: Yahoo Finance gold futures RSS ───────
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers) as c:
            r = await c.get(
                "https://feeds.finance.yahoo.com/rss/2.0/headline"
                "?s=GC=F&region=US&lang=en-US"
            )
            if r.status_code == 200:
                titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
                raw_headlines += titles[:6]
    except Exception:
        pass

    # ── Source 3: Reuters business (gold/Fed filter) ───
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers) as c:
            r = await c.get("https://feeds.reuters.com/reuters/businessNews")
            if r.status_code == 200:
                titles = re.findall(r'<title>(.*?)</title>', r.text)
                gold_kw = {"gold","fed","rate","dollar","inflation","war","crisis","bank"}
                raw_headlines += [
                    t for t in titles[1:10]
                    if any(kw in t.lower() for kw in gold_kw)
                ]
    except Exception:
        pass

    if not raw_headlines:
        return {"checked": False, "source_count": 0}

    # Only classify headlines we haven't seen before
    new = _find_new_headlines(raw_headlines)

    if not new:
        _save_state()
        return {"checked": True, "new_headlines": 0, "alert": _alert_level}

    # Find highest severity among new headlines
    triggered_level  = "NONE"
    triggered_reason = ""
    for h in new:
        lvl = _classify_headline(h)
        if lvl == "HIGH":
            triggered_level  = "HIGH"
            triggered_reason = h[:100]
            break                        # HIGH overrides everything
        if lvl == "MEDIUM" and triggered_level == "NONE":
            triggered_level  = "MEDIUM"
            triggered_reason = h[:100]

    # Escalate alert only (never downgrade within the 30-min window)
    severity_rank = {"NONE": 0, "MEDIUM": 1, "HIGH": 2}
    if severity_rank[triggered_level] > severity_rank[_alert_level]:
        _alert_level  = triggered_level
        _alert_reason = triggered_reason
        _alert_time   = datetime.now()

        logger.warning(
            f"📰 BREAKING NEWS [{_alert_level}]: {_alert_reason[:80]}"
        )

        # Tighten stops immediately on HIGH alert
        if _alert_level == "HIGH":
            _tighten_open_trades()

        # Telegram notification
        if tg_bot:
            try:
                from config.settings import settings
                icon = "🚨" if _alert_level == "HIGH" else "⚠️"
                msg = (
                    f"{icon} BREAKING NEWS — {_alert_level}\n"
                    f"{'━'*22}\n"
                    f"{_alert_reason}\n"
                    f"{'━'*22}\n"
                    f"{'New trades PAUSED for 30 min' if _alert_level == 'HIGH' else 'Trading in cautious mode'}\n"
                    f"Open trade SLs {'moved to break-even' if _alert_level == 'HIGH' else 'unchanged'}"
                )
                await tg_bot.send_message(
                    chat_id=settings.telegram_chat_id, text=msg
                )
            except Exception:
                pass

    elif new:
        logger.debug(f"📰 Watchdog: {len(new)} new headlines — no alert trigger")

    _save_state()

    return {
        "checked":        True,
        "headlines_seen": len(raw_headlines),
        "new_headlines":  len(new),
        "alert":          _alert_level,
        "reason":         _alert_reason,
    }
