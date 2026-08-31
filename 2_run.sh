#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x "./venv/bin/streamlit" ] && [ ! -f "./venv/bin/streamlit" ]; then
  echo "[ОШИБКА] venv не найден. Сначала запустите ./1_setup.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "./venv/bin/activate"
echo "============================================================"
echo "  WMS Parser (Региональный склад Челябинск)"
echo "  Сервер запущен: http://localhost:8501"
echo "  Пожалуйста, не закрывайте это окно во время работы."
echo "============================================================"
exec ./venv/bin/streamlit run app_ui.py --server.port 8501 --server.fileWatcherType none --browser.gatherUsageStats false
