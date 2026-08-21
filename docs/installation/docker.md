# Ambiente local com Docker Compose

Este é o fluxo para rodar o AIShoppingAgent localmente, para desenvolvimento
e validação — não é o procedimento de produção. Para produção, veja
[Windows Server](windows-server.md) (plataforma atual) ou
[Linux](linux.md) (legado).

## Passo a passo

Copie o exemplo de configuração não sensível e inicialize os secrets com
entrada oculta. O Compose monta os valores em `/run/secrets`; não os
coloque no `.env` usado pelos contêineres:

```powershell
Copy-Item .env.example .env
python -m backend.scripts.manage_secrets init
python -m backend.scripts.manage_secrets check
docker compose up -d database
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose up --build
```

Quem já possui valores em `.env` pode usar
`python -m backend.scripts.manage_secrets migrate`, sem imprimir nem apagar
os arquivos de origem. Desenvolvimento Python fora do Docker ainda aceita
`backend/.env`; produção aceita somente `*_FILE`. Consulte
[Secrets](secrets.md), especialmente antes de rotacionar a senha de um
banco já inicializado.

## Portas expostas

| Serviço | Endereço local |
| --- | --- |
| API | `http://localhost:8000` |
| PostgreSQL | `localhost:5432` |
| Prometheus | `http://localhost:9090` |
| Jaeger | `http://localhost:16686` |

Essas portas usam `127.0.0.1` por padrão. PostgreSQL, Prometheus, Jaeger,
Collector e métricas do worker nunca devem ser abertos diretamente para a
Internet — veja o [Runbook de operação](../operations/runbook.md) antes de
alterar qualquer bind ou firewall.

O serviço `collection_worker` pesquisa as missões agendadas e publica
eventos; `telegram_notifier` consome continuamente os alertas de preço.
Para encerrar os contêineres sem apagar o volume do banco:

```powershell
docker compose down
```

## Verificação

Com a API em execução, verifique sua vivacidade em
`http://localhost:8000/health`. A resposta esperada é:

```json
{"status":"ok"}
```

`/health` não consulta dependências. Use `/ready` para verificar o
PostgreSQL real (`200` com `{"status":"ready"}` ou `503` com
`{"status":"not_ready"}`). `/metrics` é operacional e não aparece no
OpenAPI.

## Logs

Os logs da aplicação são emitidos como JSON em `stdout`:

```powershell
docker compose logs --follow api collection_worker telegram_notifier
```

O contrato dos eventos de log está em
[docs/operations/logging.md](../operations/logging.md).
