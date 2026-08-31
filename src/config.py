"""Application configuration loaded from environment variables.

Uses Pydantic v2 BaseModel for structured, type-safe config.
Hydrated from the process environment on each call to ``get_config()``.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field


class AppConfig(BaseModel):
    """Runtime configuration for the WMS Bridge application."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    warehouse_pin: str = Field(
        default="",
        alias="WAREHOUSE_PIN",
        description=(
            "Warehouse access PIN. When empty, authentication is disabled "
            "(local dev / offline tests). Set via WAREHOUSE_PIN env var."
        ),
    )


def get_config() -> AppConfig:
    """Return :class:`AppConfig` hydrated from the current process environment."""
    return AppConfig.model_validate(
        {"WAREHOUSE_PIN": os.environ.get("WAREHOUSE_PIN", "")}
    )
