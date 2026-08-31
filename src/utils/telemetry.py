"""Best-effort Telegram alerts; never raise into the warehouse UI/CLI."""

from __future__ import annotations

import html
import os
import platform
import threading
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from src.utils.logger import default_log_path, get_logger

_TELEGRAM_TIMEOUT = 3.0
_TELEGRAM_DOC_TIMEOUT = 20.0
_MAX_TEXT = 3900
_MAX_CAPTION = 1024
_QUARANTINE_SNIPPET_LIMIT = 5
_pending: list[threading.Thread] = []
_lock = threading.Lock()
_STARTUP_NOTIFIED = False


class CardType(str, Enum):
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def telemetry_enabled() -> bool:
    return bool(_token() and _chat_id())


def _html_escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _level_prefix(level: str) -> str:
    upper = (level or "INFO").upper()
    if upper in {"ERROR", "CRITICAL", "FATAL"}:
        return "🔴"
    if upper in {"WARNING", "WARN"}:
        return "🟡"
    return "🟢"


def _enqueue(target: Any, *args: Any) -> None:
    thread = threading.Thread(target=target, args=args, daemon=True)
    with _lock:
        _pending.append(thread)
    thread.start()


def _post_telegram(text: str) -> None:
    token = _token()
    chat_id = _chat_id()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:_MAX_TEXT],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        with httpx.Client(timeout=_TELEGRAM_TIMEOUT) as client:
            response = client.post(url, json=payload)
            if response.status_code >= 400:
                fallback = {
                    "chat_id": chat_id,
                    "text": text[:_MAX_TEXT],
                    "disable_web_page_preview": True,
                }
                client.post(url, json=fallback)
    except Exception:
        return


def _post_telegram_document(file_path: Path, caption: str) -> None:
    token = _token()
    chat_id = _chat_id()
    path = Path(file_path)
    if not token or not chat_id or not path.is_file():
        return
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    data = {
        "chat_id": chat_id,
        "caption": (caption or "")[:_MAX_CAPTION],
        "parse_mode": "HTML",
    }
    try:
        with path.open("rb") as handle:
            files = {"document": (path.name, handle, "application/octet-stream")}
            with httpx.Client(timeout=_TELEGRAM_DOC_TIMEOUT) as client:
                response = client.post(url, data=data, files=files)
                if response.status_code >= 400:
                    handle.seek(0)
                    client.post(
                        url,
                        data={"chat_id": chat_id, "caption": (caption or "")[:_MAX_CAPTION]},
                        files={"document": (path.name, handle, "application/octet-stream")},
                    )
    except Exception:
        return


def send_telegram_alert(message: str, level: str = "INFO") -> None:
    """Fire-and-forget POST. Missing token or network errors are ignored."""
    if not telemetry_enabled():
        get_logger().info("Telegram telemetry skipped (no token/chat id)")
        return
    body = message if message.startswith(("🟢", "🟡", "🔴", "🚨", "📦", "📤")) else (
        f"{_level_prefix(level)} {message}"
    )
    get_logger().info("Telegram alert queued (%s)", level)
    _enqueue(_post_telegram, body)


def send_telegram_document(file_path: Path, caption: str) -> None:
    """Fire-and-forget sendDocument. Missing token, file, or network errors are ignored."""
    path = Path(file_path)
    if not telemetry_enabled():
        get_logger().info("Telegram document skipped (no token/chat id)")
        return
    if not path.is_file():
        get_logger().info("Telegram document skipped (missing file): %s", path)
        return
    get_logger().info("Telegram document queued: %s", path.name)
    _enqueue(_post_telegram_document, path, caption or "")


def flush_telemetry(timeout: float = 3.5) -> None:
    """Wait briefly so CLI processes can finish in-flight alerts."""
    with _lock:
        threads = list(_pending)
        _pending.clear()
    for thread in threads:
        thread.join(timeout=timeout)


def reset_startup_latch() -> None:
    """Test helper: allow notify_startup to send again in the current process."""
    global _STARTUP_NOTIFIED
    with _lock:
        _STARTUP_NOTIFIED = False


