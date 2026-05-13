"""
News Engine
───────────
Fetches from Finnhub + Alpha Vantage + RSS feeds.
Sends headlines to Claude → structured sentiment JSON.
Manages ForexFactory blackout gate.
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional
import httpx
import feedparser
from loguru import logger
from anthropic import AsyncAnthropic

from config.settings import settings

# ── RSS Feed Sources ──────────────────────────────────
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.fxstreet.com/rss/news",
    "https://www.forexlive.com/feed/news",
]

# ── High-Impact Economic Events (gold-relevant) ───────
HIGH_IMPACT_KEYWORDS = [
    "NFP", "Non-Farm Payroll", "FOMC", "Federal Reserve",
    "CPI", "inflation", "PPI", "GDP", "interest rate",
    "Powell", "ECB", "Bank of England", "BOE",
]

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
_cache: dict = {}  # simple in-memory cache


class NewsSignal:
    def __init__(self, data: dict):
        self.sentiment: str = data.get("sentiment", "NEUTRAL")
        self.score: int = data.get("score", 50)
        self.bias: str = data.get("bias", "WAIT")
        self.urgency: str = data.get("urgency", "LOW")
        self.key_driver: str = data.get("key_driver", "No clear driver")
        self.blackout: bool = data.get("blackout", False)
        self.blackout_event: Optional[str] = data.get("blackout_event")
        self.blackout_minutes: int = data.get("blackout_minutes", 0)
        self.dxy_conflict: bool = data.get("dxy_conflict", False)
        self.trade_ok: bool = data.get("trade_ok", True)
        self.confidence: int = data.get("confidence", 50)
        self.headlines: list = data.get("headlines", [])

    def __str__(self):
        status = "BLACKOUT" if self.blackout else self.bias
        return (
            f"[{status}] Sentiment={self.sentiment} "
            f"Score={self.score} Confidence={self.confidence} "
            f"Driver: {self.key_driver}"
        )


async def fetch_rss_headlines(limit: int = 10) -> list[dict]:
    """Fetch latest headlines from RSS feeds."""
    headlines = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                headlines.append({
                    "source": feed.feed.get("title", "RSS"),
                    "title": entry.get("title", ""),
                    "published": entry.get("published", ""),
                    "url": entry.get("link", ""),
                })
        except Exception as e:
            logger.warning(f"RSS fetch failed {url}: {e}")
    return headlines[:limit]


async def fetch_finnhub_news() -> list[dict]:
    """Fetch forex news from Finnhub."""
    if not settings.finnhub_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/news",
                params={"category": "forex", "token": settings.finnhub_api_key},
            )
            items = resp.json()[:8]
            return [
                {
                    "source": "Finnhub",
                    "title": i.get("headline", ""),
                    "published": datetime.fromtimestamp(
                        i.get("datetime", 0)
                    ).strftime("%Y-%m-%d %H:%M"),
                }
                for i in items
                if i.get("headline")
            ]
    except Exception as e:
        logger.warning(f"Finnhub fetch failed: {e}")
        return []


async def fetch_economic_calendar() -> list[dict]:
    """Fetch upcoming high-impact events from Finnhub calendar."""
    if not settings.finnhub_api_key:
        return []
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"from": today, "to": tomorrow, "token": settings.finnhub_api_key},
            )
            events = resp.json().get("economicCalendar", [])
            high_impact = [
                e for e in events
                if e.get("impact") in ("high", "medium")
                and any(k.lower() in e.get("event", "").lower() for k in HIGH_IMPACT_KEYWORDS)
            ]
            return high_impact[:5]
    except Exception as e:
        logger.warning(f"Calendar fetch failed: {e}")
        return []


def _check_blackout(calendar_events: list[dict]) -> tuple[bool, Optional[str], int]:
    """
    Check if any high-impact event is within ±5 minutes.
    Returns (is_blackout, event_name, minutes_until)
    """
    now = datetime.now()
    for event in calendar_events:
        try:
            event_time_str = event.get("time", "")
            if not event_time_str:
                continue
            event_time = datetime.strptime(event_time_str[:16], "%Y-%m-%d %H:%M")
            delta_minutes = (event_time - now).total_seconds() / 60
            if -5 <= delta_minutes <= 30:
                return True, event.get("event", "Unknown"), int(delta_minutes)
        except Exception:
            continue
    return False, None, 0


async def analyze_sentiment(
    headlines: list[dict],
    calendar_events: list[dict],
) -> NewsSignal:
    """Send headlines to Claude for sentiment analysis."""

    # Check cache — refresh every 5 minutes
    cache_key = "news_sentiment"
    cached = _cache.get(cache_key)
    if cached and (datetime.now() - cached["ts"]).seconds < 300:
        return cached["signal"]

    # Check blackout first (no need for AI if blackout active)
    is_blackout, blackout_event, blackout_mins = _check_blackout(calendar_events)

    headlines_text = "\n".join(
        f"- [{h['source']}] {h['title']}" for h in headlines[:10]
    )
    calendar_text = "\n".join(
        f"- {e.get('time','?')} | {e.get('event','?')} | Impact: {e.get('impact','?')}"
        for e in calendar_events[:5]
    ) or "No high-impact events in next 24h"

    prompt = f"""You are an expert gold (XAUUSD) trading analyst with 30 years experience.

