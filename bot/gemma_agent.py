"""
NEXUS AI Agent — Multi-Provider Brain
──────────────────────────────────────
Priority chain (fastest/free first):

  1. Ollama  — LOCAL on your Mac, zero API keys, zero rate limits
               Runs Llama 3.1 / Mistral / any model you pull
               Install: brew install ollama && ollama pull llama3.1

  2. Groq    — Free cloud (Llama 3.3 70B), 30 req/min limit
  3. Gemini  — Free cloud, rotates Key1 → Key2 on 429
  4. Claude  — Paid fallback

Once Ollama is running, keys 2-4 are almost never needed.
"""

import asyncio, json, re
from loguru import logger

OLLAMA_MODEL  = "llama3.2:1b"          # change to llama3.2, mistral, etc. if preferred

def _ollama_url() -> str:
    """Read URL from settings so it can point to VPS."""
    try:
        from config.settings import settings
        return settings.ollama_url.rstrip("/")
    except Exception:
        return "http://localhost:11434"

GEMMA_MODEL     = "gemini-2.5-flash"
GEMMA_MODEL_PRO = "gemini-2.5-pro"


# ── JSON extractor ────────────────────────────────────────────────

def extract_json(raw: str) -> dict:
    """
    Robustly extract JSON from AI response.
    Handles: markdown fences, trailing commas, extra text, single quotes.
    """
    if not raw:
        return {}

    text = raw.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if m:
        chunk = m.group(0)
        chunk = re.sub(r',\s*([}\]])', r'\1', chunk)
        chunk = re.sub(r"'([^']*)'", r'"\1"', chunk)
        try:
            return json.loads(chunk)
        except Exception:
            pass

    result = {}
    for key in ["decision", "confidence", "reason", "trade_quality", "concern"]:
        m = re.search(rf'"{key}"\s*:\s*"?([^",\}}]+)"?', text)
        if m:
            val = m.group(1).strip().strip('"')
            result[key] = int(val) if val.isdigit() else val
    return result


# ── Ollama (local) ────────────────────────────────────────────────

async def ollama_call(prompt: str, max_tokens: int = 500,
                      model: str = OLLAMA_MODEL) -> str:
    """
    Call local Ollama — completely free, no rate limits, no internet needed.
    Uses OpenAI-compatible endpoint (/v1/chat/completions).
    Returns "" if Ollama is not running (silent fail → fallback chain).
    """
    import httpx
    try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system",
                 "content": "You are an expert XAUUSD gold trader. Always respond with valid JSON only. Never echo back context text."},
                {"role": "user", "content": prompt},
            ],
            "stream":       False,
            "options":      {"num_predict": max_tokens, "temperature": 0.2},
        }
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{_ollama_url()}/api/chat", json=payload)
            if r.status_code == 200:
                text = r.json().get("message", {}).get("content", "").strip()
                if text:
                    logger.debug(f"Ollama ({model}) ✅")
                    return text
            else:
                logger.debug(f"Ollama {r.status_code}: {r.text[:60]}")
    except Exception as e:
        # Ollama not running — silent, fall through to cloud APIs
        logger.debug(f"Ollama not available: {str(e)[:50]}")
    return ""


async def ollama_is_running() -> bool:
    """Quick health-check — returns True if Ollama server is up."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(f"{_ollama_url()}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


# ── Gemini (Google cloud) ─────────────────────────────────────────

async def gemma_call(prompt: str, api_key: str,
                     max_tokens: int = 500,
                     model: str = GEMMA_MODEL) -> str:
    """
    Google Gemini call — free tier (15 req/min, 1M tokens/day).
    Tries Flash first, falls back to Pro.
    thinkingBudget=0 forces all tokens into the response.
    """
    import httpx

    for attempt_model in [model, GEMMA_MODEL_PRO]:
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{attempt_model}:generateContent?key={api_key}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature":     0.15,
                    "maxOutputTokens": max_tokens,
                    "thinkingConfig":  {"thinkingBudget": 0},
                },
            }
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    text = (data.get("candidates", [{}])[0]
                                .get("content", {})
                                .get("parts", [{}])[0]
                                .get("text", "")).strip()
                    if text:
                        logger.debug(f"Gemini ({attempt_model}) ✅")
                        return text
                else:
                    logger.debug(f"Gemini {attempt_model} {r.status_code}: {r.text[:80]}")
        except Exception as e:
            logger.debug(f"Gemini {attempt_model} error: {str(e)[:60]}")

    return ""


# ── Main call — full priority chain ──────────────────────────────

async def agent_call(prompt: str, max_tokens: int = 500) -> str:
    """
    Priority chain:
      1. Ollama  (local - free, unlimited)
      2. Groq    (cloud - free, fast)
      3. Gemini  (cloud - free, 2 keys)
      4. Claude  (paid - last resort)
    """
    from config.settings import settings

    # 1. Ollama (local - primary, no rate limits)
    result = await ollama_call(prompt, max_tokens)
    if result:
        return result

    # 2. Groq (free cloud, fast)
    if settings.grok_api_key:
        for groq_attempt in range(2):
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(
                    base_url="https://api.groq.com/openai/v1",
                    api_key=settings.grok_api_key,
                )
                r = await client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": "You are an expert XAUUSD gold trader. Always respond with valid JSON only. Never echo back context text."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
                result = r.choices[0].message.content.strip()
                if result:
                    logger.debug("Groq OK")
                    return result
                break
            except Exception as e:
                err = str(e)
                if "429" in err and groq_attempt == 0:
                    logger.debug("Groq 429 - waiting 3s then retry")
                    await asyncio.sleep(3)
                    continue
                logger.warning(f"Groq failed: {err[:50]}")
                break

    # 3. Gemini - rotates Key1 to Key2
    gemini_keys = [k for k in [settings.google_ai_api_key, settings.google_ai_api_key_2] if k]
    for i, gkey in enumerate(gemini_keys):
        result = await gemma_call(prompt, gkey, max_tokens)
        if result:
            logger.debug(f"Gemini key{i+1} OK")
            return result

    # 4. Claude (paid - last resort)
    if settings.anthropic_api_key:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            r = await client.messages.create(
                model=settings.claude_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            logger.debug("Claude OK")
            return r.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude failed: {str(e)[:60]}")

    return ""



async def nvidia_call(prompt: str, api_key: str = "", max_tokens: int = 500) -> str:
    return await agent_call(prompt, max_tokens)

async def grok_call(prompt: str, api_key: str = "", max_tokens: int = 500) -> str:
    return await agent_call(prompt, max_tokens)
