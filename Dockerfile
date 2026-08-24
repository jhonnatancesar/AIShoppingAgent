# TASK-091 (item 1 da V1.2, endurecimento de 2026-08-21): build multi-stage
# -- Node só existe no estágio de build do frontend, nunca no runtime.
# TASK-109 (fechamento): `collection_worker` saiu desta imagem -- roda
# nativo no Windows, driblando um Microsoft Edge real via CDP. Restam
# `api` e `telegram_notifier` (mesma imagem, só o `api` serve a SPA);
# nenhum dos dois abre navegador, por isso o estágio Python não instala
# mais Chromium nem Xvfb.

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
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --system aishopping \
    && useradd --system --gid aishopping --home-dir /app --no-create-home aishopping

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/alembic.ini .
COPY backend/migrations ./migrations

# `frontend-dist` (não `frontend/dist`) -- fica fora de `./app`, para nunca
# ser confundido com o pacote Python `app`. `AISHOPPING_SPA_DIST_DIR`
# (compose.yaml, só no serviço `api`) aponta pra cá; `Settings.spa_dist_dir`
# continua `None` por padrão para o checkout local (resolve o caminho
# relativo ao repositório, fora do container).
COPY --from=frontend-build /frontend/dist ./frontend-dist

RUN chown -R aishopping:aishopping /app

USER aishopping

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
