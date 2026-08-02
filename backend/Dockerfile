FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

COPY app ./app
COPY scripts ./scripts
COPY alembic.ini .
COPY migrations ./migrations
COPY docker-entrypoint.sh /usr/local/bin/aishopping-entrypoint
RUN chmod +x /usr/local/bin/aishopping-entrypoint

EXPOSE 8000

ENTRYPOINT ["aishopping-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
