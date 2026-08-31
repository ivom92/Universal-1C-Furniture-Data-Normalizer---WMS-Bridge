"""Application configuration loaded from environment variables.

Uses Pydantic v2 BaseModel for structured, type-safe config.
Hydrated from the process environment on each call to ``get_config()``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class AppConfig(BaseModel):
    """Runtime configuration for the WMS Bridge application."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    warehouse_pin: str = Field(default="", alias="WAREHOUSE_PIN")
    llm_provider: str = Field(default="gemini", alias="LLM_PROVIDER")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", alias="GEMINI_MODEL")
    gemini_base_url: Optional[str] = Field(default=None, alias="GEMINI_BASE_URL")
    gemini_api_keys_raw: str = Field(default="", alias="GEMINI_API_KEYS")
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(default=None, alias="TELEGRAM_CHAT_ID")

    @field_validator("gemini_base_url", mode="before")
    @classmethod
    def _normalize_gemini_base_url(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().rstrip("/")
        return text or None

    @property
    def gemini_api_keys(self) -> list[str]:
        raw = (self.gemini_api_keys_raw or "").strip()
        if not raw:
            raw = os.getenv("GEMINI_API_KEY", "").strip()
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]


def get_config() -> AppConfig:
    """Return :class:`AppConfig` hydrated from the current process environment."""
    base_url = os.getenv("GEMINI_BASE_URL") or os.getenv("GOOGLE_GENAI_BASE_URL")

    return AppConfig(
        WAREHOUSE_PIN=os.getenv("WAREHOUSE_PIN", ""),
        LLM_PROVIDER=os.getenv("LLM_PROVIDER", "gemini"),
        GEMINI_MODEL=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        GEMINI_BASE_URL=base_url,
        GEMINI_API_KEYS=os.getenv("GEMINI_API_KEYS", ""),
        TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN"),
        TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID"),
    )
