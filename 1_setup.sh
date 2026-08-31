#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Установка WMS Parser ==="

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ОШИБКА] python3 не найден. Нужен Python 3.11+."
  exit 1
fi

python3 --version
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"

echo "Создание venv..."
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "Прогрев кэша FAISS..."
./venv/bin/python scripts/check_system_health.py --warm

echo "Установка успешно завершена. Дальше: ./2_run.sh"
