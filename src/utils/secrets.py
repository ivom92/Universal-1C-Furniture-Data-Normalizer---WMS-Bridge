"""Secret masking utilities — safe display of API keys and tokens in UI / logs."""

from __future__ import annotations


def mask_secret(val: str, visible_chars: int = 6) -> str:
    """Return a masked version of a secret value suitable for UI display.

    Keeps only the first ``visible_chars`` characters visible so a human can
    verify which key is active without exposing the full credential.

    Examples::

        mask_secret("AIzaSyABCDEFghijklmn")  -> "AIzaSy…***"
        mask_secret("", 6)                    -> "—"
        mask_secret("short")                  -> "***"
    """
    cleaned = (val or "").strip()
    if not cleaned:
        return "—"
    if len(cleaned) <= visible_chars:
        return "***"
    return f"{cleaned[:visible_chars]}…***"
