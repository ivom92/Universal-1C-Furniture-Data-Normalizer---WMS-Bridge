"""Contracts for batch scan-station isolation (Tab 2) and warehouse launcher files."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models import CatalogEntity, ExtractedFeatures, MatchDecision, RawOrderBlock
from src.utils.scan_station import (
    attention_by_order,
    format_station_order_label,
    partition_scan_attention,
    station_status_badge,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _block(description: str, line_number: int, quantity: int = 1) -> RawOrderBlock:
    return RawOrderBlock(
        line_number=line_number,
        client_description=description,
        item_type="Пачка",
        quantity=quantity,
        factory_alias=description,
        order_service_line="Продажи оптовые УРП_ test",
        excel_row_start=line_number,
    )


def _entity(*, nomenclature: str, barcode: str | None) -> CatalogEntity:
    return CatalogEntity.model_validate(
        {
            "Номенклатура": nomenclature,
            "НоменклатураКод": "00000010001",
            "Штрихкод": barcode,
            "Упаковка": "1/1",
        }
    )


def _decision(
    *,
    description: str,
    line_number: int,
    status: str,
    entity: CatalogEntity | None,
    match_method: str,
    quantity: int = 1,
) -> MatchDecision:
    return MatchDecision(
        raw_block=_block(description, line_number, quantity),
        extracted_features=ExtractedFeatures(),
        status=status,
        matched_entity=entity,
        confidence_score=1.0,
        match_method=match_method,
    )


def _hardware_no_barcode(count: int = 31) -> list[MatchDecision]:
    """Synthetic analogue of «Перемещение 01.09.xls» hardware without factory EAN."""
    rows: list[MatchDecision] = []
    for index in range(1, count + 1):
        rows.append(
            _decision(
                description=f"Фурнитура {index}",
                line_number=index,
                status="MATCHED_AUTO",
                entity=_entity(nomenclature=f"Фурнитура {index} 1/1", barcode=None),
                match_method="AUTO_NO_BARCODE",
            )
        )
    return rows


def test_batch_scan_attention_isolated_across_three_orders() -> None:
    hardware = _hardware_no_barcode(31)
    cabinet_ok = [
        _decision(
            description="Кухня",
            line_number=1,
            status="MATCHED_AUTO",
            entity=_entity(nomenclature="Кухня 1/1", barcode="2006000045445"),
            match_method="vector_auto",
        ),
        _decision(
            description="Пенал",
            line_number=2,
            status="MATCHED_AUTO",
            entity=_entity(nomenclature="Пенал 1/1", barcode="2006000045446"),
            match_method="exact_article",
        ),
    ]
    mixed = [
        _decision(
            description="Зеркало заказное",
            line_number=1,
            status="QUARANTINE",
            entity=None,
            match_method="QUARANTINE",
        ),
        _decision(
            description="Полка стекло",
            line_number=2,
            status="MATCHED_AUTO",
            entity=_entity(nomenclature="Полка стекло", barcode=None),
            match_method="AUTO_NO_BARCODE",
        ),
        _decision(
            description="Шкаф",
            line_number=3,
            status="MATCHED_AUTO",
            entity=_entity(nomenclature="Шкаф 1/1", barcode="2006000045999"),
            match_method="vector_auto",
        ),
    ]

    batch = [
        {
            "order_id": "ord-hardware",
            "filename": "Перемещение 01.09.xls",
            "decisions": hardware,
            "overrides": {},
        },
        {
            "order_id": "ord-cabinet",
            "filename": "Отборочная-кухня.xls",
            "decisions": cabinet_ok,
            "overrides": {},
        },
        {
            "order_id": "ord-mixed",
            "filename": "Отборочная-смесь.xls",
            "decisions": mixed,
            "overrides": {},
        },
    ]
    isolated = attention_by_order(batch)

    assert list(isolated) == ["ord-hardware", "ord-cabinet", "ord-mixed"]
    assert isolated["ord-hardware"]["filename"] == "Перемещение 01.09.xls"
    assert isolated["ord-hardware"]["quarantine"] == []
    assert len(isolated["ord-hardware"]["no_barcode"]) == 31
    assert isolated["ord-hardware"]["no_barcode_lines"] == list(range(1, 32))

    assert isolated["ord-cabinet"]["quarantine"] == []
    assert isolated["ord-cabinet"]["no_barcode"] == []

    assert isolated["ord-mixed"]["quarantine_lines"] == [1]
    assert isolated["ord-mixed"]["no_barcode_lines"] == [2]
    assert station_status_badge(isolated["ord-mixed"]["quarantine"][0]) == "🟡 Карантин"
    assert station_status_badge(isolated["ord-mixed"]["no_barcode"][0]) == "⚪ Без ШК"

    # Overlapping line №1 is QUARANTINE only in the mixed order, hardware in the hardware order.
    hw_q, hw_nb = partition_scan_attention(hardware, {})
    mix_q, mix_nb = partition_scan_attention(mixed, {})
    assert {int(row.order_line_number) for row in hw_q} == set()
    assert 1 in {int(row.order_line_number) for row in hw_nb}
    assert {int(row.order_line_number) for row in mix_q} == {1}
    assert 1 not in {int(row.order_line_number) for row in mix_nb}


def test_operator_override_removes_row_only_for_that_order() -> None:
    hardware = _hardware_no_barcode(3)
    mixed = [
        _decision(
            description="Зеркало заказное",
            line_number=1,
            status="QUARANTINE",
            entity=None,
            match_method="QUARANTINE",
        )
    ]
    isolated = attention_by_order(
        [
            {
                "order_id": "a",
                "filename": "Перемещение 01.09.xls",
                "decisions": hardware,
                "overrides": {1: "1234567890123"},
            },
            {
                "order_id": "b",
                "filename": "карантин.xls",
                "decisions": mixed,
                "overrides": {},
            },
        ]
    )
    assert isolated["a"]["no_barcode_lines"] == [2, 3]
    assert isolated["b"]["quarantine_lines"] == [1]


def test_station_order_label_includes_counts() -> None:
    label = format_station_order_label(
        "Перемещение 01.09.xls",
        total_rows=31,
        total_places=31,
        quarantine_count=0,
    )
    assert label == "Перемещение 01.09.xls (Строк: 31 | Мест: 31 | Карантин: 0)"


def test_silent_launcher_and_warehouse_readme_exist() -> None:
    vbs = PROJECT_ROOT / "Запуск_WMS.vbs"
    readme = PROJECT_ROOT / "README_СКЛАД.txt"
    assert vbs.is_file()
    text = vbs.read_text(encoding="utf-8")
    assert "WScript.Shell" in text
    assert "sh.Run cmd, 0, False" in text
    assert "http://localhost:8501" in text
    assert "streamlit run app_ui.py" in text
    assert "netstat" in text.lower()
    assert readme.is_file()
    readme_text = readme.read_text(encoding="utf-8")
    assert "Запуск_WMS.vbs" in readme_text
    assert "Остановить_WMS.vbs" in readme_text
    assert "3_ОСТАНОВИТЬ.bat" in readme_text


def test_stop_scripts_and_idempotent_launch_bat() -> None:
    stop_bat = PROJECT_ROOT / "3_ОСТАНОВИТЬ.bat"
    stop_vbs = PROJECT_ROOT / "Остановить_WMS.vbs"
    launch_bat = PROJECT_ROOT / "2_ЗАПУСК.bat"
    assert stop_bat.is_file()
    assert stop_vbs.is_file()
    stop_bat_text = stop_bat.read_text(encoding="utf-8")
    assert "8501" in stop_bat_text
    assert "taskkill" in stop_bat_text.lower()
    assert "Сервер WMS" in stop_bat_text
    stop_vbs_text = stop_vbs.read_text(encoding="utf-8")
    assert "8501" in stop_vbs_text
    assert "taskkill" in stop_vbs_text.lower()
    assert "sh.Run" in stop_vbs_text
    launch_bat_text = launch_bat.read_text(encoding="utf-8")
    assert "netstat" in launch_bat_text.lower()
    assert "8501" in launch_bat_text
    assert "http://localhost:8501" in launch_bat_text
    assert "venv\\Scripts\\python.exe" in launch_bat_text
    assert "PYTHONLEGACYWINDOWSSTDIO=1" in launch_bat_text


def test_bat_launchers_windows8_cwd_and_console() -> None:
    for name in ("1_УСТАНОВКА.bat", "2_ЗАПУСК.bat", "3_ОСТАНОВИТЬ.bat"):
        path = PROJECT_ROOT / name
        text = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        assert lines[0] == "cd /d \"%~dp0\"", f"{name}: cwd fix must be first line"
        assert "chcp 65001 >nul 2>&1" in text, f"{name}: safe chcp redirect"
    setup_bat = (PROJECT_ROOT / "1_УСТАНОВКА.bat").read_text(encoding="utf-8")
    assert "venv\\Scripts\\python.exe" in setup_bat
    readme = (PROJECT_ROOT / "README_СКЛАД.txt").read_text(encoding="utf-8")
    assert "Windows 8" in readme


def test_app_ui_has_shutdown_button() -> None:
    app_ui = (PROJECT_ROOT / "app_ui.py").read_text(encoding="utf-8")
    assert "Завершить работу сервера" in app_ui
    assert "_shutdown_server" in app_ui
    assert "signal.SIGTERM" in app_ui or "os._exit" in app_ui


def test_app_ui_gemini_status_uses_key_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEYS", "key-one, key-two, key-three")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    import app_ui

    available, status, caption = app_ui._llm_status_info("gemini")
    assert available is True
    assert "🟢 LLM: доступен (Пул: 3 шт.)" == status
    assert "Пул ключей: 3 шт." in caption
    assert "Модель:" in caption


def test_app_ui_gemini_status_empty_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    import app_ui

    available, status, caption = app_ui._llm_status_info("gemini")
    assert available is False
    assert "🔴 LLM: недоступен" in status
    assert "Gemini:" in caption


def test_app_ui_has_llm_key_ping_button() -> None:
    app_ui = (PROJECT_ROOT / "app_ui.py").read_text(encoding="utf-8")
    assert "KeyPool.from_env" in app_ui
    assert "Проверить ключи" in app_ui
    assert "test_connection" in app_ui


def test_build_dist_includes_lifecycle_scripts() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_warehouse_dist.py").read_text(
        encoding="utf-8"
    )
    assert "3_ОСТАНОВИТЬ.bat" in build_script
    assert "Остановить_WMS.vbs" in build_script
