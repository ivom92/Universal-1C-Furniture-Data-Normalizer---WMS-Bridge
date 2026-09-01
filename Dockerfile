FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для lxml, сборки и healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Установка Python-зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода, каталогов и векторов
COPY . .

# Порт Streamlit
EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Запуск в headless-режиме с отключением строгой проверки CORS для обратного прокси
CMD ["streamlit", "run", "app_ui.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