Analyze these latest news headlines and economic calendar for GOLD trading direction.

HEADLINES:
{headlines_text}

UPCOMING ECONOMIC EVENTS:
{calendar_text}

BLACKOUT STATUS: {"ACTIVE - " + blackout_event + " in " + str(blackout_mins) + " minutes" if is_blackout else "Clear"}

Respond ONLY with valid JSON, no other text:
{{
  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "score": 0-100,
  "bias": "BUY" | "SELL" | "WAIT",
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "key_driver": "one sentence max — main reason for bias",
  "blackout": true | false,
  "blackout_event": "event name or null",
  "blackout_minutes": 0,
  "dxy_conflict": true | false,
  "trade_ok": true | false,
  "confidence": 0-100
}}

Gold is BULLISH when: USD weak, DXY falling, Fed dovish, geopolitical risk, inflation rising, central bank buying.
Gold is BEARISH when: USD strong, DXY rising, Fed hawkish, risk-on rally, crypto surge.
Set trade_ok=false if: blackout active, confidence<40, conflicting signals."""

    try:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip any markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        data["headlines"] = [h["title"] for h in headlines[:5]]
        signal = NewsSignal(data)
        _cache[cache_key] = {"signal": signal, "ts": datetime.now()}
        logger.info(f"News signal: {signal}")
        return signal

    except Exception as e:
        logger.error(f"Claude sentiment failed: {e}")
        # Return neutral/wait signal on failure
        return NewsSignal({
            "sentiment": "NEUTRAL",
            "score": 50,
            "bias": "WAIT",
            "urgency": "LOW",
            "key_driver": "News analysis unavailable",
            "blackout": is_blackout,
            "blackout_event": blackout_event,
            "blackout_minutes": blackout_mins,
            "trade_ok": not is_blackout,
            "confidence": 0,
        })


async def get_news_signal() -> NewsSignal:
    """Main entry point — fetch all sources and return signal."""
    rss, finnhub_news, calendar = await asyncio.gather(
        fetch_rss_headlines(),
        fetch_finnhub_news(),
        fetch_economic_calendar(),
    )
    all_headlines = (rss + finnhub_news)[:12]
    return await analyze_sentiment(all_headlines, calendar)


if __name__ == "__main__":
    import asyncio
    signal = asyncio.run(get_news_signal())
    print(f"\nSignal: {signal}")
    print(f"Trade OK: {signal.trade_ok}")
    print(f"Blackout: {signal.blackout}")
