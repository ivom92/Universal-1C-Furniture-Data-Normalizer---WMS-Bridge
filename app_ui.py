"""Streamlit web UI for 1C v7.7 order normalization and WMS export."""

from __future__ import annotations

import os
import uuid
import warnings

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
warnings.filterwarnings("ignore", category=UserWarning)

import sys
import time
import traceback
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from src.adapters.wms_excel_adapter import WMSExcelAdapter
from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.hybrid_matcher import HybridMatcher
from src.matcher.key_rotator import KeyPool
from src.matcher.llm_resolver import LLMResolver, resolve_gemini_base_url
from src.matcher.vector_store import CatalogVectorStore
from src.models import MatchDecision
from src.parsers.v8_loader import load_catalog_v8
from src.pipeline import log_order_profiler, process_order
from src.utils.auth import BruteForceProtector, is_auth_required, verify_pin
from src.utils.history_manager import (
    HistoryManager,
    OrderRunMeta,
    build_order_run_meta,
    dump_decisions_for_session,
    load_decisions_from_session,
    shift_summary,
)
from src.utils.logger import default_log_path, get_logger
from src.utils.reporter import count_without_barcode, get_status_badge
from src.utils.secrets import mask_secret
from src.utils.scan_station import (
    format_station_order_label,
    partition_scan_attention,
    station_status_badge,
)
from src.utils.telemetry import (
    collect_system_info,
    notify_diagnostics,
    notify_error,
    notify_order_processed,
    notify_batch_completion,
    notify_startup,
    order_stats,
    send_telegram_document,
)

CATALOG_PATH = PROJECT_ROOT / "data" / "catalog_v8.xlsx"
CACHE_DIR = PROJECT_ROOT / ".cache"
_RESULT_KEYS = (
    "current_result",
    "batch_results",
    "operator_overrides",
    "operator_overrides_by_order",
    "decisions",
    "customer_name",
    "wms_bytes",
    "upload_name",
    "preview_parsed",
    "customer_wms_name",
    "document_type",
    "_upload_id",
    "_restore_banner",
)


