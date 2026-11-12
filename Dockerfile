# syntax=docker/dockerfile:1
FROM python:3.10-slim

WORKDIR /app

# Шрифт DejaVu нужен шаблону PDF (кириллица в booking_note.html).
# Компилятор и libcairo больше не нужны: все зависимости ставятся готовыми wheel-пакетами.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Зависимости — отдельным слоем: пересобирается только при изменении requirements.txt,
# а кэш pip переживает и это.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