def notify_startup(system_info: Mapping[str, Any] | None = None) -> None:
    global _STARTUP_NOTIFIED
    with _lock:
        if _STARTUP_NOTIFIED:
            get_logger().debug("Startup Telegram latch: skip duplicate notify_startup")
            return
        _STARTUP_NOTIFIED = True
    info = dict(system_info or {})
    os_label = str(info.get("os") or f"{platform.system()} {platform.release()}")
    catalog = info.get("catalog", info.get("catalog_size", "?"))
    message = (
        f"🟢 <b>WMS Parser запущен</b> | ОС: {_html_escape(os_label)} | "
        f"Каталог: {_html_escape(catalog)}"
    )
    get_logger().info("Startup: OS=%s catalog=%s", os_label, catalog)
    send_telegram_alert(message, level="INFO")


def classify_order_card(stats: Mapping[str, Any] | None = None) -> CardType:
    data = dict(stats or {})
    quarantine_count = int(data.get("quarantine_count", data.get("quarantine", 0)) or 0)
    checksum_mismatch = bool(data.get("checksum_mismatch", False))
    if quarantine_count > 0 or checksum_mismatch:
        return CardType.WARNING
    return CardType.SUCCESS


def render_success_card(filename: str, stats: Mapping[str, Any]) -> str:
    customer = _html_escape(stats.get("customer_name") or "—")
    elapsed = _as_float(stats.get("elapsed_sec", stats.get("elapsed", 0)))
    rows = int(stats.get("total_rows", stats.get("rows", 0)) or 0)
    places = int(stats.get("total_places", stats.get("places", 0)) or 0)
    auto_count = int(stats.get("matched_auto", stats.get("auto_count", 0)) or 0)
    llm_count = int(stats.get("matched_llm", stats.get("llm_count", 0)) or 0)
    no_barcode_count = int(stats.get("no_barcode_count", 0) or 0)
    auto_pct = stats.get("auto_pct")
    if auto_pct is None:
        auto_pct = round(100.0 * auto_count / rows, 1) if rows else 0.0
    auto_pct_f = _as_float(auto_pct)
    return (
        f"🟢 <b>Заказ обработан:</b> <code>{_html_escape(filename)}</code>\n"
        f"🏢 <b>Заказчик:</b> {customer} | ⏱️ <b>{elapsed:.1f}s</b>\n"
        f"📦 <b>Объем:</b> <code>{rows}</code> строк / <code>{places}</code> мест\n"
        f"───────────────\n"
        f"• Сопоставлено (с ШК): <b>{auto_count}</b> ({auto_pct_f:.1f}%)\n"
        f"• Через LLM: <b>{llm_count}</b>\n"
        f"• Без ШК (фурнитура): <b>{no_barcode_count}</b>\n"
        f"• Карантин: <b>0</b>\n"
        f"🚀 <i>WMS-файл сформирован успешно</i>"
    )


def render_warning_card(filename: str, stats: Mapping[str, Any]) -> str:
    customer = _html_escape(stats.get("customer_name") or "—")
    quarantine_count = int(stats.get("quarantine_count", stats.get("quarantine", 0)) or 0)
    snippet = str(stats.get("quarantine_lines_snippet") or "").strip()
    if not snippet:
        snippet = _format_quarantine_snippet(stats.get("quarantine_items") or [])
    extra = ""
    if stats.get("checksum_mismatch"):
        declared = stats.get("declared_places", "?")
        parsed = stats.get("parsed_places", stats.get("total_places", "?"))
        extra = (
            f"\n⚠️ <b>Расхождение сумм мест:</b> ИТОГО={_html_escape(declared)} "
            f"vs разобрано={_html_escape(parsed)}"
        )
    return (
        f"🟡 <b>ВНИМАНИЕ: Заказ требует ручного контроля!</b>\n"
        f"📄 <b>Файл:</b> <code>{_html_escape(filename)}</code> | 🏢 <b>{customer}</b>\n"
        f"───────────────\n"
        f"⚠️ <b>Позиций в Карантине: {quarantine_count}</b>\n"
        f"{snippet}"
        f"{extra}\n"
        f"───────────────\n"
        f"📥 <i>Оператор уведомлен на листе «Сводка_Отбора»</i>"
    )


