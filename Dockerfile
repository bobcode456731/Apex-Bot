FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/apexbot.sqlite3

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app

COPY --chown=botuser:botuser apexbot ./apexbot
COPY --chown=botuser:botuser main.py ./main.py
USER botuser
VOLUME ["/app/data"]
CMD ["python", "main.py"]