def _render_pin_screen() -> None:
    """Render the PIN authentication gate (blocks the full app until correct PIN entered)."""
    st.title("🔐 Доступ к WMS Bridge (Склад Челябинск)")
    st.markdown(
        "Введите PIN-код склада для доступа к системе управления складом."
    )
    st.divider()

    protector: BruteForceProtector = st.session_state["_auth_protector"]

    if protector.is_locked_out():
        remaining = protector.seconds_remaining()
        st.error(
            f"🔒 Превышено количество попыток. "
            f"Доступ заблокирован. Повторите через **{remaining}** сек."
        )
        time.sleep(1)
        st.rerun()
        return

    if protector.failed_attempts > 0:
        attempts_left = protector.max_attempts - protector.failed_attempts
        st.warning(f"❌ Неверный PIN-код. Осталось попыток: {attempts_left}")

    with st.form("pin_login_form", clear_on_submit=True):
        pin_input = st.text_input(
            "PIN-код",
            type="password",
            placeholder="Введите PIN-код склада",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("🔓 Войти", type="primary", use_container_width=True)

    if submitted:
        target_pin = os.environ.get("WAREHOUSE_PIN", "")
        if verify_pin(pin_input, target_pin):
            protector.record_success()
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            protector.record_failure()
            st.rerun()


def _render_support_footer(catalog_size: int = 0) -> None:
    st.divider()
    st.caption("Поддержка: удалённая диагностика для разработчика (Telegram).")
    if st.button("📤 Отправить диагностику разработчику"):
        log_path = default_log_path()
        info = collect_system_info(catalog_size=catalog_size)
        caption = (
            f"📤 <b>Диагностика склада</b>\n"
            f"ОС: {info['os']}\n"
            f"Python: {info['python']}\n"
            f"Каталог: {info['catalog']}"
        )
        if log_path.is_file():
            send_telegram_document(log_path, caption)
            st.success("Файл лога отправлен разработчику в Telegram.")
        else:
            notify_diagnostics(
                f"ОС: {info['os']}\nPython: {info['python']}\n"
                f"Каталог: {info['catalog']}\nЛог-файл отсутствует."
            )
            st.warning("Лог-файл не найден, отправлено текстовое уведомление.")


_DISPLAY_COLUMNS = [
    "№",
    "Статус",
    "Клиентское наименование (1С 7.7)",
    "Фабричное наименование (1С v8)",
    "Штрихкод (EAN-13)",
    "Кол-во",
    "Заказчик",
]
_QUARANTINE_DISPLAY_COLUMNS = [
    *_DISPLAY_COLUMNS,
    "Причина",
]
_STATION_COLUMNS = ["№", "Статус", "Наименование", "Кол-во", "Штрихкод (EAN-13)"]


def _configured_llm_model(provider: str) -> str:
    if provider == "ollama":
        return os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


def _llm_available(provider: str) -> bool:
    if provider == "ollama":
        return LLMResolver(provider="ollama").is_available()
    return KeyPool.from_env().is_available


def _llm_status_info(provider: str) -> tuple[bool, str, str]:
    """Return sidebar LLM badge text: (available, status_line, caption_line)."""
    model = _configured_llm_model(provider)
    if provider == "ollama":
        available = _llm_available(provider)
        status = (
            "🟢 LLM: доступен — фолбэк активен"
            if available
            else "🔴 LLM: недоступен — авто-сопоставление без LLM-фолбэка"
        )
        return available, status, f"Модель: Ollama: {model}"

    pool = KeyPool.from_env()
    if pool.is_available:
        status = f"🟢 LLM: доступен (Пул: {pool.key_count} шт.)"
        caption = f"Пул ключей: {pool.key_count} шт. | Модель: {model}"
        return True, status, caption

    return (
        False,
        "🔴 LLM: недоступен — авто-сопоставление без LLM-фолбэка",
        f"Модель: Gemini: {model}",
    )


@st.cache_resource(show_spinner="Загрузка каталога 1С v8 и построение FAISS-индекса…")
def load_pipeline(provider: str) -> tuple[HybridMatcher, int]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Каталог не найден: {CATALOG_PATH}")

    catalog = load_catalog_v8(CATALOG_PATH)
    vocabulary = DynamicVocabulary(catalog)
    feature_extractor = FeatureExtractor(vocabulary)
    vector_store = CatalogVectorStore(cache_dir=str(CACHE_DIR))
    vector_store.build_or_load_index(catalog)

    llm_resolver = LLMResolver(provider=provider)
    matcher = HybridMatcher(vector_store, feature_extractor, llm_resolver=llm_resolver)
    return matcher, len(catalog)


def _decisions_to_dataframe(
    decisions: list[MatchDecision],
    customer_name: str,
    overrides: dict[int, str] | None = None,
) -> pd.DataFrame:
    resolved = WMSExcelAdapter.normalize_overrides(overrides)
    rows: list[dict[str, object]] = []
    ordered = WMSExcelAdapter.sort_decisions(decisions)
    for decision in ordered:
        block = decision.raw_block
        line = int(decision.order_line_number)
        operator_barcode = resolved.get(line, "")
        if decision.matched_entity is not None:
            factory_name = decision.matched_entity.nomenclature
            barcode = operator_barcode or (decision.matched_entity.barcode or "")
        elif decision.status == "QUARANTINE":
            factory_name = "— (Отсутствует в 1С 8)"
            barcode = operator_barcode
        else:
            factory_name = block.client_description
            barcode = operator_barcode

        if operator_barcode:
            status_label = "🔵 Введен оператором"
        else:
            status_label = get_status_badge(decision)

        rows.append(
            {
                "№": block.order_line_number,
                "Статус": status_label,
                "Клиентское наименование (1С 7.7)": block.client_description,
                "Фабричное наименование (1С v8)": factory_name,
                "Штрихкод (EAN-13)": barcode or "",
                "Кол-во": block.quantity,
                "Заказчик": block.customer_override or customer_name,
                "Причина": decision.status_detail or "",
                "_status_key": decision.status,
                "_operator": bool(operator_barcode),
            }
        )
    return pd.DataFrame(rows)


def _results_column_config() -> dict[str, st.column_config.Column]:
    return {
        "№": st.column_config.NumberColumn("№", width="small", format="%d"),
        "Статус": st.column_config.TextColumn("Статус", width="small"),
        "Клиентское наименование (1С 7.7)": st.column_config.TextColumn(
            "Клиентское наименование (1С 7.7)",
            width="large",
        ),
        "Фабричное наименование (1С v8)": st.column_config.TextColumn(
            "Фабричное наименование (1С v8)",
            width="large",
        ),
        "Штрихкод (EAN-13)": st.column_config.TextColumn(
            "Штрихкод (EAN-13)",
            width="medium",
        ),
        "Кол-во": st.column_config.NumberColumn("Кол-во", width="small", format="%d"),
        "Заказчик": st.column_config.TextColumn("Заказчик", width="medium"),
        "Причина": st.column_config.TextColumn("Причина", width="large"),
        "Наименование": st.column_config.TextColumn("Наименование", width="large"),
        "Количество": st.column_config.NumberColumn("Количество", width="small", format="%d"),
        "Текущий ШК": st.column_config.TextColumn("Текущий ШК", width="medium"),
        "Ввести/Отсканировать ШК": st.column_config.TextColumn(
            "Ввести/Отсканировать ШК",
            width="medium",
        ),
    }


def _render_results_table(df: pd.DataFrame, columns: list[str] | None = None) -> None:
    display_columns = columns or _DISPLAY_COLUMNS
    present = [column for column in display_columns if column in df.columns]
    display_df = df[present]
    st.dataframe(
        display_df,
        column_config=_results_column_config(),
        width="stretch",
        hide_index=True,
    )


def _operator_overrides() -> dict[int, str]:
    return WMSExcelAdapter.normalize_overrides(st.session_state.get("operator_overrides"))


def _order_id_of(result: dict[str, Any]) -> str:
    return _meta_from_result(result).order_id


def _overrides_by_order() -> dict[str, dict[int, str]]:
    store = st.session_state.setdefault("operator_overrides_by_order", {})
    return store


def _overrides_for_result(result: dict[str, Any]) -> dict[int, str]:
    oid = _order_id_of(result)
    stored = _overrides_by_order().get(oid) or {}
    return WMSExcelAdapter.normalize_overrides(stored)


def _activate_result_overrides(result: dict[str, Any]) -> dict[int, str]:
    overrides = _overrides_for_result(result)
    st.session_state["operator_overrides"] = overrides
    return overrides


def _persist_overrides_for_result(result: dict[str, Any], overrides: dict[int, str]) -> None:
    oid = _order_id_of(result)
    store = dict(_overrides_by_order())
    store[oid] = dict(overrides)
    st.session_state["operator_overrides_by_order"] = store
    st.session_state["operator_overrides"] = dict(overrides)


def _apply_scanned_barcode(line_number: int, raw: str, result: dict[str, Any]) -> str | None:
    text = (raw or "").strip()
    if not WMSExcelAdapter.is_valid_ean13(text):
        return "Нужен штрихкод EAN-13 из ровно 13 цифр."
    overrides = dict(_overrides_for_result(result))
    overrides[int(line_number)] = text
    _persist_overrides_for_result(result, overrides)
    return None


def _render_scan_station(
    eligible: list[MatchDecision],
    form_key: str,
    result: dict[str, Any],
) -> None:
    if not eligible:
        return
    labels: dict[int, str] = {}
    for decision in eligible:
        name = decision.raw_block.client_description
        if decision.matched_entity is not None:
            name = decision.matched_entity.nomenclature
        labels[int(decision.order_line_number)] = f"№{decision.order_line_number} — {name}"
    options = [int(decision.order_line_number) for decision in eligible]
    with st.form(form_key, clear_on_submit=True):
        st.markdown("**Сканер ТСД / быстрый ввод EAN-13**")
        line = st.selectbox(
            "Позиция",
            options=options,
            format_func=lambda number: labels[int(number)],
        )
        scanned = st.text_input(
            "Ввести/Отсканировать ШК",
            placeholder="13 цифр, затем Enter",
        )
        submitted = st.form_submit_button("Записать штрихкод", type="primary")
    if submitted:
        error = _apply_scanned_barcode(int(line), scanned, result)
        if error:
            st.error(error)
        else:
            st.rerun()


def _sync_editor_barcodes(
    edited: pd.DataFrame,
    barcode_column: str,
    result: dict[str, Any],
) -> None:
    overrides = dict(_overrides_for_result(result))
    changed = False
    invalid: list[int] = []
    for _, row in edited.iterrows():
        line = int(row["№"])
        raw = row[barcode_column]
        text = "" if raw is None else str(raw).strip()
        if text in {"—", "-", "nan", "None"}:
            text = ""
        current = overrides.get(line, "")
        if text == current:
            continue
        if not text:
            if line in overrides:
                del overrides[line]
                changed = True
            continue
        if not WMSExcelAdapter.is_valid_ean13(text):
            invalid.append(line)
            continue
        overrides[line] = text
        changed = True
    if invalid:
        st.warning(
            "EAN-13 должен содержать ровно 13 цифр. Проверьте позиции: "
            + ", ".join(f"№{line}" for line in invalid)
        )
    if changed:
        _persist_overrides_for_result(result, overrides)
        st.rerun()


def _history() -> HistoryManager:
    return HistoryManager()


def _clear_active_view() -> None:
    for key in _RESULT_KEYS:
        st.session_state.pop(key, None)
    st.session_state["_skip_restore"] = True
    st.session_state["current_result"] = None
    st.session_state["batch_results"] = []
    st.session_state["operator_overrides"] = {}
    st.session_state["operator_overrides_by_order"] = {}
    st.session_state.pop("station_order_id", None)


def _meta_from_result(result: dict[str, Any]) -> OrderRunMeta:
    return OrderRunMeta.model_validate(result["meta"])


def _session_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "decisions": dump_decisions_for_session(result.get("decisions") or []),
        "customer_name": result.get("customer_name") or "",
        "upload_name": result.get("upload_name") or "",
        "doc_type_label": result.get("doc_type_label") or "",
        "operator_overrides": {
            str(key): value for key, value in _overrides_for_result(result).items()
        },
    }


