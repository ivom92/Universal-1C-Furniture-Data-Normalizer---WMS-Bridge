FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для lxml и сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости и устанавливаем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект (включая catalog_v8.xlsx и код)
COPY . .

# Порт Streamlit
EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Запуск Streamlit в headless-режиме
CMD ["streamlit", "run", "app_ui.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]