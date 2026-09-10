import json
import logging
import os
from typing import Any, Dict, List, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger(__name__)


def _provider_order() -> List[str]:
    order = os.getenv("LLM_PROVIDER_ORDER", "gemini,claude")
    return [item.strip().lower() for item in order.split(",") if item.strip()]


def _safe_text_from_message(msg: Dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _gemini_call(system: str, mensagens: Sequence[Dict[str, Any]], max_tokens: int) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    contents = []
    for msg in mensagens:
        role = msg.get("role", "user")
        if role == "assistant":
            role = "model"
        contents.append({
            "role": role,
            "parts": [{"text": _safe_text_from_message(msg)}],
        })

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API failed: {exc.code} {detail}") from exc

    candidates = body.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini API returned no candidates: {body}")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    return text or json.dumps(body)


def _anthropic_call(system: str, mensagens: Sequence[Dict[str, Any]], max_tokens: int) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [
            {"role": msg.get("role", "user"), "content": _safe_text_from_message(msg)}
            for msg in mensagens
        ],
    }

    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API failed: {exc.code} {detail}") from exc

    text_blocks = []
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text_blocks.append(block.get("text", ""))
    return "\n".join(text_blocks) or json.dumps(body)


def chamar_llm(system: str, mensagens: Sequence[Dict[str, Any]], max_tokens: int = 8000) -> str:
    """Tenta os provedores configurados e devolve texto legível mesmo sem credenciais."""
    if not mensagens:
        return ""

    for provider in _provider_order():
        try:
            if provider == "gemini":
                return _gemini_call(system, mensagens, max_tokens)
            if provider == "claude":
                return _anthropic_call(system, mensagens, max_tokens)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM provider %s falhou: %s", provider, exc)

    if not os.getenv("GEMINI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        return (
            "Serviço de IA indisponível no ambiente atual. "
            "Configure GEMINI_API_KEY ou ANTHROPIC_API_KEY para ativar o agente."
        )

    raise RuntimeError("Todos os provedores de IA falharam.")
