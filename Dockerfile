# TASK-091 (item 1 da V1.2, endurecimento de 2026-08-21): build multi-stage
# -- Node só existe no estágio de build do frontend, nunca no runtime. Os
# três serviços (api, telegram_notifier, collection_worker) continuam
# compartilhando esta mesma imagem (`aishoppingagent-app:local` em
# compose.yaml); só o `api` de fato serve a SPA, mas nenhum dos três ganha
# uma imagem/Dockerfile separado só por causa do frontend.

# --- Estágio 1: build do frontend (React + Vite) ---------------------------
FROM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# --- Estágio 2: aplicação Python (runtime real) -----------------------------
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY backend/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

RUN groupadd --system aishopping \
    && useradd --system --gid aishopping --home-dir /app --no-create-home aishopping \
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/alembic.ini .
COPY backend/migrations ./migrations
COPY backend/docker-entrypoint.sh /usr/local/bin/aishopping-entrypoint
RUN chmod +x /usr/local/bin/aishopping-entrypoint

# `frontend-dist` (não `frontend/dist`) -- fica fora de `./app`, para nunca
# ser confundido com o pacote Python `app`. `AISHOPPING_SPA_DIST_DIR`
# (compose.yaml, só no serviço `api`) aponta pra cá; `Settings.spa_dist_dir`
# continua `None` por padrão para o checkout local (resolve o caminho
# relativo ao repositório, fora do container).
COPY --from=frontend-build /frontend/dist ./frontend-dist

RUN chown -R aishopping:aishopping /app /ms-playwright

USER aishopping

EXPOSE 8000

ENTRYPOINT ["aishopping-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
