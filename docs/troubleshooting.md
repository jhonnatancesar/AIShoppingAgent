# Troubleshooting

Problemas comuns, como diagnosticar e o primeiro passo seguro. Para
comandos rápidos do dia a dia, veja o
[Runbook de operação](operations/runbook.md).

## Container não sobe

```powershell
docker compose ps
docker compose logs --since=10m --tail=200 <serviço>
```

Causas comuns: `.env`/secret faltando ou ilegível (produção exige
`*_FILE` — veja [Secrets](installation/secrets.md)), porta já ocupada no
host (ver abaixo), imagem desatualizada depois de uma mudança de código
(`docker compose build --pull`), ou dependência (`database`) ainda não
`healthy` — `depends_on: condition: service_healthy` já cobre isso para os
serviços que dependem do banco, mas confirme com `docker compose ps`.

## PostgreSQL indisponível

```powershell
docker compose ps database
docker compose logs --since=10m --tail=200 database
docker compose exec -T database sh -c 'pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

`/health=200` da API com `/ready=503` confirma: API viva, banco
inacessível. Verifique espaço em disco (`docker system df`) e se o volume
`aishoppingagent_postgres_data` existe (`docker volume ls`). Não apague o
volume como tentativa de correção.

## Migration falhou

```powershell
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m alembic -c alembic.ini history
```

Não execute downgrade para "testar" contra um banco com dados. Se a
migration falhou no meio, leia o erro real (constraint, tipo, dado
incompatível) antes de tentar de novo — veja
[Migrations](database/migrations.md). Em caso de dúvida sobre
compatibilidade entre código e schema, trate como rollback avaliado
manualmente, não como tentativa e erro.

## Um provider de loja está falhando (Pichau, Terabyte, Amazon, KaBuM!)

```powershell
docker compose logs --since=30m --tail=200 collection_worker
```

Procure por `collection_source_failed` nos logs — o formato estruturado
já inclui classe do erro, detalhe seguro, status HTTP e etapa. Timeout,
erro de transporte, `408`, `429` transitório e `5xx` contam para o circuit
breaker daquele Store Provider (chave independente por loja); `400`/`401`/
`403` e falhas de validação não contam — costumam indicar bloqueio
permanente (ex.: proteção anti-automação do próprio site) em vez de
instabilidade temporária. Um circuito aberto se recupera sozinho via sonda
`half-open`; não force reinício do worker como primeira tentativa.

## Bot do Telegram não responde

1. Confirme que a API está `/ready`;
2. confirme o Tailscale Funnel de fato acessível de fora (não só "on"
   localmente):

   ```powershell
   powershell -File scripts\funnel_healthcheck.ps1
   Get-Content logs\funnel-healthcheck.log -Tail 20
   ```

3. confirme o webhook registrado no Telegram:

   ```powershell
   docker compose run --rm api python -m scripts.register_telegram_webhook --action info
   ```

   `pending_update_count` maior que zero e crescendo indica que o Telegram
   não está conseguindo entregar — geralmente é o Funnel, não a aplicação.
4. veja os logs da API e do `telegram_notifier`:

   ```powershell
   docker compose logs --since=10m --tail=200 api telegram_notifier
   ```

Nunca coloque token ou segredo do webhook na URL nem em comando impresso.

## API não responde

```powershell
docker compose ps api
docker compose logs --since=10m --tail=200 api
curl.exe --fail --silent --show-error http://127.0.0.1:8000/health
```

Se `docker compose ps` mostra o container reiniciando repetidamente, veja
"Serviço em crash loop" abaixo antes de tentar mais restarts manuais.

## Variável de ambiente faltando

Erros de configuração ausente aparecem na inicialização do serviço, nos
logs (`docker compose logs <serviço>`), geralmente antes de qualquer
healthcheck passar. Confira a variável contra
[Configuração](installation/configuration.md) — produção rejeita secret em
valor direto (só aceita `*_FILE`) e falha fechado em vez de usar um
default inseguro.

## Porta já em uso no host

```powershell
docker compose config --quiet
```

Se `docker compose up` falhar por porta ocupada, outro processo (ou outra
instância do próprio Compose) já está usando `API_PORT`/`POSTGRES_PORT`/
`PROMETHEUS_PORT`/`JAEGER_UI_PORT`. Verifique com:

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
```

Ajuste a porta correspondente no `.env` ([Configuração](installation/configuration.md))
em vez de encerrar processos desconhecidos às cegas.

## Docker Desktop indisponível

```powershell
docker info
wsl --status
```

No Windows, o Docker Desktop depende do WSL2. Se `docker info` falhar,
confirme que o Docker Desktop está em execução e que o WSL2 não está em
estado inconsistente (`wsl --shutdown` seguido de reabrir o Docker Desktop
costuma recuperar). Veja também a configuração de memória do WSL2 em
[Windows Server](installation/windows-server.md#memória-do-wsl2) — memória
insuficiente pode causar instabilidade do daemon.

## Serviço em crash loop

```powershell
docker compose ps
docker compose logs --tail=200 <serviço>
```

Não fique reiniciando manualmente em loop. Leia o log do momento exato da
falha (não só o mais recente — o container pode já ter reiniciado
sozinho), identifique se é configuração (variável/secret), dependência
(banco indisponível) ou um erro de aplicação, e corrija a causa antes de
reiniciar de novo. Se envolver dado no banco, siga primeiro
[PostgreSQL](database/postgresql.md) para inspecionar antes de alterar
qualquer coisa.
