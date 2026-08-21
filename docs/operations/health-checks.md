# Health checks

## Endpoints da API

| Endpoint | O que verifica | Resposta |
| --- | --- | --- |
| `GET /health` | Só que o processo da API está vivo — não consulta dependências | `200` `{"status":"ok"}` |
| `GET /ready` | Consulta mínima real ao PostgreSQL | `200` `{"status":"ready"}` ou `503` `{"status":"not_ready"}` |
| `GET /metrics` | Métricas Prometheus — operacional, não aparece no OpenAPI | Texto no formato Prometheus |

```powershell
curl.exe --fail --silent --show-error http://127.0.0.1:8000/health
curl.exe --fail --silent --show-error http://127.0.0.1:8000/ready
curl.exe --fail --silent --show-error http://127.0.0.1:8000/metrics
```

`/health=200` com `/ready=503` indica API viva, mas banco indisponível.
Prometheus, Jaeger e o Collector não determinam a readiness funcional da
API.

## Workers (`collection_worker`, `telegram_notifier`)

Não publicam porta HTTP no host (só `9464/tcp` interno ao Compose).
Verifique pelo estado do container e pelos logs:

```powershell
docker compose ps collection_worker telegram_notifier
docker compose logs --since=5m --tail=100 collection_worker
docker compose logs --since=5m --tail=100 telegram_notifier
```

## PostgreSQL

```powershell
docker compose exec -T database sh -c 'pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

## Observabilidade

```powershell
curl.exe --fail --silent --show-error http://127.0.0.1:9090/-/ready
curl.exe --fail --silent --show-error http://127.0.0.1:16686/api/services
curl.exe --fail --silent --show-error http://127.0.0.1:13133/
```

Essas três portas atendem só em `127.0.0.1` — para consultar remotamente,
use um túnel SSH ou uma rede administrativa privada (Tailscale), nunca
exponha diretamente.

## Telegram / Tailscale Funnel

O Funnel pode reportar "on" localmente sem estar de fato acessível de
fora. Verifique com o healthcheck dedicado:

```powershell
powershell -File scripts\funnel_healthcheck.ps1
Get-Content logs\funnel-healthcheck.log -Tail 20
```

E confira o lado do Telegram (não deve mostrar `pending_update_count`
crescendo):

```powershell
docker compose run --rm api python -m scripts.register_telegram_webhook --action info
```

Veja também [Troubleshooting](../troubleshooting.md#bot-do-telegram-não-responde).
