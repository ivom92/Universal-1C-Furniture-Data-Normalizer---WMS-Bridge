"""Pack an isolated warehouse zip with pre-warmed FAISS cache."""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.logger import console

DIST_DIR = PROJECT_ROOT / "dist"
ARCHIVE_NAME = "Warehouse_WMS_Pilot_v1.0.zip"
ROOT_IN_ZIP = "Warehouse_WMS_Pilot_v1.0"

INCLUDE_FILES = [
    "app_ui.py",
    "requirements.txt",
    ".env",
    "1_УСТАНОВКА.bat",
    "2_ЗАПУСК.bat",
    "3_ОСТАНОВИТЬ.bat",
    "1_setup.sh",
    "2_run.sh",
    "ИНСТРУКЦИЯ_ДЛЯ_ЕГОРА.txt",
    "README_СКЛАД.txt",
    "Запуск_WMS.vbs",
    "Остановить_WMS.vbs",
    ".streamlit/config.toml",
]
INCLUDE_TREES = [
    "src",
    "scripts",
    "data/orders",
    ".cache",
    ".streamlit",
]
INCLUDE_SINGLE = [
    Path("data") / "catalog_v8.xlsx",
]

SKIP_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    "tests",
    "__pycache__",
    "venv",
    ".venv",
    ".cache_pytest",
    ".cache_test_llm_fail",
}
SKIP_SUFFIXES = {".pyc", ".pyo"}
HARDCODED_ABS = re.compile(r"[A-Za-z]:\\(?:Users|Cursor|Windows)\\")
BAT_LAUNCHER_FILES = [
    "1_УСТАНОВКА.bat",
    "2_ЗАПУСК.bat",
    "3_ОСТАНОВИТЬ.bat",
]
OFFICE_COM_PATTERNS = (
    re.compile(r"^\s*(?:from\s+win32com|import\s+win32com)", re.MULTILINE),
    re.compile(r"win32com\.client"),
    re.compile(r"Excel\.Application"),
    re.compile(r"os\.startfile\s*\("),
    re.compile(r"^\s*(?:from\s+xlwings|import\s+xlwings)", re.MULTILINE),
    re.compile(r"^\s*(?:from\s+comtypes|import\s+comtypes)", re.MULTILINE),
)


def _is_skipped(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIR_NAMES:
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    if path.suffix.lower() in {".xlsx", ".xls"} and "output" in path.parts:
        return True
    return False


def _iter_tree(relative: str) -> list[Path]:
    root = PROJECT_ROOT / relative
    if not root.exists():
        raise FileNotFoundError(f"Не найден каталог для архива: {root}")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        if _is_skipped(rel):
            continue
        files.append(rel)
    return files


def assert_no_office_com() -> None:
    """Fail the build if project code references Microsoft Office COM automation."""
    scanned = list((PROJECT_ROOT / "src").rglob("*.py"))
    scanned.extend(
        path
        for path in (PROJECT_ROOT / "scripts").rglob("*.py")
        if path.name != "build_warehouse_dist.py"
    )
    scanned.append(PROJECT_ROOT / "app_ui.py")
    offenders: list[str] = []
    for path in scanned:
        if not path.exists() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in OFFICE_COM_PATTERNS:
            if pattern.search(text):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
                break
    if offenders:
        raise RuntimeError(
            "Найдены вызовы Office/COM (win32com, Excel.Application, os.startfile): "
            + ", ".join(sorted(set(offenders)))
        )


def assert_bat_launchers_windows8() -> None:
    """Ensure .bat launchers fix cwd and use legacy-safe console settings."""
    offenders: list[str] = []
    python_bats = {"1_УСТАНОВКА.bat", "2_ЗАПУСК.bat"}
    for name in BAT_LAUNCHER_FILES:
        path = PROJECT_ROOT / name
        if not path.exists():
            offenders.append(f"{name}: file missing")
            continue
        text = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0] != "cd /d \"%~dp0\"":
            offenders.append(f"{name}: first line must be cd /d \"%~dp0\"")
        if "chcp 65001 >nul 2>&1" not in text:
            offenders.append(f"{name}: chcp 65001 must redirect to >nul 2>&1")
        if name in python_bats:
            if "PYTHONIOENCODING=utf-8" not in text:
                offenders.append(f"{name}: missing PYTHONIOENCODING=utf-8")
            if "PYTHONLEGACYWINDOWSSTDIO=1" not in text:
                offenders.append(f"{name}: missing PYTHONLEGACYWINDOWSSTDIO=1")
            if "PYTHONUTF8=1" not in text:
                offenders.append(f"{name}: missing PYTHONUTF8=1")
    launch_bat = PROJECT_ROOT / "2_ЗАПУСК.bat"
    if launch_bat.exists():
        launch_text = launch_bat.read_text(encoding="utf-8")
        if "venv\\Scripts\\python.exe" not in launch_text:
            offenders.append("2_ЗАПУСК.bat: must call venv\\Scripts\\python.exe directly")
    vbs = PROJECT_ROOT / "Запуск_WMS.vbs"
    if vbs.exists():
        vbs_text = vbs.read_text(encoding="utf-8")
        if "venv\\Scripts\\python.exe" not in vbs_text:
            offenders.append("Запуск_WMS.vbs: must call venv\\Scripts\\python.exe directly")
    if offenders:
        raise RuntimeError("Проверка Windows 8 launchers: " + "; ".join(offenders))


