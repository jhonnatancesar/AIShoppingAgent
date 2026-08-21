# Instalação em produção — Windows Server

Guia completo, do zero, para colocar o AIShoppingAgent em execução num
Windows Server novo. **Esta é a plataforma de produção atual do projeto** —
o servidor autoritativo roda Windows Server com Docker Desktop (WSL2). Para
a instalação legada em Ubuntu Server, veja [Linux](linux.md).

Este guia é o ponto de entrada da instalação; ele referencia, e não
duplica, os documentos que já são fonte única de verdade para cada assunto:
[Configuração](configuration.md) e [Secrets](secrets.md) para variáveis e
credenciais, [PostgreSQL](../database/postgresql.md) para acesso ao banco,
[Migrations](../database/migrations.md) para Alembic,
[Backup e restauração](../operations/backup-restore.md) para o
procedimento completo de backup, e o
[Runbook de operação](../operations/runbook.md) para rotina, diagnóstico e
rollback depois que o sistema já está no ar.

## 1. Visão geral do fluxo

```
Git (tag da release) → Windows Server → .env / .secrets criados no servidor
  → Docker Desktop (build) → PostgreSQL → migrations Alembic
  → serviços (api, workers, observabilidade) → Tailscale Funnel → validação
```

Os mesmos princípios da instalação em Linux se aplicam integralmente:

- **O código vem do Git.** Clone direto do `origin`; nunca copie a pasta de
  outra máquina.
- **`.env` e `.secrets/` são criados diretamente no servidor**, nunca
  versionados, nunca copiados de outra máquina.
- **O PostgreSQL de produção nasce vazio** ou é restaurado de um backup de
  produção já validado — nunca do banco de desenvolvimento.
- **Nada do ambiente de desenvolvimento é copiado para produção.**

## 2. Pré-requisitos do servidor

- Windows Server com suporte a virtualização e WSL2 habilitados;
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) com o
  backend WSL2 (inclui o Docker Engine e o plugin `docker compose`);
- Git para Windows;
- a instalação oficial do Python da máquina, somente para os utilitários
  locais (`backend/scripts/manage_secrets.py`) — a aplicação em si roda
  inteiramente dentro dos containers;
- acesso de saída HTTPS liberado para: Telegram (`api.telegram.org`),
  Gemini, Groq, OpenRouter, Firecrawl, as lojas pesquisadas e o registro de
  imagens Docker;
