"""Unit tests for Telegram telemetry (no live HTTP) and rotating file logs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils.logger import LOG_FILENAME, get_logger, setup_file_logging
from src.utils.telemetry import (
    CardType,
    classify_order_card,
    flush_telemetry,
    notify_batch_completion,
    notify_error,
    notify_order_processed,
    notify_startup,
    render_batch_card,
    render_error_card,
    render_success_card,
    render_warning_card,
    reset_startup_latch,
    send_telegram_alert,
    send_telegram_document,
    telemetry_enabled,
)


def test_telemetry_disabled_when_no_token(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telemetry_enabled() is False
    with patch("src.utils.telemetry.httpx.Client") as client_cls:
        send_telegram_alert("should-not-send", level="INFO")
        client_cls.assert_not_called()


def test_rotating_file_handler(tmp_path: Path) -> None:
    log_path = setup_file_logging(tmp_path)
    assert log_path.name == LOG_FILENAME
    logger = get_logger()
    logger.info("warehouse-rotating-handler-ok")
    logger.debug("warehouse-debug-blackbox")
    for handler in logger.handlers:
        handler.flush()
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "warehouse-rotating-handler-ok" in text
    assert "warehouse-debug-blackbox" in text

    project_log = setup_file_logging()
    assert project_log.parent.name == "logs"
    assert project_log.name == "warehouse_app.log"
    logger.info("warehouse-app-log-write")
    for handler in logger.handlers:
        handler.flush()
    assert project_log.exists()
    assert "warehouse-app-log-write" in project_log.read_text(encoding="utf-8")


def test_telegram_post_is_background_and_swallows_errors(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    def _boom(*_args, **_kwargs):
        raise OSError("no network")

    with patch("src.utils.telemetry.httpx.Client", side_effect=_boom):
        send_telegram_alert("ping", level="INFO")

    flush_telemetry(timeout=1.0)


def test_telegram_payload_uses_send_message(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")

    response = MagicMock()
    response.status_code = 200
    client = MagicMock()
    client.post.return_value = response
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("src.utils.telemetry.httpx.Client", return_value=client):
        from src.utils.telemetry import _post_telegram

        _post_telegram("hello")

    client.post.assert_called()
    args, kwargs = client.post.call_args
    assert "api.telegram.org/bottest-token/sendMessage" in args[0]
    assert kwargs["json"]["chat_id"] == "275"
    assert kwargs["json"]["text"] == "hello"
    assert kwargs["json"]["parse_mode"] == "HTML"


def test_send_telegram_document_payload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")

    log_file = tmp_path / "warehouse_app.log"
    log_file.write_text("warehouse diagnostic line\n", encoding="utf-8")

    response = MagicMock()
    response.status_code = 200
    client = MagicMock()
    client.post.return_value = response
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("src.utils.telemetry.httpx.Client", return_value=client):
        from src.utils.telemetry import _post_telegram_document

        _post_telegram_document(log_file, "caption-text")

    client.post.assert_called()
    args, kwargs = client.post.call_args
    assert "api.telegram.org/bottest-token/sendDocument" in args[0]
    assert kwargs["data"]["chat_id"] == "275"
    assert kwargs["data"]["caption"] == "caption-text"
    assert "document" in kwargs["files"]
    filename, _handle, content_type = kwargs["files"]["document"]
    assert filename == "warehouse_app.log"
    assert content_type == "application/octet-stream"


def test_send_telegram_document_skipped_without_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    log_file = tmp_path / "warehouse_app.log"
    log_file.write_text("x\n", encoding="utf-8")
    with patch("src.utils.telemetry.httpx.Client") as client_cls:
        send_telegram_document(log_file, "nope")
        client_cls.assert_not_called()


def test_success_card_html() -> None:
    stats = {
        "customer_name": "РС УрФО Империал",
        "total_rows": 384,
        "total_places": 871,
        "matched_auto": 377,
        "auto_pct": 98.2,
        "matched_llm": 6,
        "quarantine_count": 0,
        "no_barcode_count": 31,
        "elapsed_sec": 12.4,
    }
    body = render_success_card("order_transfering_01_09.xls", stats)
    assert "🟢 <b>Заказ обработан:</b> <code>order_transfering_01_09.xls</code>" in body
    assert "<b>Заказчик:</b> РС УрФО Империал" in body
    assert "<b>12.4s</b>" in body
    assert "<code>384</code> строк / <code>871</code> мест" in body
    assert "Сопоставлено (с ШК): <b>377</b> (98.2%)" in body
    assert "Через LLM: <b>6</b>" in body
    assert "Без ШК (фурнитура): <b>31</b>" in body
    assert "Карантин: <b>0</b>" in body
    assert "WMS-файл сформирован успешно" in body
    assert classify_order_card(stats) is CardType.SUCCESS


def test_warning_card_html() -> None:
    stats = {
        "customer_name": "ИП Тестов",
        "quarantine_count": 2,
        "quarantine_items": [
            {"line": 12, "name": "Стекло 116х596", "reason": "Нестандартный заказной размер"},
            {"line": 44, "name": "Фасад Равенна", "reason": "Отсутствует в каталоге фабрики"},
        ],
        "checksum_mismatch": True,
        "declared_places": 10,
        "parsed_places": 8,
    }
    body = render_warning_card("order_soft.xls", stats)
    assert "🟡 <b>ВНИМАНИЕ: Заказ требует ручного контроля!</b>" in body
    assert "<code>order_soft.xls</code>" in body
    assert "<b>Позиций в Карантине: 2</b>" in body
    assert "Строка #12: Стекло 116х596 — [Нестандартный заказной размер]" in body
    assert "Строка #44:" in body
    assert "Расхождение сумм мест" in body
    assert "Сводка_Отбора" in body
    assert classify_order_card(stats) is CardType.WARNING


def test_error_card_html() -> None:
    body = render_error_card("bad.xls", "RuntimeError", "Zero-Loss нарушен")
    assert "🚨 <b>КРИТИЧЕСКИЙ СБОЙ ОБРАБОТКИ</b>" in body
    assert "<code>bad.xls</code>" in body
    assert "<code>RuntimeError: Zero-Loss нарушен</code>" in body
    assert "Файл лога прикреплен ниже" in body


def test_batch_card_html() -> None:
    items = [
        {"filename": "a.xls", "card_type": "SUCCESS", "places": 10, "customer_name": "А"},
        {"filename": "b.xls", "card_type": "WARNING", "places": 3, "customer_name": "Б"},
        {"filename": "c.xls", "card_type": "ERROR", "places": 0},
    ]
    body = render_batch_card(items)
    assert "📦 <b>Пакет обработан:</b> <code>3</code> файл(ов)" in body
    assert "🟢 <code>a.xls</code>" in body
    assert "🟡 <code>b.xls</code>" in body
    assert "🔴 <code>c.xls</code>" in body
    assert "<b>Всего мест:</b> <code>13</code>" in body


def test_notify_order_processed_uses_success_card(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")
    stats = {
        "customer_name": "РС УрФО Империал",
        "total_rows": 384,
        "total_places": 871,
        "matched_auto": 377,
        "auto_pct": 98.2,
        "matched_llm": 6,
        "quarantine_count": 0,
        "no_barcode_count": 31,
        "elapsed_sec": 12.4,
    }
    with patch("src.utils.telemetry.send_telegram_alert") as mocked:
        notify_order_processed("order_transfering_01_09.xls", stats)
    mocked.assert_called_once()
    body = mocked.call_args[0][0]
    assert "🟢 <b>Заказ обработан:</b>" in body
    assert "<code>order_transfering_01_09.xls</code>" in body


def test_notify_order_processed_uses_warning_card(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")
    stats = {
        "customer_name": "РС УрФО Империал",
        "total_rows": 384,
        "matched_auto": 377,
        "quarantine_count": 1,
        "quarantine_items": [{"line": 9, "name": "Стекло", "reason": "custom size"}],
        "elapsed_sec": 12.4,
    }
    with patch("src.utils.telemetry.send_telegram_alert") as mocked:
        notify_order_processed("order_transfering_01_09.xls", stats)
    body = mocked.call_args[0][0]
    assert "ВНИМАНИЕ: Заказ требует ручного контроля" in body
    assert "Строка #9:" in body


def test_notify_batch_completion(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")
    items = [
        {"filename": "one.xls", "card_type": CardType.SUCCESS.value, "places": 5},
        {"filename": "two.xls", "card_type": CardType.WARNING.value, "places": 2},
    ]
    with patch("src.utils.telemetry.send_telegram_alert") as mocked:
        notify_batch_completion(items)
    mocked.assert_called_once()
    body = mocked.call_args[0][0]
    assert "Пакет обработан" in body
    assert "one.xls" in body
    assert "two.xls" in body


def test_notify_error_attaches_log(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")
    log_file = tmp_path / "warehouse_app.log"
    log_file.write_text("trace\n", encoding="utf-8")
    with patch("src.utils.telemetry.send_telegram_alert") as alert:
        with patch("src.utils.telemetry.send_telegram_document") as doc:
            notify_error("ValueError: boom", "Traceback...", filename="x.xls", log_path=log_file)
    alert.assert_called_once()
    assert "КРИТИЧЕСКИЙ СБОЙ" in alert.call_args[0][0]
    doc.assert_called_once()
    assert doc.call_args[0][0] == log_file


def test_startup_latch_sends_http_once(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "275")
    reset_startup_latch()

    response = MagicMock()
    response.status_code = 200
    client = MagicMock()
    client.post.return_value = response
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("src.utils.telemetry.httpx.Client", return_value=client):
        notify_startup({"os": "Windows", "catalog": 100})
        notify_startup({"os": "Windows", "catalog": 100})
        flush_telemetry(timeout=2.0)

    assert client.post.call_count == 1
    payload = client.post.call_args.kwargs["json"]["text"]
    assert "WMS Parser запущен" in payload
    reset_startup_latch()
