"""Gemini API client helpers with native ``x-goog-api-key`` auth (AQ. keys)."""

from __future__ import annotations

from typing import Optional

import httpx

from src.config import get_config

_GEMINI_API_ROOT = "https://generativelanguage.googleapis.com"


def resolve_gemini_base_url(base_url: Optional[str] = None) -> Optional[str]:
    """Return effective Gemini HTTP base URL (Cloudflare proxy or direct Google)."""
    if base_url is not None:
        cleaned = base_url.strip().rstrip("/")
        return cleaned or None
    return get_config().gemini_base_url


def gemini_auth_headers(api_key: str) -> dict[str, str]:
    """Return native Gemini auth headers.

    AQ.-prefixed Google AI Studio keys must be sent as ``x-goog-api-key``,
    never as ``Authorization: Bearer`` (Google treats Bearer as OAuth 2.0).
    """
    key = api_key.strip()
    return {"x-goog-api-key": key}


def gemini_auth_query_params(api_key: str) -> dict[str, str]:
    """Return native Gemini auth query params for REST probes."""
    return {"key": api_key.strip()}


def gemini_models_list_url(base_url: Optional[str] = None) -> str:
    """URL for the Gemini ``models`` list probe (direct Google or reverse-proxy)."""
    root = (base_url or _GEMINI_API_ROOT).rstrip("/")
    return f"{root}/v1beta/models"


def probe_gemini_models_list(
    api_key: str,
    *,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> httpx.Response:
    """GET ``/v1beta/models`` using native Gemini API-key auth."""
    effective_base_url = resolve_gemini_base_url(base_url)
    url = gemini_models_list_url(effective_base_url)
    with httpx.Client(timeout=timeout) as client:
        return client.get(url, headers=gemini_auth_headers(api_key))


def build_gemini_client(
    api_key: Optional[str],
    *,
    timeout: float,
    base_url: Optional[str] = None,
):
    """Construct ``google.genai.Client`` with native ``x-goog-api-key`` auth."""
    from google import genai
    from google.genai import types

    if not api_key or not api_key.strip():
        raise ValueError("Gemini API key is required")

    key = api_key.strip()
    timeout_ms = max(1, int(timeout * 1000))
    http_options_kwargs: dict[str, object] = {
        "timeout": timeout_ms,
        "headers": gemini_auth_headers(key),
    }
    if base_url:
        http_options_kwargs["base_url"] = base_url.rstrip("/")
    else:
        configured_base_url = resolve_gemini_base_url()
        if configured_base_url:
            http_options_kwargs["base_url"] = configured_base_url

    http_options = types.HttpOptions(**http_options_kwargs)
    return genai.Client(api_key=key, http_options=http_options)
