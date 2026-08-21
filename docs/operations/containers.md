# Containers

Os sete serviços definidos em `compose.yaml` (nome do projeto Compose:
`aishoppingagent`):

| Serviço | Função | Healthcheck |
| --- | --- | --- |
| `database` | PostgreSQL 18 — persistência | `pg_isready` |
| `api` | FastAPI — endpoints HTTP e webhook do Telegram | `GET /ready` |
| `collection_worker` | Executa coletas agendadas, classifica relevância via IA | `GET :9464/metrics` (interno) |
| `telegram_notifier` | Consome alertas de preço e envia mensagens | `GET :9464/metrics` (interno) |
| `otel-collector` | Recebe traces OTLP e encaminha ao Jaeger | endpoint `:13133` |
| `prometheus` | Coleta métricas por scrape | `GET /-/healthy` |
| `jaeger` | Armazena e exibe traces | `GET /api/services` |

Todos têm `restart: unless-stopped` — voltam a subir sozinhos quando o
Docker reinicia, salvo se tiverem sido parados manualmente antes.

## Listar e ver status

```powershell
docker compose ps
```

## Entrar num container (shell)

```powershell
docker compose exec api sh
docker compose exec database sh
```

Use `exec -T` (sem TTY) quando o comando for não interativo, por exemplo
dentro de um script:

```powershell
docker compose exec -T api sh -c "python --version"
```

## Ver e seguir logs

```powershell
# últimos 200 eventos de um serviço, últimos 10 minutos
docker compose logs --since=10m --tail=200 api

# seguir em tempo real
docker compose logs --follow api collection_worker telegram_notifier

# todos os serviços
docker compose logs --since=10m --tail=200
```

Os logs são JSON estruturado em `stdout` — veja o contrato dos campos em
[docs/operations/logging.md](logging.md). Não copie logs completos para
canais públicos, mesmo sanitizados.

## Reiniciar um serviço específico

```powershell
docker compose restart api
docker compose restart collection_worker telegram_notifier
```

## Parar e retomar (sem apagar dados)

```powershell
docker compose stop
docker compose start
```

## Subir tudo (cria o que faltar, não afeta o que já está rodando)

```powershell
docker compose up -d
```

## Executar um comando pontual dentro de um container já configurado

Usado para migrations, scripts administrativos e diagnósticos — cria um
container novo e temporário com a mesma imagem/configuração do serviço,
sem afetar o que já está rodando:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m scripts.register_telegram_webhook --action info
```

## Encerrar containers (preservando ou não os dados)

```powershell
# remove containers e rede; preserva os volumes (o banco continua intacto)
docker compose down

# ⚠️ além disso, apaga os volumes nomeados — destrói o PostgreSQL de produção
docker compose down --volumes
```

**Nunca** use `--volumes`/`-v` em produção sem uma decisão destrutiva
explícita e um backup validado em mãos — veja
[Backup e restauração](backup-restore.md).

## Diagnóstico de recursos

```powershell
docker system df
docker volume ls
docker images
```