def _refresh_wms_bytes(result: dict[str, Any]) -> bytes:
    decisions: list[MatchDecision] = result.get("decisions") or []
    if not decisions:
        return result.get("wms_bytes") or b""
    customer = result.get("customer_name") or ""
    wms_bytes = WMSExcelAdapter().export_to_bytes(
        decisions,
        customer,
        source_name=result.get("upload_name"),
        overrides=_overrides_for_result(result),
    ).getvalue()
    result["wms_bytes"] = wms_bytes
    meta = _meta_from_result(result)
    manager = _history()
    try:
        if meta.wms_excel_path:
            manager.update_excel(meta, wms_bytes)
            manager.update_session(meta, _session_payload(result))
            result["meta"] = meta.model_dump(mode="json")
    except OSError:
        pass
    return wms_bytes


def _restore_from_meta(meta: OrderRunMeta) -> dict[str, Any] | None:
    manager = _history()
    try:
        excel = manager.read_excel_bytes(meta)
    except FileNotFoundError:
        return None
    session = manager.load_session(meta) or {}
    decisions = load_decisions_from_session(session.get("decisions"))
    overrides = session.get("operator_overrides") or {}
    resolved = WMSExcelAdapter.normalize_overrides(overrides)
    st.session_state["operator_overrides"] = resolved
    st.session_state["operator_overrides_by_order"] = {meta.order_id: resolved}
    return {
        "meta": meta.model_dump(mode="json"),
        "decisions": decisions,
        "customer_name": session.get("customer_name") or meta.customer_name,
        "wms_bytes": excel,
        "upload_name": session.get("upload_name") or meta.original_filename,
        "doc_type_label": session.get("doc_type_label") or meta.doc_type,
        "restored": True,
    }