def assert_streamlit_headless() -> None:
    cfg = PROJECT_ROOT / ".streamlit" / "config.toml"
    text = cfg.read_text(encoding="utf-8")
    if not re.search(r"^\s*headless\s*=\s*true\s*$", text, re.MULTILINE | re.IGNORECASE):
        raise RuntimeError(".streamlit/config.toml: требуется server.headless = true")


def assert_relative_paths() -> None:
    scanned = list((PROJECT_ROOT / "src").rglob("*.py"))
    scanned.extend((PROJECT_ROOT / "scripts").rglob("*.py"))
    scanned.append(PROJECT_ROOT / "app_ui.py")
    offenders: list[str] = []
    for path in scanned:
        if not path.exists() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if HARDCODED_ABS.search(text):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    if offenders:
        raise RuntimeError("Найдены абсолютные пути Windows в: " + ", ".join(offenders))


def collect_members() -> list[Path]:
    members: list[Path] = []
    for name in INCLUDE_FILES:
        path = Path(name)
        full = PROJECT_ROOT / path
        if not full.exists():
            raise FileNotFoundError(f"Нет файла: {full}")
        members.append(path)
    for rel in INCLUDE_SINGLE:
        full = PROJECT_ROOT / rel
        if not full.exists():
            raise FileNotFoundError(f"Нет файла: {full}")
        members.append(rel)
    for tree in INCLUDE_TREES:
        members.extend(_iter_tree(tree))
    unique: list[Path] = []
    seen: set[str] = set()
    for item in members:
        key = item.as_posix()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    cache_files = [item for item in unique if item.parts and item.parts[0] == ".cache"]
    if not cache_files:
        raise FileNotFoundError("Папка .cache пуста — сначала прогрейте FAISS (check_system_health.py --warm)")
    streamlit_cfg = [item for item in unique if item.as_posix() == ".streamlit/config.toml"]
    if not streamlit_cfg:
        raise FileNotFoundError("В архив не попал .streamlit/config.toml (fileWatcherType=none)")
    return unique


def build_archive() -> Path:
    assert_no_office_com()
    assert_bat_launchers_windows8()
    assert_streamlit_headless()
    assert_relative_paths()
    members = collect_members()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = DIST_DIR / ARCHIVE_NAME
    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in members:
            arcname = Path(ROOT_IN_ZIP) / rel
            zf.write(PROJECT_ROOT / rel, arcname.as_posix())
            console.print(f"[dim] + {rel.as_posix()}[/dim]")

    console.print(f"[green]Архив:[/green] {archive_path} ({archive_path.stat().st_size} байт, {len(members)} файлов)")
    return archive_path


def main() -> None:
    build_archive()


if __name__ == "__main__":
    main()
