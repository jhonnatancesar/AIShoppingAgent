FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

RUN groupadd --system aishopping \
    && useradd --system --gid aishopping --home-dir /app --no-create-home aishopping \
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

COPY app ./app
COPY scripts ./scripts
COPY alembic.ini .
COPY migrations ./migrations
COPY docker-entrypoint.sh /usr/local/bin/aishopping-entrypoint
RUN chmod +x /usr/local/bin/aishopping-entrypoint
RUN chown -R aishopping:aishopping /app /ms-playwright

USER aishopping

EXPOSE 8000

ENTRYPOINT ["aishopping-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