def render_error_card(
    filename: str,
    error_type: str,
    error_msg: str,
) -> str:
    return (
        f"🚨 <b>КРИТИЧЕСКИЙ СБОЙ ОБРАБОТКИ</b>\n"
        f"📄 <b>Файл:</b> <code>{_html_escape(filename)}</code>\n"
        f"❌ <b>Ошибка:</b> <code>{_html_escape(error_type)}: {_html_escape(error_msg)}</code>\n"
        f"📎 <i>Файл лога прикреплен ниже</i>"
    )


def render_batch_card(items: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = [
        f"📦 <b>Пакет обработан:</b> <code>{len(items)}</code> файл(ов)",
        "───────────────",
    ]
    total_places = 0
    for item in items:
        filename = _html_escape(item.get("filename") or "—")
        emoji = _card_emoji(item.get("card_type") or item.get("status"))
        places = int(item.get("places", item.get("total_places", 0)) or 0)
        total_places += places
        customer = _html_escape(item.get("customer_name") or item.get("customer") or "")
        suffix = f" — {customer}" if customer and customer != "—" else ""
        lines.append(f"{emoji} <code>{filename}</code> | <b>{places}</b> мест{suffix}")
    lines.append("───────────────")
    lines.append(f"📦 <b>Всего мест:</b> <code>{total_places}</code>")
    return "\n".join(lines)


def notify_order_processed(filename: str, stats: Mapping[str, Any] | None = None) -> None:
    data = dict(stats or {})
    card_type = classify_order_card(data)
    if card_type is CardType.WARNING:
        message = render_warning_card(filename, data)
        level = "WARNING"
    else:
        message = render_success_card(filename, data)
        level = "INFO"
    get_logger().info(
        "Order processed: %s card=%s customer=%s rows=%s places=%s auto=%s llm=%s quarantine=%s",
        filename,
        card_type.value,
        data.get("customer_name") or "—",
        data.get("total_rows", data.get("rows", 0)),
        data.get("total_places", data.get("places", 0)),
        data.get("matched_auto", 0),
        data.get("matched_llm", 0),
        data.get("quarantine_count", data.get("quarantine", 0)),
    )
    send_telegram_alert(message, level=level)


def notify_batch_completion(items: Sequence[Mapping[str, Any]] | None = None) -> None:
    payload = list(items or [])
    if len(payload) <= 1:
        if payload:
            row = dict(payload[0])
            notify_order_processed(str(row.get("filename") or "unknown"), row)
        return
    message = render_batch_card(payload)
    get_logger().info("Batch processed: %s files", len(payload))
    send_telegram_alert(message, level="INFO")


def notify_error(
    error_title: str,
    traceback_str: str,
    *,
    filename: str | None = None,
    log_path: Path | None = None,
) -> None:
    file_label = filename or "—"
    error_type, error_msg = _split_error(error_title, traceback_str)
    message = render_error_card(file_label, error_type, error_msg)
    trace = traceback_str or ""
    if len(trace) > 3200:
        trace = trace[:3200] + "\n…"
    get_logger().error("%s\n%s", error_title, traceback_str)
    send_telegram_alert(message, level="ERROR")
    path = Path(log_path) if log_path is not None else default_log_path()
    caption = message[:_MAX_CAPTION]
    send_telegram_document(path, caption)


def notify_diagnostics(details: str) -> None:
    message = f"📤 <b>Диагностика склада</b>\n{_html_escape(details)}"
    get_logger().info("Operator requested diagnostics")
    send_telegram_alert(message, level="INFO")


def collect_system_info(*, catalog_size: int | None = None) -> dict[str, Any]:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "catalog": catalog_size if catalog_size is not None else "?",
        "machine": platform.machine(),
    }


def _barcode_present(value: object | None) -> bool:
    return bool(value is not None and str(value).strip())