- [Tailscale](https://tailscale.com/) instalado e autenticado, usado para
  expor o webhook do Telegram publicamente via **Tailscale Funnel** (é o
  mecanismo de HTTPS público realmente usado nesta instalação — veja a
  seção 10).

Valide o ambiente:

```powershell
git --version
docker --version
docker compose version
python --version
docker info
```

### Memória do WSL2

O Docker Desktop no Windows roda os containers dentro do WSL2. Limite a
memória e o swap usados pela distro WSL2 num arquivo `.wslconfig` no perfil
do usuário (`C:\Users\<usuário>\.wslconfig`):

```ini
[wsl2]
memory=6GB
swap=2GB

[experimental]
autoMemoryReclaim=gradual
```

Ajuste os valores conforme a RAM real disponível no servidor — a
configuração acima é a usada nesta instalação. Depois de criar ou alterar o
arquivo, reinicie o WSL2:

```powershell
wsl --shutdown
```

O Docker Desktop reinicia automaticamente a distro na próxima vez que for
usado.

## 3. Clonar o projeto

**Não copie a pasta do projeto de outra máquina.** O servidor obtém o
código diretamente do Git:

```powershell
git clone https://github.com/jhonnatancesar/AIShoppingAgent.git
cd AIShoppingAgent
git fetch --tags --prune
git checkout --detach <tag-da-release>
```

Confirme que o checkout aponta exatamente para o commit esperado da tag:

```powershell
git rev-parse HEAD
git rev-list -n1 <tag-da-release>
```

As duas saídas devem ser **idênticas**. Confirme também que a working tree
está limpa:

```powershell
git status --short --branch
```

Não use branch mutável em produção: nunca `git reset --hard`, nunca
`git pull` cego, nunca remova volumes como parte de uma atualização
automática.

## 4. Configuração (`.env`) e secrets

```powershell
Copy-Item .env.example .env
```

O `.env` não é versionado e nunca deve conter secrets (senha do banco,
chaves de IA, token do bot) — isso vai em `.secrets/`. A lista completa de
variáveis lidas por `compose.yaml`, com recomendação de produção para cada
uma, está em [Configuração](configuration.md).

Crie os secret files sem nunca exibir o valor no terminal:

```powershell
python -m backend.scripts.manage_secrets init
python -m backend.scripts.manage_secrets check
```

Detalhes completos (inventário de secrets, criação manual alternativa,
rotação, detecção de vazamento) estão em [Secrets](secrets.md).

Em produção, `AISHOPPING_ENVIRONMENT=production` no `.env` e
`AISHOPPING_AUTH_PUBLIC_BASE_URL` apontando para a URL pública real (nesta
instalação, o hostname do Tailscale Funnel — seção 10). Produção aceita
secrets exclusivamente por `*_FILE`.

## 5. PostgreSQL e migrations

Suba primeiro só o banco e valide a configuração:

```powershell
docker compose config --quiet
docker compose build --pull
docker compose up -d database
docker compose ps database
```

Espere o status `healthy`. Aplique as migrations Alembic pelo ambiente
reproduzível do Compose:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose run --rm api python -m alembic -c alembic.ini current
```

O segundo `current` deve mostrar a mesma revisão do topo de
`backend/migrations/versions/`. Detalhes completos do ciclo de migrations
estão em [Migrations](../database/migrations.md); para consultar o banco
diretamente, veja [PostgreSQL](../database/postgresql.md).

## 6. Build e inicialização dos serviços

`compose.yaml` define sete serviços: `database`, `api`, `collection_worker`,
`telegram_notifier`, `otel-collector`, `prometheus` e `jaeger`. Todos usam
`restart: unless-stopped`.

```powershell
docker compose up -d
docker compose ps
```

Todos os sete devem aparecer; os que têm healthcheck definido devem mostrar
`healthy`.

## 7. Verificação dos serviços

```powershell
curl.exe --fail --silent --show-error http://127.0.0.1:8000/health
curl.exe --fail --silent --show-error http://127.0.0.1:8000/ready
curl.exe --fail --silent --show-error http://127.0.0.1:9090/-/ready
curl.exe --fail --silent --show-error http://127.0.0.1:16686/api/services
curl.exe --fail --silent --show-error http://127.0.0.1:13133/
```

`/health=200` prova só que o processo da API está vivo; `/ready=200` inclui
uma consulta mínima real ao PostgreSQL. `collection_worker` e
`telegram_notifier` não publicam porta HTTP no host — verifique-os por
`docker compose ps` (estado `healthy`) e pelos logs:

```powershell
docker compose logs --since=5m --tail=100 collection_worker
docker compose logs --since=5m --tail=100 telegram_notifier
```

Mais comandos de diagnóstico do dia a dia estão em
[Containers](../operations/containers.md).

## 8. Criação do primeiro usuário `DEV`

Nenhum fluxo de cadastro pelo bot atribui `ADMIN` ou `DEV` — o cadastro
normal (`/cadastro`) sempre cria um usuário `USER`. A promoção do primeiro
`DEV` é uma operação manual única, feita diretamente no PostgreSQL. O
procedimento completo, com o SQL real, está em
[Administração de usuários](../administration/users.md#promover-o-primeiro-usuário-dev).

## 9. Telegram

- **Bot token**: criado com `@BotFather`, gravado em
  `.secrets/telegram_bot_token`.
- **Webhook secret**: valor aleatório, gravado em
  `.secrets/telegram_webhook_secret`, usado para autenticar que uma
  requisição realmente veio do Telegram.
- **Endpoint**: o webhook é servido pela própria API, no path
  `/telegram/webhook`, e exige uma URL HTTPS pública e estável.

### Tailscale Funnel como HTTPS público

Esta instalação usa o **Tailscale Funnel** para expor `/telegram/webhook`
publicamente, sem depender de domínio próprio e certificado TLS
gerenciados manualmente:

```powershell
tailscale funnel --bg 8000
tailscale funnel status
```

Isso publica a porta 8000 do host (onde a API está vinculada) em
`https://<hostname-tailscale>/`. Use esse endereço como
`AISHOPPING_AUTH_PUBLIC_BASE_URL` no `.env` e como base da URL do webhook
abaixo.

Registro do webhook e dos comandos:

```powershell
docker compose run --rm api python -m scripts.register_telegram_webhook `
  --action set --url https://<hostname-tailscale>/telegram/webhook
docker compose run --rm api python -m scripts.register_telegram_webhook `
  --action info
docker compose run --rm api python -m scripts.register_telegram_commands
```

`--action info` não exibe token nem segredo do webhook — só confirma a URL
registrada e o status da entrega.

### Healthcheck automático do Funnel

Depois de um reboot ou de qualquer reinício do Tailscale/Docker, o Funnel
pode reportar "on" localmente sem estar de fato acessível de fora. Uma
Scheduled Task do Windows verifica isso a cada 5 minutos e se
autorrecupera (reinicia o serviço Tailscale e reaplica o Funnel só depois
de 2 falhas seguidas, no máximo 1 restart a cada 10 min):

```powershell
# Registrar (uma vez, como Administrador):
powershell -File scripts\register_funnel_healthcheck_task.ps1

# Rodar manualmente / inspecionar:
powershell -File scripts\funnel_healthcheck.ps1
Get-ScheduledTask -TaskName AIShoppingAgent-FunnelHealthcheck
Get-ScheduledTaskInfo -TaskName AIShoppingAgent-FunnelHealthcheck
Get-Content logs\funnel-healthcheck.log -Tail 50
```

Isso cobre só a recorrência desse problema específico — não substitui
verificar `docker compose ps` e o `getWebhookInfo` do Telegram depois de
qualquer reboot ou incidente.

## 10. Validação inicial

Checklist de smoke test — sem gerar carga alta:

- [ ] PostgreSQL saudável;
- [ ] Migrations no head;
- [ ] API saudável (`/health` e `/ready` em `200`);
- [ ] `collection_worker` e `telegram_notifier` `healthy`, sem erro nos logs recentes;
- [ ] Telegram respondendo a `/start`;
- [ ] Cadastro funcionando (`/cadastro` completo);
- [ ] Login funcionando (`/recuperar` + `/entrar`);
- [ ] Missão pequena criada por texto livre, com confirmação;
- [ ] Uma coleta real pequena executada e persistida;
- [ ] Nenhum erro crítico (`level=ERROR`) inesperado nos logs.

## 11. Comportamento após reboot do servidor

Os sete serviços têm `restart: unless-stopped` — voltam a subir sozinhos
quando o Docker Desktop reinicia, desde que não tenham sido parados
manualmente antes do reboot. Ainda assim, confirme/complete manualmente
depois de reiniciar o servidor:

```powershell
cd C:\App\AIShoppingAgent
docker compose ps
docker compose up -d
docker compose ps
```

Confira também o Funnel do Tailscale (seção 9) e o `getWebhookInfo` do
Telegram — o healthcheck automático cobre a recorrência já conhecida desse
problema, mas vale confirmar depois de qualquer reboot.

## 12. Backup, restauração, atualização e operação contínua

Esses procedimentos não são específicos do Windows e por isso têm um único
documento cada, para não duplicar:

- [Backup e restauração](../operations/backup-restore.md);
- [Atualização entre releases](update.md);
- [Runbook de operação](../operations/runbook.md) — rotina, diagnóstico,
  rollback;
- [Troubleshooting](../troubleshooting.md) — problemas comuns e como
  resolvê-los.

## 13. Se precisar recriar o servidor do zero

1. Instalar Windows Server, Docker Desktop (WSL2) e Git (seção 2);
2. clonar o repositório (seção 3) — nunca copiar a pasta de outra máquina;
3. `git checkout --detach <tag-da-release-em-produção>`;
4. recriar `.env` a partir de `.env.example` (seção 4) — novo, criado no
   servidor;
5. recriar `.secrets/` com `manage_secrets.py init` (seção 4) — novo,
   criado no servidor;
6. restaurar o PostgreSQL a partir do backup mais recente validado
   ([Backup e restauração](../operations/backup-restore.md)) — nunca do
   banco de desenvolvimento;
7. aplicar as migrations Alembic compatíveis (seção 5);
8. subir os serviços (seção 6);
9. reconfigurar o Tailscale Funnel e o webhook do Telegram (seção 9);
10. executar os health checks e a validação inicial (seções 7 e 10).

Resumo do princípio: **o código vem do Git; os dados vêm do backup; os
secrets vêm da configuração criada diretamente no novo servidor; nada vem
do computador de desenvolvimento.**