def _ensure_restored_session() -> None:
    """Restore last-run WMS data — only for sessions that have been active before.

    Fresh sessions (new browser tabs, incognito windows, different users) are
    detected by the absence of ``_session_initialized`` and are never
    contaminated with another user's data from the shared disk history.
    """
    if st.session_state.get("_skip_restore"):
        return
    # Do NOT auto-restore for brand-new sessions — prevents cross-user state leak.
    if not st.session_state.get("_session_initialized"):
        return
    if st.session_state.get("current_result"):
        return
    last = _history().get_last_run()
    if last is None:
        st.session_state["current_result"] = None
        return
    restored = _restore_from_meta(last)
    if restored is None:
        st.session_state["current_result"] = None
        return
    st.session_state["current_result"] = restored
    st.session_state["batch_results"] = [restored]
    st.session_state["_restore_banner"] = {
        "name": last.original_filename,
        "time": last.timestamp,
    }


def _process_uploaded_file(
    file_bytes: bytes,
    filename: str,
    matcher: HybridMatcher,
    customer_override: str,
    progress_callback,
    *,
    notify: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    doc_type, parsed, decisions = process_order(
        file_bytes,
        matcher,
        filename=filename,
        progress_callback=progress_callback,
    )
    decisions = WMSExcelAdapter.sort_decisions(decisions)
    if len(decisions) != len(parsed.blocks):
        raise RuntimeError(
            f"Zero-Loss нарушен: {len(parsed.blocks)} входных блоков → {len(decisions)} решений"
        )
    customer_name = customer_override.strip() or parsed.customer_name
    adapter = WMSExcelAdapter()
    t_excel = time.perf_counter()
    wms_bytes = adapter.export_to_bytes(
        decisions,
        customer_name,
        source_name=filename,
        overrides={},
    ).getvalue()
    excel_sec = time.perf_counter() - t_excel
    timings = getattr(matcher, "stage_timings", None)
    if timings is not None:
        timings.excel = excel_sec
        log_order_profiler(filename, timings, time.perf_counter() - started)
    meta = build_order_run_meta(
        original_filename=filename,
        doc_type=doc_type,
        decisions=decisions,
        excel_bytes=wms_bytes,
        customer_name=customer_name,
    )
    result = {
        "meta": meta.model_dump(mode="json"),
        "decisions": decisions,
        "customer_name": customer_name,
        "wms_bytes": wms_bytes,
        "upload_name": filename,
        "doc_type_label": meta.doc_type,
        "restored": False,
    }
    _history().save_run(meta, wms_bytes, session=_session_payload(result))
    result["meta"] = meta.model_dump(mode="json")
    elapsed_sec = time.perf_counter() - started
    get_logger().info("UI processed %s in %.1fs", filename, elapsed_sec)
    stats = order_stats(
        decisions,
        customer_name=customer_name,
        elapsed_sec=elapsed_sec,
        filename=filename,
        checksum_mismatch=parsed.checksum_mismatch,
        declared_places=parsed.declared_places,
    )
    result["telemetry_stats"] = stats
    if notify:
        notify_order_processed(filename, stats)
    return result


def _download_name(result: dict[str, Any]) -> str:
    meta = result.get("meta") or {}
    stored = str(meta.get("wms_excel_path") or "")
    if stored:
        return Path(stored).name
    return WMSExcelAdapter.build_download_filename(
        result.get("customer_name") or "Заказ",
        date.today().isoformat(),
    )


def _render_order_card(result: dict[str, Any], *, key_prefix: str) -> None:
    meta = _meta_from_result(result)
    decisions: list[MatchDecision] = result.get("decisions") or []
    with st.container(border=True):
        st.markdown(f"**{result.get('upload_name') or meta.original_filename}**")
        st.caption(meta.doc_type)
        cols = st.columns(4)
        cols[0].metric("Строк WMS", meta.total_rows)
        cols[1].metric("Мест", meta.total_places)
        cols[2].metric("Со ШК", meta.matched_auto_count)
        cols[3].metric("Карантин", meta.quarantine_count)
        if meta.quarantine_count:
            st.warning(f"Карантин: {meta.quarantine_count} поз. — требуется ручной отбор")
        else:
            st.success("Карантин пуст")
        st.download_button(
            label="📥 Скачать WMS Excel",
            data=result.get("wms_bytes") or b"",
            file_name=_download_name(result),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            width="stretch",
            key=f"{key_prefix}_dl_{meta.order_id}",
        )
        if decisions:
            preview = _decisions_to_dataframe(
                decisions,
                result.get("customer_name") or "",
                _overrides_for_result(result),
            )
            _render_results_table(preview.head(20))
        else:
            st.caption("Превью таблицы недоступно (восстановлена только выгрузка Excel).")


def _render_header(catalog_size: int) -> None:
    st.title("WMS Ассистент: Мебельный Склад (г. Челябинск)")
    if catalog_size:
        st.caption("🟢 Локальная система активна (FAISS + E5)")
    else:
        st.caption("🔴 Каталог 1С v8 не загружен")


def _format_catalog_size(n: int) -> str:
    """Format a catalog size with a space as thousands separator (e.g. 12 880)."""
    return f"{n:,}".replace(",", " ")


def _render_sidebar() -> tuple[str, int]:
    """Operator-first sidebar: warehouse status, actions, collapsed diagnostics."""
    provider = str(st.session_state.get("sidebar_llm_provider", "gemini"))
    if provider not in ("gemini", "ollama"):
        provider = "gemini"
    os.environ["LLM_PROVIDER"] = provider

    catalog_size = 0
    catalog_error: str | None = None
    try:
        _, catalog_size = load_pipeline(provider)
    except FileNotFoundError as exc:
        catalog_error = str(exc)
        notify_error(
            "Каталог 1С v8 не найден",
            traceback.format_exc(),
            filename="catalog_v8.xlsx",
        )

    if not st.session_state.get("_startup_notified"):
        notify_startup(collect_system_info(catalog_size=catalog_size))
        st.session_state["_startup_notified"] = True

    # --- Warehouse Status ---
    st.markdown("### 📦 Мебельный Склад")
    if catalog_size:
        st.caption(f"🟢 **Каталог 1С:** {_format_catalog_size(catalog_size)} позиций")
        st.caption("🟢 **ИИ-Ассистент:** Активен (FAISS + E5)")
    else:
        st.caption("🔴 **Каталог 1С:** не загружен")
        st.caption("🔴 **ИИ-Ассистент:** неактивен")
        if catalog_error:
            st.error(catalog_error)

    st.divider()

    # --- Operator Actions ---
    if st.button(
        "➕ Начать новый заказ",
        key="sidebar_clear",
        use_container_width=True,
        type="primary",
    ):
        _clear_active_view()
        st.rerun()

    if is_auth_required():
        if st.button(
            "🔒 Выйти / Сменить смену",
            key="sidebar_logout",
            use_container_width=True,
        ):
            st.session_state["authenticated"] = False
            st.rerun()

    # --- Engineering diagnostics (collapsed by default) ---
    with st.expander("🛠️ Инженерная диагностика", expanded=False):
        provider = st.radio(
            "LLM провайдер",
            options=["gemini", "ollama"],
            format_func=lambda value: (
                "Gemini (облако)" if value == "gemini" else "Ollama (локально)"
            ),
            horizontal=True,
            key="sidebar_llm_provider",
        )
        os.environ["LLM_PROVIDER"] = provider

        _, llm_status_line, llm_status_caption = _llm_status_info(provider)
        st.markdown(llm_status_line)
        st.caption(llm_status_caption)

        if provider == "gemini":
            raw_key = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get(
                "GEMINI_API_KEY", ""
            )
            first_key = (raw_key.split(",")[0]).strip() if raw_key else ""
            st.caption(f"Ключ (preview): `{mask_secret(first_key)}`")
            if st.button("🔍 Проверить ключи", key="sidebar_ping_llm"):
                ping = KeyPool.from_env().test_connection(
                    base_url=resolve_gemini_base_url()
                )
                st.toast(ping.message, icon="✅" if ping.ok else "⚠️")
        elif provider == "ollama":
            raw_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            if raw_token:
                st.caption(f"Telegram token: `{mask_secret(raw_token)}`")

        st.markdown(
            "**Контракт WMS:** `[№, Наименование, Штрихкод, Количество, Заказчик]`"
        )
        session_id = str(st.session_state.get("session_uuid", "—"))
        preview = f"{session_id[:8]}…" if len(session_id) > 8 else session_id
        st.caption(f"Сессия: `{preview}`")

    return provider, catalog_size


def _render_process_tab(provider: str, catalog_size: int) -> None:
    uploaded_files = st.file_uploader(
        "Отборочные листы 1С 7.7 (можно несколько файлов сразу)",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        help="Корпусная мебель и мягкая мебель обрабатываются независимо.",
    )
    customer_override = st.text_input(
        "Заказчик / Получатель для WMS (необязательно)",
        help="Если пусто — берётся из шапки каждого файла.",
    )

    if uploaded_files and catalog_size:
        if st.button("Обработать заказ(ы)", type="primary", width="stretch"):
            matcher, _ = load_pipeline(provider)
            results: list[dict[str, Any]] = []
            total = len(uploaded_files)
            bar = st.progress(0, text="Инициализация…")
            speed = st.empty()
            try:
                notify_each = total == 1
                for index, uploaded in enumerate(uploaded_files):
                    file_started = time.perf_counter()

                    def _on_progress(
                        done: int,
                        total_units: int,
                        counts: dict[str, int],
                        *,
                        _index: int = index,
                        _name: str = uploaded.name,
                    ) -> None:
                        fraction = (_index + done / max(total_units, 1)) / total
                        bar.progress(min(max(fraction, 0.0), 0.99), text=f"Сопоставление: {_name}")
                        elapsed = max(time.perf_counter() - file_started, 1e-6)
                        processed = (
                            counts.get("MATCHED_AUTO", 0)
                            + counts.get("MATCHED_LLM", 0)
                            + counts.get("QUARANTINE", 0)
                        )
                        speed.caption(
                            f"{_name}: {processed} поз. — {processed / elapsed:.1f} поз/сек "
                            f"(файл {_index + 1} из {total})"
                        )

                    result = _process_uploaded_file(
                        uploaded.getvalue(),
                        uploaded.name,
                        matcher,
                        customer_override,
                        _on_progress,
                        notify=notify_each,
                    )
                    results.append(result)
                    bar.progress((index + 1) / total, text=f"Готово: {uploaded.name}")
                bar.progress(1.0, text="Пакет обработан")
                if total > 1:
                    notify_batch_completion(
                        [item.get("telemetry_stats") or {} for item in results]
                    )
                st.session_state["batch_results"] = results
                st.session_state["current_result"] = results[-1]
                st.session_state["operator_overrides"] = {}
                st.session_state["operator_overrides_by_order"] = {
                    _order_id_of(item): {} for item in results
                }
                st.session_state.pop("station_order_id", None)
                st.session_state["_restore_banner"] = None
                st.session_state["_skip_restore"] = True
            except Exception as exc:
                failed_index = min(len(results), max(len(uploaded_files) - 1, 0))
                failed_name = uploaded_files[failed_index].name if uploaded_files else "—"
                notify_error(
                    f"Обработка заказа: {exc}",
                    traceback.format_exc(),
                    filename=failed_name,
                )
                st.error("Ошибка обработки заказа. Диагностика отправлена разработчику.")
                with st.expander("Показать детали ошибки"):
                    st.exception(exc)
    elif uploaded_files and catalog_size == 0:
        st.error("Каталог не загружен — обработка недоступна.")
    elif not uploaded_files:
        st.caption("Загрузите один или несколько файлов .xls / .xlsx.")

    batch = _active_batch()
    if not batch:
        return
    for result in batch:
        _render_order_card(result, key_prefix="process")


def _active_batch() -> list[dict[str, Any]]:
    batch: list[dict[str, Any]] = list(st.session_state.get("batch_results") or [])
    current = st.session_state.get("current_result")
    if current and not batch:
        return [current]
    return batch


def _render_station_tab() -> None:
    batch = _active_batch()
    if not batch:
        st.info("Нет активного заказа. Обработайте файл на вкладке «Обработка заказа».")
        return

    by_id = {_order_id_of(item): item for item in batch}
    order_ids = list(by_id.keys())
    selected_id = st.selectbox(
        "📦 Выберите заказ для станции сканирования:",
        options=order_ids,
        format_func=lambda oid: format_station_order_label(
            by_id[oid].get("upload_name") or _meta_from_result(by_id[oid]).original_filename,
            total_rows=_meta_from_result(by_id[oid]).total_rows,
            total_places=_meta_from_result(by_id[oid]).total_places,
            quarantine_count=_meta_from_result(by_id[oid]).quarantine_count,
        ),
        key="station_order_id",
    )
    selected = by_id[str(selected_id)]
    st.session_state["current_result"] = selected
    overrides = _activate_result_overrides(selected)

    decisions: list[MatchDecision] = selected.get("decisions") or []
    if not decisions:
        st.info("Для станции ШК нужна полная сессия. Скачивание Excel доступно на вкладке обработки.")
        return
    _refresh_wms_bytes(selected)
    filename = selected.get("upload_name") or _meta_from_result(selected).original_filename
    quarantine_open, no_barcode_open = partition_scan_attention(decisions, overrides)
    eligible = [*quarantine_open, *no_barcode_open]
    if not eligible:
        st.success(
            f"✅ В заказе «{filename}» все позиции имеют заводские ШК, ручной ввод не требуется"
        )
        return

    st.info(
        f"🔍 Позиций для ввода/сканирования ШК: {len(eligible)} "
        f"(Карантин: {len(quarantine_open)}, Без ШК: {len(no_barcode_open)})"
    )

    result_df = _decisions_to_dataframe(
        decisions,
        selected.get("customer_name") or "",
        overrides,
    )
    station_rows: list[dict[str, object]] = []
    eligible_lines = {int(item.order_line_number) for item in eligible}
    badge_by_line = {int(item.order_line_number): station_status_badge(item) for item in eligible}
    for _, row in result_df.iterrows():
        if int(row["№"]) not in eligible_lines:
            continue
        name = row["Фабричное наименование (1С v8)"] or row["Клиентское наименование (1С 7.7)"]
        station_rows.append(
            {
                "№": int(row["№"]),
                "Статус": badge_by_line[int(row["№"])],
                "Наименование": name,
                "Кол-во": int(row["Кол-во"]),
                "Штрихкод (EAN-13)": str(row["Штрихкод (EAN-13)"] or ""),
            }
        )
    station_df = pd.DataFrame(station_rows)
    oid = _order_id_of(selected)
    _render_scan_station(eligible, f"scan_station_{oid}", selected)
    edited = st.data_editor(
        station_df,
        column_config=_results_column_config(),
        disabled=["№", "Статус", "Наименование", "Кол-во"],
        width="stretch",
        hide_index=True,
        key=f"editor_station_{oid}",
    )
    if "Штрихкод (EAN-13)" in edited.columns:
        _sync_editor_barcodes(edited, "Штрихкод (EAN-13)", selected)

    if quarantine_open:
        grouped: dict[str, list[str]] = defaultdict(list)
        for decision in quarantine_open:
            reason = decision.status_detail or "Отсутствует в каталоге фабрики"
            block = decision.raw_block
            grouped[reason].append(f"**№{block.line_number}.** {block.client_description}")
        reason_blocks = [
            f"**{reason}**\n" + "\n".join(lines) for reason, lines in grouped.items()
        ]
        st.warning(
            "**⚠️ Карантин — требуется ручной отбор на складе**\n\n"
            + "\n\n".join(reason_blocks)
        )


def _render_history_tab() -> None:
    manager = _history()
    history = manager.get_shift_history()
    if not history:
        st.info("За сегодня обработанных заказов пока нет.")
        return
    rows = [
        {
            "Время": item.timestamp,
            "Имя файла": item.original_filename,
            "Тип": item.doc_type,
            "Строк": item.total_rows,
            "Мест": item.total_places,
            "Карантин": item.quarantine_count,
        }
        for item in history
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.markdown("**Повторная выгрузка**")
    for item in history:
        cols = st.columns((4, 1))
        cols[0].caption(f"{item.timestamp} — {item.original_filename}")
        try:
            data = manager.read_excel_bytes(item)
        except FileNotFoundError:
            cols[1].warning("файл не найден")
            continue
        cols[1].download_button(
            "Excel",
            data=data,
            file_name=Path(item.wms_excel_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"hist_dl_{item.order_id}",
        )
    zip_bytes = manager.create_shift_zip()
    st.download_button(
        label="📦 Скачать архив всей смены (ZIP)",
        data=zip_bytes,
        file_name=f"Смена_{manager.today_str()}.zip",
        mime="application/zip",
        type="primary",
        width="stretch",
        key="shift_zip",
    )


def _render_summary_tab() -> None:
    summary = shift_summary(_history().get_shift_history())
    cols = st.columns(4)
    cols[0].metric("Всего заказов за смену", summary["orders"])
    cols[1].metric("Всего отгружено мест", summary["places"])
    cols[2].metric("% Авто-сопоставления", f"{summary['auto_pct']}%")
    cols[3].metric("Позиций в Карантине", summary["quarantine"])


def main() -> None:
    st.set_page_config(
        page_title="WMS Ассистент: Мебельный Склад",
        page_icon="📦",
        layout="wide",
    )

    # ------------------------------------------------------------------
    # Auth gate — must run before any sidebar or content rendering.
    # ------------------------------------------------------------------
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if "_auth_protector" not in st.session_state:
        st.session_state["_auth_protector"] = BruteForceProtector()

    if is_auth_required() and not st.session_state.get("authenticated"):
        _render_pin_screen()
        st.stop()
        return

    # ------------------------------------------------------------------
    # Assign a unique UUID on first access — distinguishes each browser session.
    # Fresh sessions (new tab / incognito / different user) start with a clean
    # slate; _session_initialized is only set after the first rerun cycle so that
    # _ensure_restored_session() skips the shared disk history for brand-new sessions.
    # ------------------------------------------------------------------
    if "session_uuid" not in st.session_state:
        st.session_state["session_uuid"] = str(uuid.uuid4())
        st.session_state["_skip_restore"] = True  # no auto-restore for fresh sessions

    if "current_result" not in st.session_state:
        st.session_state["current_result"] = None
    if "batch_results" not in st.session_state:
        st.session_state["batch_results"] = []
    if "operator_overrides" not in st.session_state:
        st.session_state["operator_overrides"] = {}
    if "operator_overrides_by_order" not in st.session_state:
        st.session_state["operator_overrides_by_order"] = {}
    # Mark session as initialized so _ensure_restored_session works on reruns.
    st.session_state["_session_initialized"] = True

    with st.sidebar:
        provider, catalog_size = _render_sidebar()

    _ensure_restored_session()
    _render_header(catalog_size)

    banner = st.session_state.get("_restore_banner")
    if banner:
        st.info(
            "ℹ️ Восстановлен последний обработанный заказ: "
            f"{banner['name']} (от {banner['time']})"
        )

    tab_process, tab_station, tab_history, tab_summary = st.tabs(
        [
            "📥 Обработка заказа",
            "🔍 Станция ШК / Карантин",
            "📁 История смены",
            "📊 Сводка смены",
        ]
    )
    with tab_process:
        _render_process_tab(provider, catalog_size)
    with tab_station:
        _render_station_tab()
    with tab_history:
        _render_history_tab()
    with tab_summary:
        _render_summary_tab()

    _render_support_footer(catalog_size)


if __name__ == "__main__":
    main()