def order_stats(
    decisions: list[Any],
    *,
    customer_name: str | None = None,
    elapsed_sec: float | None = None,
    filename: str | None = None,
    checksum_mismatch: bool = False,
    declared_places: int | None = None,
    parsed_places: int | None = None,
) -> dict[str, Any]:
    rows = len(decisions)
    places = 0
    quarantine = 0
    matched_auto = 0
    matched_llm = 0
    no_barcode = 0
    quarantine_items: list[dict[str, Any]] = []
    for decision in decisions:
        block = getattr(decision, "raw_block", None)
        if block is not None:
            places += int(getattr(block, "quantity", 0) or 0)
        status = getattr(decision, "status", "") or ""
        if status == "QUARANTINE":
            quarantine += 1
            quarantine_items.append(
                {
                    "line": getattr(block, "line_number", None) if block is not None else None,
                    "name": getattr(block, "client_description", "") if block is not None else "",
                    "reason": getattr(decision, "status_detail", None) or "QUARANTINE",
                }
            )
        elif status == "MATCHED_AUTO":
            matched_auto += 1
        elif status == "MATCHED_LLM":
            matched_llm += 1
        if status in {"MATCHED_AUTO", "MATCHED_LLM"}:
            entity = getattr(decision, "matched_entity", None)
            barcode = getattr(entity, "barcode", None) if entity is not None else None
            if not _barcode_present(barcode):
                no_barcode += 1
    auto_pct = round(100.0 * matched_auto / rows, 1) if rows else 0.0
    card_type = CardType.WARNING if quarantine > 0 or checksum_mismatch else CardType.SUCCESS
    stats: dict[str, Any] = {
        "filename": filename or "",
        "rows": rows,
        "places": places,
        "quarantine": quarantine,
        "total_rows": rows,
        "total_places": places,
        "matched_auto": matched_auto,
        "matched_llm": matched_llm,
        "quarantine_count": quarantine,
        "no_barcode_count": no_barcode,
        "auto_pct": auto_pct,
        "customer_name": customer_name or "—",
        "elapsed_sec": float(elapsed_sec or 0.0),
        "checksum_mismatch": checksum_mismatch,
        "declared_places": declared_places,
        "parsed_places": parsed_places if parsed_places is not None else places,
        "quarantine_items": quarantine_items,
        "quarantine_lines_snippet": _format_quarantine_snippet(quarantine_items),
        "card_type": card_type.value,
    }
    return stats


def _format_quarantine_snippet(items: Sequence[Mapping[str, Any]] | Sequence[Any]) -> str:
    if not items:
        return "• Карантин пуст"
    lines: list[str] = []
    for item in list(items)[:_QUARANTINE_SNIPPET_LIMIT]:
        if isinstance(item, Mapping):
            line_no = item.get("line") or item.get("n") or "?"
            name = _shorten(str(item.get("name") or item.get("nomenclature") or "—"), 80)
            reason = item.get("reason") or item.get("status_detail") or "QUARANTINE"
        else:
            line_no = "?"
            name = _shorten(str(item), 80)
            reason = "QUARANTINE"
        lines.append(
            f"• Строка #{_html_escape(line_no)}: {_html_escape(name)} — [{_html_escape(reason)}]"
        )
    remaining = len(items) - _QUARANTINE_SNIPPET_LIMIT
    if remaining > 0:
        lines.append(f"• … ещё {remaining}")
    return "\n".join(lines)


def _card_emoji(value: object) -> str:
    text = str(value or CardType.SUCCESS.value).upper()
    if text in {CardType.ERROR.value, "ERROR", "FAIL", "FAILED"}:
        return "🔴"
    if text in {CardType.WARNING.value, "WARNING", "WARN"}:
        return "🟡"
    return "🟢"


def _as_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _shorten(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _split_error(error_title: str, traceback_str: str) -> tuple[str, str]:
    title = (error_title or "Exception").strip()
    if ": " in title:
        left, right = title.split(": ", 1)
        if left:
            return left.strip()[-80:], right.strip()[:240]
    error_type = "Exception"
    if traceback_str:
        for raw in reversed(traceback_str.strip().splitlines()):
            line = raw.strip()
            if line and not line.startswith("File ") and "Error" in line:
                error_type = line.split(":")[0].strip()[:80]
                break
    return error_type, title[:240]
