"""Local on-disk shift archive: WMS Excel files, metadata, session restore."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models import MatchDecision
from src.parsers.document_detector import DocumentType
from src.utils.logger import PROJECT_ROOT
from src.utils.reporter import count_without_barcode

DOC_TYPE_CABINET = "Корпусная мебель (1С 7.7)"
DOC_TYPE_SOFT = "Мягкая мебель (Перемещение)"
MANIFEST_NAME = "shift_manifest.json"
SESSION_SUFFIX = ".session.json"
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]+')
_WS = re.compile(r"\s+")


class OrderRunMeta(BaseModel):
    """Metadata for one processed warehouse order (one WMS Excel file)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    order_id: str
    timestamp: str
    original_filename: str
    doc_type: str
    total_rows: int = Field(..., ge=0)
    total_places: int = Field(..., ge=0)
    matched_auto_count: int = Field(..., ge=0)
    auto_no_barcode_count: int = Field(..., ge=0)
    quarantine_count: int = Field(..., ge=0)
    wms_excel_path: str = ""
    customer_name: str = ""
    session_path: str = ""

    @field_validator("order_id", "timestamp", "original_filename", "doc_type", mode="before")
    @classmethod
    def _stringify(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


def doc_type_label(doc_type: DocumentType | str | None) -> str:
    if doc_type == DocumentType.SOFT_FURNITURE_TRANSFER or doc_type == DOC_TYPE_SOFT:
        return DOC_TYPE_SOFT
    if isinstance(doc_type, str) and "мягк" in doc_type.lower():
        return DOC_TYPE_SOFT
    return DOC_TYPE_CABINET


def safe_stem(filename: str, *, max_len: int = 80) -> str:
    stem = Path(filename or "order").stem
    cleaned = _UNSAFE_CHARS.sub("_", stem)
    cleaned = _WS.sub("_", cleaned).strip("._")
    return (cleaned[:max_len] or "order")


def make_order_id(filename: str, timestamp: str, payload_len: int) -> str:
    raw = f"{filename}|{timestamp}|{payload_len}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def dump_decisions_for_session(decisions: list[MatchDecision]) -> list[dict[str, Any]]:
    """Serialize MatchDecision without FAISS candidate lists (keeps session JSON small)."""
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        payload = decision.model_dump(mode="json", by_alias=True)
        payload["candidates"] = []
        rows.append(payload)
    return rows


def load_decisions_from_session(rows: list[dict[str, Any]] | None) -> list[MatchDecision]:
    if not rows:
        return []
    return [MatchDecision.model_validate(row) for row in rows]


def build_order_run_meta(
    *,
    original_filename: str,
    doc_type: DocumentType | str,
    decisions: list[MatchDecision],
    excel_bytes: bytes,
    customer_name: str = "",
    timestamp: str | None = None,
    order_id: str | None = None,
    overrides: dict[int, str] | None = None,
) -> OrderRunMeta:
    stamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    oid = order_id or make_order_id(original_filename, stamp, len(excel_bytes))
    total_rows = len(decisions)
    total_places = sum(int(item.raw_block.quantity) for item in decisions)
    from src.adapters.wms_excel_adapter import WMSExcelAdapter

    matched = WMSExcelAdapter.count_matched_with_barcode(decisions, overrides)
    no_barcode = count_without_barcode(decisions, overrides)
    quarantine = sum(1 for item in decisions if item.status == "QUARANTINE")
    return OrderRunMeta(
        order_id=oid,
        timestamp=stamp,
        original_filename=original_filename,
        doc_type=doc_type_label(doc_type),
        total_rows=total_rows,
        total_places=total_places,
        matched_auto_count=matched,
        auto_no_barcode_count=no_barcode,
        quarantine_count=quarantine,
        customer_name=customer_name,
    )


class HistoryManager:
    """Stores shift Excel files under ``output/history/{YYYY-MM-DD}/``."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else PROJECT_ROOT / "output" / "history"
        self._clock = clock or datetime.now

    def today_str(self) -> str:
        return self._clock().strftime("%Y-%m-%d")

    def shift_dir(self, date_str: Optional[str] = None) -> Path:
        stamp = date_str or self.today_str()
        return self.root / stamp

    def save_run(
        self,
        meta: OrderRunMeta,
        excel_bytes: bytes,
        *,
        session: dict[str, Any] | None = None,
    ) -> Path:
        """Write WMS Excel, optional session JSON, and append ``shift_manifest.json``."""
        folder = self.shift_dir(self._date_from_timestamp(meta.timestamp))
        folder.mkdir(parents=True, exist_ok=True)

        ts_file = self._file_stamp(meta.timestamp)
        stem = safe_stem(meta.original_filename)
        excel_name = f"WMS_{ts_file}_{stem}.xlsx"
        excel_path = folder / excel_name
        if excel_path.exists():
            excel_name = f"WMS_{ts_file}_{stem}_{meta.order_id[:8]}.xlsx"
            excel_path = folder / excel_name
        excel_path.write_bytes(excel_bytes)

        stored = meta.model_copy(deep=True)
        stored.wms_excel_path = self._store_path(excel_path)
        if session is not None:
            session_path = folder / f"{excel_path.stem}{SESSION_SUFFIX}"
            session_path.write_text(
                json.dumps(session, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            stored.session_path = self._store_path(session_path)

        runs = self._read_manifest(folder)
        runs = [item for item in runs if item.order_id != stored.order_id]
        runs.append(stored)
        self._write_manifest(folder, runs)
        meta.wms_excel_path = stored.wms_excel_path
        meta.session_path = stored.session_path
        meta.customer_name = stored.customer_name or meta.customer_name
        return excel_path

    def get_shift_history(self, date_str: Optional[str] = None) -> list[OrderRunMeta]:
        folder = self.shift_dir(date_str)
        runs = self._read_manifest(folder)
        return sorted(runs, key=lambda item: item.timestamp, reverse=True)

    def get_last_run(self, date_str: Optional[str] = None) -> Optional[OrderRunMeta]:
        history = self.get_shift_history(date_str)
        return history[0] if history else None

    def create_shift_zip(self, date_str: Optional[str] = None) -> bytes:
        folder = self.shift_dir(date_str)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for meta in self.get_shift_history(date_str):
                path = self.resolve_path(meta.wms_excel_path)
                if path.is_file() and path.suffix.lower() == ".xlsx":
                    archive.write(path, path.name)
            if folder.is_dir():
                for path in sorted(folder.glob("WMS_*.xlsx")):
                    if path.name not in archive.namelist():
                        archive.write(path, path.name)
        buffer.seek(0)
        return buffer.getvalue()

    def read_excel_bytes(self, meta: OrderRunMeta) -> bytes:
        path = self.resolve_path(meta.wms_excel_path)
        if not path.is_file():
            raise FileNotFoundError(f"WMS Excel not found: {path}")
        return path.read_bytes()

    def load_session(self, meta: OrderRunMeta) -> dict[str, Any] | None:
        if not meta.session_path:
            folder = self.shift_dir(self._date_from_timestamp(meta.timestamp))
            excel = self.resolve_path(meta.wms_excel_path)
            fallback = folder / f"{excel.stem}{SESSION_SUFFIX}"
            path = fallback if fallback.is_file() else None
        else:
            path = self.resolve_path(meta.session_path)
        if path is None or not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        overrides = payload.get("operator_overrides") or {}
        payload["operator_overrides"] = {int(key): str(value) for key, value in overrides.items()}
        return payload

    def update_excel(self, meta: OrderRunMeta, excel_bytes: bytes) -> None:
        path = self.resolve_path(meta.wms_excel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(excel_bytes)

    def update_session(self, meta: OrderRunMeta, session: dict[str, Any]) -> None:
        if meta.session_path:
            path = self.resolve_path(meta.session_path)
        else:
            excel = self.resolve_path(meta.wms_excel_path)
            path = excel.parent / f"{excel.stem}{SESSION_SUFFIX}"
            meta.session_path = self._store_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    def resolve_path(self, stored: str) -> Path:
        path = Path(stored)
        if path.is_absolute():
            return path
        return (self.root / path).resolve()

    def _store_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    def _date_from_timestamp(self, timestamp: str) -> str:
        text = (timestamp or "").strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        return self.today_str()

    def _file_stamp(self, timestamp: str) -> str:
        parsed = self._parse_timestamp(timestamp)
        if parsed is not None:
            return parsed.strftime("%Y%m%d_%H%M%S")
        return self._clock().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _parse_timestamp(timestamp: str) -> datetime | None:
        text = (timestamp or "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _read_manifest(self, folder: Path) -> list[OrderRunMeta]:
        manifest = folder / MANIFEST_NAME
        if not manifest.is_file():
            return []
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        items = raw.get("runs", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [OrderRunMeta.model_validate(item) for item in items]

    def _write_manifest(self, folder: Path, runs: list[OrderRunMeta]) -> None:
        payload = {
            "date": folder.name,
            "runs": [item.model_dump(mode="json") for item in runs],
        }
        target = folder / MANIFEST_NAME
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)


def shift_summary(runs: list[OrderRunMeta]) -> dict[str, float | int]:
    orders = len(runs)
    places = sum(item.total_places for item in runs)
    rows = sum(item.total_rows for item in runs)
    matched = sum(item.matched_auto_count for item in runs)
    quarantine = sum(item.quarantine_count for item in runs)
    auto_pct = round(100.0 * matched / rows, 1) if rows else 0.0
    return {
        "orders": orders,
        "places": places,
        "rows": rows,
        "matched_auto": matched,
        "quarantine": quarantine,
        "auto_pct": auto_pct,
    }
