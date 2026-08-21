# Guia de instalação em produção — Ubuntu Server

Manual completo, passo a passo, para instalar o AIShoppingAgent do zero em um
Ubuntu Server novo, a partir da release **`v1.0.1`**. Este documento é a fonte
única para recriar a produção sem depender de nenhuma conversa ou sessão
anterior.

Este guia **não substitui** `docs/OPERATIONS.md` (runbook de operação
contínua — rotina, rollback, diagnóstico, rotação de credenciais) nem
`docs/SECRETS.md` (gestão detalhada de secrets) nem `docs/MIGRATIONS.md`
(Alembic). Ele os referencia onde aplicável e não duplica o que já está
documentado ali.

> Nenhum comando deste documento foi executado como parte da sua criação.
> Este arquivo é só documentação.

## 1. Visão geral

Fluxo de instalação:

```
Git (tag v1.0.1) → servidor Ubuntu → .env / .secrets criados no servidor
  → Docker Compose (build) → PostgreSQL → migrations Alembic
  → serviços (api, workers, observabilidade) → validação
```

Princípios que este guia segue à risca:

- **O código vem do Git.** O servidor clona o repositório diretamente do
  `origin`; nunca copie a pasta do projeto de uma máquina de desenvolvimento.
- **Os secrets são criados diretamente no servidor.** `.env` e `.secrets/`
  nunca são versionados, nunca são copiados de outra máquina e nunca
  aparecem neste ou em qualquer outro documento.
- **Os dados persistentes vêm do PostgreSQL real da produção**, criado vazio
  nesta instalação, ou de um backup de produção já validado (`docs/OPERATIONS.md`)
  — nunca do banco de desenvolvimento.
- **Nada do ambiente de desenvolvimento é copiado para produção**: nenhum
  usuário de teste, missão, oferta, observação de preço, evento, sessão,
  token ou dado gerado por E2E.

## 2. Pré-requisitos do servidor

O que o projeto realmente usa (verificado em `docs/DEPENDENCIES.md`,
`docs/OPERATIONS.md` e `compose.yaml`):

- Ubuntu Server LTS x86-64, atualizado;
- acesso SSH com usuário próprio (não usar `root` diretamente);
- Git;
- Docker Engine (canal oficial);
- Docker Compose Plugin (`docker compose`, não o `docker-compose` em Python legado);
- timezone do servidor sincronizada (NTP);
- acesso de saída HTTPS liberado para: Telegram (`api.telegram.org`), Gemini,
  Groq, as lojas pesquisadas e o registro de imagens Docker.

Python **não precisa ser instalado no host** para rodar a aplicação — ela
roda inteiramente dentro dos containers. O Python do host só é necessário se
você quiser usar os utilitários locais (`backend/scripts/manage_secrets.py`)
fora do container; se preferir, o mesmo resultado pode ser obtido criando os
arquivos de secret manualmente (seção 6).

Dimensionamento observado pelo próprio projeto (`docs/OPERATIONS.md`, DEC-046):
a implantação de referência já validada roda com **8 GB de RAM**, com
`AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES=30` e
`AISHOPPING_COLLECTION_MAX_CONCURRENCY=2` como ajuste temporário para essa
memória (até 4 Chromium simultâneos seriam pesados demais em 8 GB). O alvo
pretendido pelo projeto ao migrar para 16 GB é `15`/`4` — não mude isso sem
seguir o procedimento da seção "Upgrade de 8 GB para 16 GB" em
`docs/OPERATIONS.md`.

| Recurso | Mínimo observado | Recomendado |
| --- | --- | --- |
| CPU | 2 vCPU | 4 vCPU |
| RAM | 8 GB | 16 GB (ver nota acima) |
| Disco | 20 GB livres | 40 GB+ (imagens Docker, volume PostgreSQL, backups, logs) |

Comandos para verificar o servidor antes de instalar:

```bash
lscpu | grep -E '^CPU\(s\)|Model name'
free -h
df -h /
lsb_release -a
uname -m
timedatectl
```

## 3. Preparação inicial do Ubuntu

```bash
sudo apt-get update
sudo apt-get upgrade -y
```

Timezone (ajuste para a zona real do servidor; UTC é uma escolha segura para
um servidor que só será acessado por SSH):

```bash
sudo timedatectl set-timezone UTC
timedatectl
```

Dependências mínimas de sistema realmente usadas pelo projeto (Git e os
pacotes exigidos pela instalação oficial do Docker):

```bash
sudo apt-get install -y ca-certificates curl gnupg git
```

Instalar o Docker Engine e o plugin Compose pelo canal oficial (repositório
`apt` da Docker, não o pacote `docker.io` do Ubuntu):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Habilitar o Docker no boot:

```bash
sudo systemctl enable docker
sudo systemctl start docker
sudo systemctl status docker --no-pager
```

Permitir que o usuário operacional use o Docker sem `sudo` — `docs/OPERATIONS.md`
alerta que isso **equivale, na prática, a acesso privilegiado ao host**;
limite esse grupo aos operadores autorizados:

```bash
sudo usermod -aG docker "$USER"
```

Encerre a sessão SSH e reconecte para que o novo grupo seja aplicado, depois
valide:

```bash
git --version
docker --version
docker compose version
docker info
```

Não instale nada além disso (sem Python de aplicação no host, sem Node, sem
`docker-compose` standalone em Python) — o projeto não usa.

## 4. Clonar o projeto

**Não copie a pasta do projeto do computador de desenvolvimento para o
servidor.** O servidor obtém o código diretamente do Git.

```bash
git clone https://github.com/jhonnatancesar/AIShoppingAgent.git
cd AIShoppingAgent
git fetch --tags --prune
git checkout --detach v1.0.1
```

Confirme que o checkout aponta exatamente para o commit esperado da tag:

```bash
git rev-parse HEAD
git rev-list -n1 v1.0.1
```

As duas saídas devem ser **idênticas**. Confirme também que a working tree
está limpa (nenhuma diferença, nenhum arquivo extra):

```bash
git status --short --branch
```

Não use branch mutável em produção. `docs/OPERATIONS.md` reforça: nunca
`git reset --hard`, nunca `git pull` cego, nunca remover volumes como parte
de uma atualização automática.

## 5. `.env` de produção

`.env` **não é versionado** (confirmado em `.gitignore`) e deve ser criado
diretamente no servidor a partir do exemplo não sensível:

```bash
cp .env.example .env
chmod 600 .env
```

Este arquivo é lido pelo **Docker Compose** para preencher variáveis não
sensíveis dos serviços (`compose.yaml`) — ele nunca deve conter secrets
(senha do banco, chaves de IA, token do bot); isso vai em `.secrets/`
(seção 6). Abaixo, **somente as variáveis que `compose.yaml` realmente lê**,
verificadas diretamente no arquivo (nenhuma inventada):

### Aplicação e ambiente

| Variável | Finalidade | Obrigatória | Exemplo/valor esperado | Recomendação para produção |
| --- | --- | --- | --- | --- |
| `AISHOPPING_APP_NAME` | Nome da aplicação nos logs | Opcional | `AIShoppingAgent` | Manter o padrão |
| `AISHOPPING_ENVIRONMENT` | Modo de execução | **Sim** | `production` | **Deve ser `production`** — só nesse modo os secrets `*_FILE` são exigidos e valores diretos são rejeitados |
| `AISHOPPING_DEBUG` | Liga modo debug | Opcional | `false` | Manter `false` |
| `AISHOPPING_LOG_LEVEL` | Nível de log | Opcional | `INFO` | `INFO`; use `WARNING`/`ERROR` só se o volume de log for um problema |
| `AISHOPPING_AUTH_PUBLIC_BASE_URL` | Origem pública usada pelos links de senha/recuperação enviados no Telegram | **Sim** | `http://localhost:8000` no exemplo | **Precisa ser a URL HTTPS pública e estável do servidor** (domínio + TLS). Enquanto essa infraestrutura não existir, marque a instância como não definitiva — ver seção 10 |

### Rede e portas (host)

| Variável | Finalidade | Obrigatória | Exemplo | Recomendação |
| --- | --- | --- | --- | --- |
| `API_BIND_ADDRESS` | Interface onde a API é publicada no host | Opcional | `127.0.0.1` | Manter `127.0.0.1` — nunca `0.0.0.0` (ver seção 19) |
| `API_PORT` | Porta publicada da API | Opcional | `8000` | Manter, salvo conflito de porta |
| `POSTGRES_BIND_ADDRESS` | Interface do PostgreSQL no host | Opcional | `127.0.0.1` | Manter `127.0.0.1` — nunca expor publicamente |
| `POSTGRES_PORT` | Porta publicada do PostgreSQL | Opcional | `5432` | Manter, salvo conflito |
| `PROMETHEUS_BIND_ADDRESS` | Interface do Prometheus | Opcional | `127.0.0.1` | Manter `127.0.0.1` |
| `PROMETHEUS_PORT` | Porta do Prometheus | Opcional | `9090` | Manter |
| `JAEGER_BIND_ADDRESS` | Interface do Jaeger | Opcional | `127.0.0.1` | Manter `127.0.0.1` |
| `JAEGER_UI_PORT` | Porta da UI do Jaeger | Opcional | `16686` | Manter |
| `OTEL_HEALTH_PORT` | Porta de health do OpenTelemetry Collector | Opcional | `13133` | Manter |

### PostgreSQL (não sensível)

| Variável | Finalidade | Obrigatória | Exemplo | Recomendação |
| --- | --- | --- | --- | --- |
| `POSTGRES_DB` | Nome do banco | Opcional | `aishoppingagent` | Manter o padrão, salvo necessidade específica |
| `POSTGRES_USER` | Usuário do papel PostgreSQL | Opcional | `aishoppingagent` | Manter o padrão |
| `AISHOPPING_SECRETS_DIR` | Caminho (não secreto) de onde o Compose lê os arquivos de secret | Opcional | `./.secrets` | Manter, salvo estrutura de disco diferente |

A senha em si (`POSTGRES_PASSWORD`) **não vai no `.env`** — vai em
`.secrets/postgres_password` (seção 6).

### Observabilidade

| Variável | Finalidade | Obrigatória | Exemplo | Recomendação |
| --- | --- | --- | --- | --- |
| `AISHOPPING_OBSERVABILITY_ENABLED` | Liga métricas Prometheus/traces OTLP | Opcional | `true` | Manter `true` se for usar Prometheus/Jaeger |
| `AISHOPPING_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Endpoint interno do Collector | Opcional | `http://otel-collector:4318/v1/traces` | Manter — é o nome do serviço no Compose, não muda |
| `AISHOPPING_TRACE_SAMPLE_RATIO` | Proporção de traces amostrados (0–1) | Opcional | `1.0` | Reduzir (ex.: `0.1`) só se o volume de traces for um problema |
| `AISHOPPING_READINESS_TIMEOUT_SECONDS` | Timeout da consulta de `/ready` ao banco | Opcional | `1.0` | Manter |
| `AISHOPPING_WORKER_METRICS_PORT` | Porta interna de métricas dos workers | Opcional | `9464` | Manter — não é publicada no host |

### Limites e resiliência

| Variável | Finalidade | Obrigatória | Exemplo | Recomendação |
| --- | --- | --- | --- | --- |
| `AISHOPPING_MAX_REQUEST_BODY_BYTES` | Limite de corpo HTTP aceito | Opcional | `65536` | Manter, salvo necessidade específica |
| `AISHOPPING_TELEGRAM_RATE_LIMIT_PER_MINUTE` | Limite de mensagens processadas por minuto | Opcional | `20` | Manter |
| `AISHOPPING_EXTERNAL_HTTP_TIMEOUT_SECONDS` | Timeout de chamadas HTTP/API externas (IA, Telegram) | Opcional | `10` | Manter |
| `AISHOPPING_BROWSER_NAVIGATION_TIMEOUT_SECONDS` | Timeout de navegação do Playwright (`page.goto`) nas lojas — só lido pelo `collection_worker`, desacoplado do timeout de API acima desde a correção da TASK-075 (carregar uma página completa via Chromium é mais lento que uma chamada de API; 10s causava falha real na Pichau, que mede ~20-25s) | Opcional | `45` | Manter — só reduza se validar que todas as lojas carregam bem abaixo disso |
| `AISHOPPING_SAFE_RETRY_MAX_ATTEMPTS` | Tentativas máximas em operações seguras | Opcional | `3` | Manter |
| `AISHOPPING_RETRY_BASE_DELAY_SECONDS` | Atraso inicial de retry | Opcional | `0.25` | Manter |
| `AISHOPPING_RETRY_MAX_DELAY_SECONDS` | Atraso máximo de retry | Opcional | `5` | Manter |
| `AISHOPPING_RETRY_AFTER_CAP_SECONDS` | Teto para `Retry-After` de terceiros | Opcional | `30` | Manter |
| `AISHOPPING_CIRCUIT_FAILURE_THRESHOLD` | Falhas até abrir o circuit breaker | Opcional | `5` | Manter |
| `AISHOPPING_CIRCUIT_OPEN_SECONDS` | Tempo de circuito aberto | Opcional | `30` | Manter |
| `AISHOPPING_EVENT_CONSUMER_MAX_ATTEMPTS` | Tentativas do consumidor de eventos | Opcional | `5` | Manter |
| `AISHOPPING_EVENT_RETRY_BASE_SECONDS` | Atraso inicial do consumidor de eventos | Opcional | `60` | Manter |
| `AISHOPPING_EVENT_RETRY_CAP_SECONDS` | Teto de atraso do consumidor de eventos | Opcional | `900` | Manter |
| `AISHOPPING_WORKER_FAILURE_BACKOFF_SECONDS` | Atraso após falha de worker | Opcional | `5` | Manter |

### Coleta (`collection_worker`)

Confirmadas em `compose.yaml`; **não aparecem em `.env.example` hoje**, mas
são lidas de verdade pelos serviços `api` e `collection_worker` — se você
quiser mudar o comportamento padrão, adicione-as ao seu `.env`:

| Variável | Finalidade | Obrigatória | Default efetivo no Compose | Recomendação |
| --- | --- | --- | --- | --- |
| `AISHOPPING_COLLECTION_POLL_SECONDS` | Intervalo de polling do worker | Opcional | `15` | Manter |
| `AISHOPPING_COLLECTION_BATCH_SIZE` | Missões por lote | Opcional | `25` | Manter |
| `AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES` | Intervalo entre coletas da mesma missão | Opcional | `30` (temporário, DEC-046, servidor de 8 GB) | Não altere para `15` sem seguir o procedimento de "Upgrade de 8 GB para 16 GB" em `docs/OPERATIONS.md` — mudar só a env var não migra agendas já persistidas |
| `AISHOPPING_COLLECTION_SCHEDULE_STAGGER_SECONDS` | Dispersão aleatória do primeiro agendamento | Opcional | `300` | Manter |
| `AISHOPPING_COLLECTION_STALE_RUN_MINUTES` | Tempo até uma execução travada ser considerada obsoleta | Opcional | `10` | Manter |
| `AISHOPPING_COLLECTION_MAX_CONCURRENCY` | Missões coletadas em paralelo (Chromium simultâneos) | Opcional | `2` (temporário, DEC-046, servidor de 8 GB) | Não suba para `4` sem confirmar 16 GB de RAM disponíveis de verdade |

> **Achado da auditoria dos arquivos atuais, documentado aqui para não
> repetir o engano:** o `compose.yaml` atual **não lê**
> `AISHOPPING_TELEGRAM_NOTIFICATION_POLL_SECONDS`/`_BATCH_SIZE` em nenhum
> serviço — os defaults efetivos vêm sempre do código, independentemente do
> que for colocado em `.env`, a menos que `compose.yaml` seja editado para
> repassá-las. Não coloque expectativa de que alterar essas duas no `.env`
> da raiz muda o comportamento do Compose hoje — isso exigiria uma mudança
> de código, fora do escopo deste documento.
>
> **Correção aplicada (TASK-065, 2026-08-10):** `AISHOPPING_GEMINI_MODEL` e
> `AISHOPPING_GROQ_MODEL` tinham o mesmo problema (sem propagação real pelo
> `compose.yaml`, nome do modelo sempre o default do código —
> `gemini-3.6-flash`/`openai/gpt-oss-120b`) e foram **removidas** de
> `.env.example` (raiz) e `backend/.env.example` — nenhum código,
> `compose.yaml` ou o servidor de produção da `v1.0.1` já implantado foram
> alterados por essa limpeza; um `.env` de produção antigo que ainda tenha
> essas duas linhas continua inofensivo (elas já não tinham efeito antes
> desta TASK).

## 6. Secrets

`docs/SECRETS.md` é a fonte de verdade completa (inventário, rotação,
detecção de vazamento). Esta seção documenta só a criação inicial.

Lista de secrets **verificada em `compose.yaml`** (seção `secrets:` no fim
do arquivo) — 6 arquivos, sem nenhum a mais ou a menos:

- `postgres_password`
- `gemini_api_key_user`
- `gemini_api_key_admin_dev`
- `groq_api_key`
- `telegram_bot_token`
- `telegram_webhook_secret`

Menor privilégio por serviço (verificado em `compose.yaml`, tabela completa em `docs/SECRETS.md`):

| Secret | `api` | `collection_worker` | `telegram_notifier` | `database` |
| --- | --- | --- | --- | --- |
| `postgres_password` | sim | sim | sim | sim |
| `gemini_api_key_user` | sim | não | não | não |
| `gemini_api_key_admin_dev` | sim | sim | não | não |
| `groq_api_key` | sim | sim | não | não |
| `telegram_bot_token` | sim | não | sim | não |
| `telegram_webhook_secret` | sim | não | não | não |

### Criação (método recomendado — usa o utilitário do projeto)

O projeto já tem um utilitário que gera e valida esses arquivos **sem nunca
exibir o valor no terminal**: `backend/scripts/manage_secrets.py`. É o mesmo
mecanismo usado em desenvolvimento (`docs/SECRETS.md`), rodando aqui contra
o servidor de produção:

```bash
python3 -m backend.scripts.manage_secrets init
```

Isso, sem imprimir nenhum valor:

- gera automaticamente `postgres_password` e `telegram_webhook_secret`
  (aleatórios, via `secrets.token_urlsafe`);
- pede, com entrada oculta (`getpass`), os quatro valores externos reais:
  `gemini_api_key_user`, `gemini_api_key_admin_dev`, `groq_api_key` e
  `telegram_bot_token`.

Depois, valide sem exibir os valores:

```bash
python3 -m backend.scripts.manage_secrets check
```

Ajuste as permissões (o utilitário já grava os arquivos com `0600`, mas
confirme o diretório):

```bash
chmod 700 .secrets
chmod 600 .secrets/*
stat -c '%a %n' .secrets .secrets/*
```

Esperado: `700 .secrets` e `600` para cada arquivo dentro dele.

### Criação manual (alternativa, se preferir não usar o utilitário)

```bash
mkdir -p .secrets
chmod 700 .secrets
```

Cada arquivo contém **uma única linha, sem quebra de linha visível no
conteúdo, sem espaços extras** — o `config.py` da aplicação rejeita valor
multilinha, vazio ou maior que 16 KiB. Exemplo de criação sem deixar o
valor no histórico do shell (o `read -s` oculta a digitação; substitua pelos
valores reais na hora, nunca grave o exemplo abaixo com um valor real):

```bash
umask 077
read -rs -p "postgres_password: " VALOR && printf '%s' "$VALOR" > .secrets/postgres_password && unset VALOR
read -rs -p "gemini_api_key_user: " VALOR && printf '%s' "$VALOR" > .secrets/gemini_api_key_user && unset VALOR
read -rs -p "gemini_api_key_admin_dev: " VALOR && printf '%s' "$VALOR" > .secrets/gemini_api_key_admin_dev && unset VALOR
read -rs -p "groq_api_key: " VALOR && printf '%s' "$VALOR" > .secrets/groq_api_key && unset VALOR
read -rs -p "telegram_bot_token: " VALOR && printf '%s' "$VALOR" > .secrets/telegram_bot_token && unset VALOR
read -rs -p "telegram_webhook_secret: " VALOR && printf '%s' "$VALOR" > .secrets/telegram_webhook_secret && unset VALOR
chmod 600 .secrets/*
```

`gemini_api_key_user` e `gemini_api_key_admin_dev` devem ser **chaves
diferentes** (contas/cotas separadas): `gemini_api_key_user` é usada
exclusivamente pelo perfil `USER` real no Telegram; `gemini_api_key_admin_dev`
é usada pela cascata `ADMIN`/`DEV` e pelo `collection_worker` — nunca
compartilhe a cota entre os dois (`docs/AI_PROVIDER_MANAGER.md`).
`telegram_webhook_secret` pode ser gerado com:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Verificação sem imprimir o segredo

```bash
python3 -m backend.scripts.manage_secrets check
stat -c '%a %n' .secrets .secrets/*
wc -c .secrets/*
```

`wc -c` mostra só o tamanho em bytes de cada arquivo — nunca o conteúdo.

### Regra permanente

`.env` e `.secrets/`:

- **nunca entram no Git** (confirmados no `.gitignore`; auditoria completa do
  histórico não encontrou nenhum caso — ver seção "Segurança" abaixo);
- **nunca devem aparecer em nenhuma documentação**, incluindo este arquivo;
- **permanecem só no servidor** onde foram criados. Não copie `.secrets/`
  entre servidores por `scp`/`rsync` casual — se precisar migrar, trate como
  rotação de credencial (`docs/SECRETS.md`) ou gere novos valores no destino.

## 7. Chaves não utilizadas

Uma auditoria do ambiente de desenvolvimento encontrou, num `backend/.env`
local, chaves configuradas para **OpenAI**, **Anthropic** e **Grok**. Essas
três chaves **não existem no `Settings` da aplicação** (`backend/app/core/config.py`)
e **nenhum código as lê** — não fazem parte da cascata do `AIProviderManager`,
que hoje usa somente Gemini (Flash) e Groq.

**Não configure essas três chaves em produção.** Não crie arquivos de
secret para elas, não adicione variáveis para elas no `.env`. Se algum dia o
projeto passar a depender de um provedor adicional, isso exigirá uma TASK
própria (`AGENTS.md`) e este documento será atualizado então.

## 8. PostgreSQL

**Produção nasce com banco novo.** O volume nomeado
`aishoppingagent_postgres_data` (declarado em `compose.yaml`) é criado vazio
na primeira subida do serviço `database` — **não restaure nem copie o banco
de desenvolvimento para cá.**

O serviço `database` no `compose.yaml` usa a imagem `postgres:18-alpine`,
inicializada com `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD_FILE`
(secret) e o volume `postgres_data` montado em `/var/lib/postgresql`.

### Subir só o banco primeiro

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d database
docker compose ps database
```

Espere o status `healthy` (healthcheck do Compose usa `pg_isready`, a cada
5s, até 10 tentativas).

### Migrations (Alembic)

As revisões ficam em `backend/migrations/versions/`. A migration mais
recente na release `v1.0.1` é `20260809_0004` (verifique sempre com o
comando abaixo — releases futuras terão um head diferente):

```bash
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose run --rm api python -m alembic -c alembic.ini current
```

O segundo `current` deve mostrar a mesma revisão do topo de
`backend/migrations/versions/` (a de data mais recente). Uma migration
compartilhada nunca é reescrita; nunca use downgrade para "testar" contra um
banco com dados (`docs/MIGRATIONS.md`, `docs/OPERATIONS.md`).

### Acesso direto ao PostgreSQL pelo container

```bash
docker compose exec -T database sh -c \
  'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

### Saúde/conectividade

```bash
docker compose exec -T database sh -c \
  'pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
curl --fail --silent --show-error http://127.0.0.1:8000/ready
```

`/ready` (da API) faz uma consulta mínima real ao PostgreSQL — `200` confirma
conectividade de ponta a ponta; `503` indica banco inacessível mesmo com a
API viva.

### O que não deve ser transferido do desenvolvimento

Nenhum dado de desenvolvimento — nenhum(a):

- usuário;
- missão;
- produto;
- oferta;
- observação de preço;
- evento;
- sessão de autenticação (`user_auth_sessions`);
- token de ação (`credential_action_tokens`);
- recibo de update do Telegram (`telegram_update_receipts`);
- dado gerado por testes E2E ou validação manual.

A produção começa com essas tabelas vazias, populadas só por uso real depois
do primeiro cadastro.

## 9. Criação do primeiro DEV

O cadastro normal do sistema (comando `/cadastro` no Telegram) sempre cria um
usuário com papel `USER` — **nenhum fluxo de cadastro atribui `ADMIN` ou
`DEV`** (`docs/USERS.md`, `docs/tasks/TASK-047.md`). O projeto **não possui,
hoje, nenhum mecanismo administrativo automatizado** para promoção de papel —
a única promoção já feita (o proprietário da V1) foi uma operação manual
única, auditada, por UUID validado diretamente no banco. Documentando esse
mesmo procedimento manual, com o schema exato verificado em
`backend/app/users/models.py` e na migration `20260802_0002_create_users.py`:

- tabela: `users`;
- coluna do papel: `role`, `VARCHAR(16)` com `CHECK` restringindo aos valores
  exatos `'USER'`, `'ADMIN'`, `'DEV'` (não é um tipo `ENUM` nativo do
  PostgreSQL — é texto com constraint);
- chave primária: `id` (`UUID`).

Passo a passo:

1. **Suba o sistema completo** (seções 8 e 11) e confirme que a API está
   `/ready`.
2. **Faça o cadastro normalmente pelo Telegram**, com a conta que você quer
   promover: `/start`, depois `/cadastro`, seguindo os passos pedidos pelo
   bot (usuário, e-mail, lojas favoritas, categorias). Ao final, o bot envia
   o link HTTPS para criar a senha; o mesmo link pode ser solicitado com
   `/recuperar`. Complete a criação de senha e faça `/entrar` para confirmar
   que o login funciona como `USER` comum.
3. **Localize o usuário recém-criado no PostgreSQL.** O jeito mais confiável
   é pelo `telegram_user_id` (o identificador numérico da sua conta do
   Telegram) ou, na ausência dele, pelo `display_name`/`username` escolhidos
   no cadastro:

   ```sql
   SELECT id, display_name, username, role, telegram_user_id, created_at
   FROM users
   ORDER BY created_at DESC
   LIMIT 5;
   ```

4. **Confira o papel atual** do usuário encontrado (substitua
   `<uuid-do-usuario>` pelo `id` retornado acima):

   ```sql
   SELECT id, display_name, role FROM users WHERE id = '<uuid-do-usuario>';
   ```

   O resultado esperado antes da promoção é `role = 'USER'`.

5. **Promova de `USER` para `DEV`** (a promoção real da V1 foi direto para
   `DEV`, mantendo a hierarquia `USER ⊂ ADMIN ⊂ DEV` — `DEV` já contém todas
   as permissões de `ADMIN`, `backend/app/authorization/policy.py`):

   ```sql
   UPDATE users
   SET role = 'DEV', updated_at = now()
   WHERE id = '<uuid-do-usuario>';
   ```

6. **Confirme a alteração**:

   ```sql
   SELECT id, display_name, role, updated_at FROM users WHERE id = '<uuid-do-usuario>';
   ```

   O resultado esperado agora é `role = 'DEV'`.

Execute os comandos acima dentro do container, sem deixar o UUID nem
qualquer dado pessoal em logs persistentes:

```bash
docker compose exec -T database sh -c \
  'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

Depois de promovido, `/entrar` novamente no Telegram para que a sessão
reflita o novo papel; interações seguintes desse usuário passam a usar a
cascata `ADMIN`/`DEV` do `AIProviderManager` em vez do Gemini gratuito do
perfil `USER` (`docs/AI_PROVIDER_MANAGER.md`).

## 10. Telegram

- **Bot token**: criado com `@BotFather` no Telegram, gravado em
  `.secrets/telegram_bot_token` (seção 6) — nunca em texto plano em nenhum
  outro lugar.
- **Webhook secret**: valor aleatório gerado localmente
  (`secrets.token_urlsafe`), gravado em `.secrets/telegram_webhook_secret`;
  usado pela API para autenticar em tempo constante que uma requisição
  recebida realmente veio do Telegram.
- **Endpoint usado pela aplicação**: o webhook do Telegram é servido pela
  própria API (`app.telegram.router`), no path `/telegram/webhook`.

> **Dependência explícita de infraestrutura ainda não definida:** registrar
> o webhook exige uma **URL HTTPS pública e estável** apontando para a API
> do servidor — isso depende de domínio e TLS, que **não fazem parte deste
> guia** nem do escopo já aprovado da V1 (`docs/OPERATIONS.md` é explícito:
> "não há domínio, TLS ou reverse proxy permanentes"). Não invente aqui um
> domínio ou uma URL pública. Quando essa infraestrutura existir, substitua
> `https://dominio.exemplo/telegram/webhook` abaixo pela URL real.

Registro do webhook e dos comandos, depois que a URL pública existir:

```bash
docker compose run --rm api python -m scripts.register_telegram_webhook \
  --action set --url https://dominio.exemplo/telegram/webhook
docker compose run --rm api python -m scripts.register_telegram_webhook \
  --action info
docker compose run --rm api python -m scripts.register_telegram_commands
```

`--action info` não exibe o token nem o segredo do webhook — só confirma a
URL registrada e o status da entrega. Comandos registrados atualmente pelo
projeto (`backend/scripts/register_telegram_commands.py`, 13 comandos):
`/start`, `/ajuda`, `/criar_missao`, `/cancelar_missao`, `/listar_missoes`, `/missao`,
`/editar_missao`, `/cadastro`, `/entrar`, `/recuperar`, `/sair`,
`/preferencias` e `/privacidade`. O campo formal da Bot API usa underscore;
o webhook também aceita os aliases digitados com hífen. `missoes` e `missões`
também acionam a listagem de forma determinística.

Para desenvolvimento/validação temporária (nunca como URL definitiva de
produção), o projeto usa `cloudflared`; remova o webhook temporário ao
final:

```bash
docker compose run --rm api python -m scripts.register_telegram_webhook \
  --action delete
```

### Teste inicial do bot

Com o webhook público registrado:

1. `/start` — confirme resposta de boas-vindas;
2. `/cadastro` — complete o fluxo (usuário, e-mail, lojas, categorias);
3. `/recuperar` (ou o link enviado ao final do cadastro) — crie ou redefina a
   senha pelo formulário HTTPS, nunca pelo chat;
4. `/entrar` — confirme login bem-sucedido (sessão absoluta de 12 horas);
5. **Missão pequena**: não existe comando dedicado — depois de autenticado,
   escreva uma frase livre descrevendo o que procura (ex.: "quero um mouse
   gamer até 200 reais"); o `IntentInterpreter` classifica como
   `create_mission` e o bot pede confirmação explícita antes de criar a
   missão de fato. Confirme com uma resposta afirmativa.

## 11. Build e inicialização

Serviços realmente presentes em `compose.yaml` (7, nenhum a mais):

| Serviço | Função | Depende de | Healthcheck |
| --- | --- | --- | --- |
| `database` | PostgreSQL 18 — persistência | — | `pg_isready`, 5s |
| `api` | FastAPI — endpoints HTTP e webhook do Telegram | `database` (healthy) | `GET /ready`, 5s |
| `collection_worker` | Executa coletas agendadas, classifica relevância via IA | `database` (healthy) | `GET :9464/metrics`, 10s (interno) |
| `telegram_notifier` | Consome alertas de preço e envia mensagens | `database` (healthy) | `GET :9464/metrics`, 10s (interno) |
| `otel-collector` | Recebe traces OTLP e encaminha ao Jaeger | `jaeger` (started) | sem healthcheck no Compose; endpoint `:13133` disponível para checagem manual |
| `prometheus` | Coleta métricas por scrape | `api` (healthy) | `GET /-/healthy`, 10s |
| `jaeger` | Armazena e exibe traces | — | `GET /api/services`, 10s |

Os sete serviços têm `restart: unless-stopped` — isso importa para a
seção 15.

> **Correção aplicada (TASK-066, 2026-08-10):** até a `v1.0.1`, só
> `collection_worker` e `telegram_notifier` tinham `restart:
> unless-stopped`; os outros cinco exigiam `docker compose up -d` manual
> após reboot/crash. A auditoria da TASK-066 encontrou que essa política
> parcial já era contraditória — `collection_worker`/`telegram_notifier`
> dependem de `database`, que não voltava sozinho — e concluiu que os 7
> serviços qualificam para `restart: unless-stopped` (nenhum é job
> pontual, nenhum tem efeito colateral destrutivo em restart automático).
> A produção da `v1.0.1` já implantada não foi alterada por esta TASK —
> aplicar a mudança lá é uma atualização operacional separada.

Ordem real de inicialização (já coberta na seção 8 para o banco/migrations):

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d database
docker compose ps database
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose up -d
docker compose ps
```

`docker compose up -d` sobe todos os sete serviços em segundo plano; a
ordem entre eles é resolvida pelo próprio Compose a partir do `depends_on`.

## 12. Verificação dos serviços

```bash
docker compose ps
```

Todos os sete devem aparecer, os que têm healthcheck como `healthy`.

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
curl --fail --silent --show-error http://127.0.0.1:8000/ready
curl --fail --silent --show-error http://127.0.0.1:8000/metrics >/dev/null
docker compose exec -T database sh -c \
  'pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
curl --fail --silent --show-error http://127.0.0.1:9090/-/ready >/dev/null
curl --fail --silent --show-error http://127.0.0.1:16686/api/services >/dev/null
curl --fail --silent --show-error http://127.0.0.1:13133/ >/dev/null
```

`collection_worker` e `telegram_notifier` não publicam porta HTTP no host
(só `9464/tcp` interno) — verifique-os por `docker compose ps` (estado
`healthy`) e por log:

```bash
docker compose logs --since=5m --tail=100 collection_worker
docker compose logs --since=5m --tail=100 telegram_notifier
```

## 13. Validação inicial de produção

Checklist de smoke test — **sem gerar carga alta** (uma missão pequena, uma
única fonte, é suficiente; não repita testes em volume):

- [ ] PostgreSQL saudável (`pg_isready`, seção 12);
- [ ] Migrations no head (`alembic current` bate com o topo de `backend/migrations/versions/`);
- [ ] API saudável (`/health` e `/ready` retornando `200`);
- [ ] `collection_worker` ativo (`docker compose ps` = `healthy`, sem erro nos logs recentes);
- [ ] `telegram_notifier` ativo (`docker compose ps` = `healthy`, sem erro nos logs recentes);
- [ ] Gemini Flash respondendo (uma interação real no Telegram autenticado — ex.: uma frase livre — deve ser classificada sem erro);
- [ ] Fallback Groq funcionando quando aplicável (não force isso artificialmente; se aparecer `ai_outcome=quota_exceeded`/`unavailable` seguido de `succeeded` via Groq nos logs, o fallback está funcionando — `docs/AI_PROVIDER_MANAGER.md`);
- [ ] Telegram respondendo a `/start`;
- [ ] Cadastro funcionando (`/cadastro` completo);
- [ ] Login funcionando (`/recuperar` + `/entrar`);
- [ ] Missão pequena criada (uma frase livre, uma única fonte, com confirmação);
- [ ] Uma coleta real pequena executada (`docker compose logs collection_worker` mostra o `CollectionRun` da missão concluindo);
- [ ] Persistência da coleta confirmada (uma `PriceObservation`/`Offer` nova aparece no banco para essa missão);
- [ ] Classificação de relevância executada (`ai_provider_attempt` com `ai_purpose=classify_offer_relevance` nos logs, `ai_outcome=succeeded` em pelo menos uma tentativa);
- [ ] Alerta recebido no Telegram quando a missão tiver alvo de preço atingido e a oferta for `MATCH` (opcional nesta validação — depende do alvo escolhido);
- [ ] Nenhum erro crítico (`level=ERROR`) inesperado nos logs de nenhum serviço.

## 14. Persistência

O que precisa sobreviver a restart de container, `docker compose down`,
reboot do servidor e atualização da aplicação: **o conteúdo do PostgreSQL**,
guardado no volume nomeado `aishoppingagent_postgres_data` (declarado em
`compose.yaml`, montado em `/var/lib/postgresql` dentro do container
`database`). Métricas do Prometheus ficam em `aishoppingagent_prometheus_data`
— úteis para histórico, mas não são dado de negócio; perdê-las não afeta a
aplicação. Traces do Jaeger são voláteis por design (`docs/OPERATIONS.md`) e
não sobrevivem a um restart do container `jaeger`.

### `docker compose down` × `docker compose down -v`

- **`docker compose down`** para e remove os containers e a rede do projeto,
  mas **preserva os volumes** — o banco continua intacto e volta a existir
  na próxima `docker compose up -d`.
- **`docker compose down -v`** faz o mesmo, **e além disso apaga os
  volumes** — incluindo `aishoppingagent_postgres_data`. Isso **destrói
  permanentemente todos os dados de produção** (usuários, missões, ofertas,
  observações de preço, eventos, sessões).

> ⚠️ **Nunca execute `docker compose down -v` em produção sem intenção
> explícita de apagar os dados e sem um backup restaurado e validado em mãos
> (seção 16).** Não existe undo depois desse comando.

## 15. Inicialização após reboot

- O Docker Engine está habilitado no boot (`sudo systemctl enable docker`,
  seção 3) — o daemon volta sozinho depois de reiniciar o Ubuntu.
- **Desde a TASK-066 (`v1.0.2`), os sete serviços do `compose.yaml` têm
  `restart: unless-stopped`** — todos voltam a subir sozinhos quando o
  Docker reinicia (a menos que tenham sido parados manualmente antes do
  reboot). Antes da TASK-066, só `collection_worker` e `telegram_notifier`
  tinham essa política; a auditoria da TASK-066 encontrou que isso já
  tornava o auto-restart deles parcialmente inútil, porque ambos dependem
  de `database`, que não voltava sozinho — a política ficou uniforme para
  os sete.
- Mesmo assim, `docker compose up -d` continua sendo a forma recomendada
  de confirmar/completar a subida depois de um reboot (ver abaixo) — o
  restart automático do Docker Engine não substitui a verificação manual
  do estado do stack.

Depois de reiniciar o servidor:

```bash
sudo systemctl status docker --no-pager
cd AIShoppingAgent
docker compose ps
docker compose up -d
docker compose ps
```

Rodar `docker compose up -d` sempre que precisar é seguro — ele só cria/inicia
o que ainda não está no ar, sem afetar os containers já rodando nem os
volumes.

### Driver de vídeo desabilitado (2026-08-12)

O servidor tem uma GPU NVIDIA GeForce GT 610, cujo driver `nouveau`
causava travamentos completos do sistema operacional (falha repetida
`failed to create ce channel, -22` em todo boot, confirmada em
`journalctl -b`). Como o servidor roda headless e o acesso remoto (RDP,
via `xrdp`) usa um driver X virtual próprio (`xrdpdev`,
`/etc/X11/xrdp/xorg.conf`, independente de GPU real), `nouveau` e
`nvidiafb` foram desabilitados permanentemente:

```bash
cat /etc/modprobe.d/blacklist-nouveau.conf
# blacklist nouveau
# blacklist nvidiafb
# options nouveau modeset=0
```

Se precisar recriar o servidor do zero (seção 20), recriar esse arquivo
e rodar `sudo update-initramfs -u` antes do primeiro reboot. Um monitor
físico plugado na GPU continua mostrando o console de texto básico
(framebuffer do firmware), mas não terá mais saída de vídeo acelerada —
não é uma regressão de uso real, já que não havia desktop gráfico local
configurado (GDM desabilitado) nem dependência disso no RDP.

### Auto-recuperação do Tailscale Funnel (criado 2026-08-12, reescrito para Windows em 2026-08-21)

Depois de um reboot ou de qualquer reinício do Tailscale/Docker, o
Funnel (que expõe o webhook do Telegram publicamente em
`https://cesar-server.tail7d0ce1.ts.net`) pode reportar "on" localmente
sem estar de fato acessível de fora — só é detectável testando resolução
DNS pública real (não o hostname direto, que usa o atalho do MagicDNS do
Tailscale e mascara o problema) seguida de uma conexão HTTPS real ao IP
resolvido.

O mecanismo original (2026-08-12) era um timer systemd, criado quando o
servidor de produção ainda rodava Linux. A migração para Windows Server
não recriou o equivalente, e isso só foi percebido em 2026-08-21 quando
o mesmo incidente se repetiu (Funnel "on" localmente, mas Telegram
acumulando `pending_update_count` sem conseguir entregar) e ficou sem
recuperação automática por horas. Reescrito como Scheduled Task do
Windows, mesma disciplina do original (checa a cada 5 min, reinicia o
serviço Tailscale e reaplica o Funnel só depois de 2 falhas seguidas,
no máximo 1 restart a cada 10 min):

```powershell
# Registrar (uma vez, como Administrador):
powershell -File scripts\register_funnel_healthcheck_task.ps1

# Rodar manualmente / inspecionar:
powershell -File scripts\funnel_healthcheck.ps1
Get-ScheduledTask -TaskName AIShoppingAgent-FunnelHealthcheck
Get-ScheduledTaskInfo -TaskName AIShoppingAgent-FunnelHealthcheck
Get-Content logs\funnel-healthcheck.log -Tail 50
```

Isso cobre só a recorrência específica desse problema — não substitui
verificar `docker compose ps` e o `getWebhookInfo` do Telegram depois de
qualquer reboot ou incidente.

## 16. Backup

Baseado no procedimento já suportado e documentado em `docs/OPERATIONS.md` —
não há automação de backup no projeto além deste procedimento manual.

```bash
install -d -m 700 backups
umask 077
BACKUP_FILE="backups/postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T database sh -c \
  'pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  > "$BACKUP_FILE"
chmod 600 "$BACKUP_FILE"
test -s "$BACKUP_FILE"
docker compose exec -T database pg_restore --list < "$BACKUP_FILE" >/dev/null
stat -c '%a %n' "$BACKUP_FILE"
```

Resultado esperado: arquivo não vazio, modo `600`. Guarde em local
controlado — cópia externa exige criptografia, retenção e controle de
acesso definidos pelo operador (fora do escopo da V1).

### Teste de restauração (sempre em banco descartável, nunca sobre o ativo)

```bash
docker compose exec -T database sh -c \
  'dropdb --if-exists --username="$POSTGRES_USER" aishoppingagent_restore_validation'
docker compose exec -T database sh -c \
  'createdb --username="$POSTGRES_USER" aishoppingagent_restore_validation'
docker compose exec -T database sh -c \
  'pg_restore --exit-on-error --no-owner --no-privileges --username="$POSTGRES_USER" --dbname=aishoppingagent_restore_validation' \
  < "$BACKUP_FILE"
docker compose exec -T database sh -c \
  'psql --username="$POSTGRES_USER" --dbname=aishoppingagent_restore_validation --set=ON_ERROR_STOP=1 --command="SELECT version_num FROM alembic_version;"'
docker compose exec -T database sh -c \
  'dropdb --username="$POSTGRES_USER" aishoppingagent_restore_validation'
```

Backup não testado não é considerado validado. Detalhes completos, incluindo
o que comparar entre banco original e restaurado sem expor dado pessoal, em
`docs/OPERATIONS.md`.

## 17. Atualização da produção

Nunca automatize esta sequência na V1 e nunca use `git pull` cego em
produção (`docs/OPERATIONS.md`):

1. registre o commit/tag atualmente em produção (`git rev-parse HEAD`) e
   identifique a release/tag candidata;
2. gere e valide um backup operacional (seção 16) **antes** de tocar em
   qualquer coisa;
3. `git fetch --tags --prune`;
4. `git checkout --detach <nova-tag>`, confirme `git status --short --branch`
   limpo e o commit esperado (`git rev-parse HEAD` == `git rev-list -n1 <nova-tag>`);
5. leia as migrations novas entre a revisão atual e a candidata
   (`backend/migrations/versions/`, mensagens de cada revisão) — confira se
   alguma é destrutiva ou exige atenção manual;
6. `docker compose config --quiet && docker compose build --pull`;
7. `docker compose run --rm api python -m alembic -c alembic.ini upgrade head`;
8. `docker compose up -d` (recria os serviços com a imagem nova);
9. health checks completos (seção 12);
10. smoke test da seção 13, sem carga alta.

**Nunca faça downgrade destrutivo de banco como parte de uma atualização.**
Se a versão candidata não for compatível com o schema atual, pare e trate
como um rollback avaliado manualmente (`docs/OPERATIONS.md`, seção
"Rollback") — não improvise.

## 18. Logs e diagnóstico

### Logs da aplicação

```bash
docker compose logs --since=10m --tail=200
docker compose logs --since=10m --tail=200 api
docker compose logs --since=10m --tail=200 database
docker compose logs --since=10m --tail=200 collection_worker
docker compose logs --since=10m --tail=200 telegram_notifier
```

Os logs são JSON estruturado em `stdout` (`docs/LOGGING.md`); não copie logs
completos para canais públicos, mesmo sanitizados.

### Recursos do Ubuntu

```bash
top -b -n1 | head -20
free -h
df -h
```

### Docker

```bash
docker compose ps
docker system df
docker volume ls
docker images
```

## 19. Segurança

Checklist específica desta arquitetura, cobrindo só o que o projeto
implementa hoje:

- [ ] `.env` fora do Git (`.gitignore`: `.env`, `.env.*`, exceto `.env.example`);
- [ ] `.secrets/` fora do Git (`.gitignore`: `.secrets/`, `secrets/`);
- [ ] Permissões `700` em `.secrets/` e `600` em cada arquivo dentro dele, `600` em `.env`;
- [ ] Nenhuma chave em nenhuma documentação (auditoria do histórico completo do Git confirmou isso — nenhum secret real jamais foi commitado);
- [ ] Nenhuma chave gravada desnecessariamente em comandos (nunca `echo`/`cat` de secret, nunca secret como argumento de linha de comando — `docs/SECRETS.md`);
- [ ] PostgreSQL não exposto publicamente (`POSTGRES_BIND_ADDRESS=127.0.0.1`);
- [ ] Prometheus e Jaeger não expostos publicamente (`PROMETHEUS_BIND_ADDRESS`/`JAEGER_BIND_ADDRESS=127.0.0.1`);
- [ ] Somente as portas realmente necessárias abertas — hoje isso é só `22/tcp` (SSH) e, quando a infraestrutura de domínio/TLS existir, `443`/`80`; `8000`, `5432`, `9090`, `16686`, `13133` continuam em loopback (`docs/OPERATIONS.md`, tabela de portas);
- [ ] Firewall do host restringindo acesso — **decisão de infraestrutura ainda não definida neste projeto**: configure conforme a política do seu provedor (ex.: `ufw`), liberando só SSH e, futuramente, `443`/`80`; este documento não assume uma ferramenta específica;
- [ ] SSH com chave (não senha), acesso restrito a operadores autorizados;
- [ ] Nenhuma senha padrão (o utilitário de secrets gera valores aleatórios; nenhum valor de exemplo deste documento deve ir para produção);
- [ ] Grupo `docker` limitado aos operadores autorizados (equivale a acesso privilegiado ao host — seção 3);
- [ ] Backups com permissão `600`, fora de local público (seção 16);
- [ ] Logs sem secrets (a aplicação já sanitiza por design — `docs/LOGGING.md` — mas revise antes de compartilhar qualquer trecho).

**Explicitamente marcado como dependente de decisão futura, não incluído
neste documento:** TLS, reverse proxy e regras de firewall específicas. O
projeto não define essa infraestrutura hoje (`docs/OPERATIONS.md`: "não há
domínio, TLS ou reverse proxy permanentes"); quando a decisão for tomada,
este documento deve ser atualizado com o procedimento real usado.

## 20. Se eu precisar recriar o servidor do zero

1. Instalar o Ubuntu Server (seção 2);
2. instalar Git e Docker (seção 3);
3. clonar o repositório (seção 4) — **nunca copiar a pasta de outra máquina**;
4. `git checkout --detach <tag-da-release-em-produção>`;
5. recriar `.env` a partir de `.env.example` (seção 5) — **novo, criado no servidor**;
6. recriar `.secrets/` com `manage_secrets.py init` ou manualmente (seção 6) — **novo, criado no servidor**;
7. restaurar o PostgreSQL a partir do backup mais recente validado (seção 16) — nunca do banco de desenvolvimento;
8. aplicar as migrations Alembic compatíveis com a versão do backup e do código (seção 8/17);
9. subir os serviços (`docker compose up -d`, seção 11);
10. executar os health checks (seção 12);
11. validar Telegram, `collection_worker`, `telegram_notifier` e API (seção 13).

Resumo do princípio: **o código vem do Git; os dados vêm do backup; os
secrets vêm da configuração criada diretamente no novo servidor; nada vem do
computador de desenvolvimento.**

## 21. Checklist final do deploy

### Preparação do servidor

- [ ] Ubuntu Server atualizado (`apt-get update && apt-get upgrade`);
- [ ] Timezone configurada;
- [ ] Git instalado;
- [ ] Docker Engine + Compose Plugin instalados pelo canal oficial;
- [ ] Docker habilitado no boot;
- [ ] Usuário operacional no grupo `docker`;
- [ ] `git --version`, `docker --version`, `docker compose version`, `docker info` validados.

### Código

- [ ] Repositório clonado direto do `origin` (nunca copiado de outra máquina);
- [ ] `git fetch --tags --prune` executado;
- [ ] `git checkout --detach v1.0.1` (ou a tag vigente);
- [ ] `git rev-parse HEAD` confere com `git rev-list -n1 <tag>`;
- [ ] `git status --short --branch` limpo.

### Configuração e secrets

- [ ] `.env` criado a partir de `.env.example`, `chmod 600`;
- [ ] `AISHOPPING_ENVIRONMENT=production` definido;
- [ ] `AISHOPPING_AUTH_PUBLIC_BASE_URL` apontando para a URL HTTPS real (ou explicitamente marcado como pendente);
- [ ] `.secrets/` criado com os 6 arquivos exigidos, `700`/`600`;
- [ ] `manage_secrets.py check` aprovado;
- [ ] Nenhuma chave OpenAI/Anthropic/Grok configurada (não usadas pelo código).

### Banco de dados

- [ ] `database` subido e `healthy`;
- [ ] Migrations aplicadas (`alembic upgrade head`);
- [ ] `alembic current` confere com o topo de `backend/migrations/versions/`;
- [ ] Nenhum dado de desenvolvimento presente (banco nasceu vazio).

### Serviços

- [ ] `docker compose build --pull` executado;
- [ ] `docker compose up -d` — sete serviços no ar;
- [ ] `docker compose ps` mostra todos `healthy` onde aplicável.

### Verificação

- [ ] `/health` e `/ready` respondendo `200`;
- [ ] PostgreSQL acessível via `pg_isready`;
- [ ] Prometheus, Jaeger e o health do Collector respondendo em loopback;
- [ ] `collection_worker` e `telegram_notifier` `healthy`, sem erro nos logs recentes.

### Telegram

- [ ] Webhook registrado com URL HTTPS pública real (ou explicitamente pendente de domínio/TLS);
- [ ] `--action info` confirma a URL sem exibir credenciais;
- [ ] Comandos registrados (`register_telegram_commands`);
- [ ] `/start` responde;
- [ ] `/cadastro` completo;
- [ ] `/recuperar` + `/entrar` funcionando;
- [ ] Promoção do primeiro DEV feita e confirmada (seção 9), se aplicável.

### Smoke test

- [ ] Missão pequena criada por texto livre, com confirmação;
- [ ] Coleta real pequena executada e persistida;
- [ ] Classificação de relevância bem-sucedida em pelo menos uma oferta;
- [ ] Fallback Groq observado funcionando quando o Flash falhar (não forçado artificialmente);
- [ ] Nenhum erro crítico inesperado nos logs.

### Persistência e segurança

- [ ] Volume `aishoppingagent_postgres_data` confirmado (`docker volume ls`);
- [ ] Diferença entre `docker compose down` e `docker compose down -v` compreendida pela equipe operacional;
- [ ] Backup inicial gerado e restauração testada em banco descartável;
- [ ] Checklist de segurança da seção 19 revisado;
- [ ] Portas administrativas confirmadas em loopback (`docker compose ps`, `ss -tlnp`).

---

Dúvidas operacionais do dia a dia (rotina, rotação de credenciais,
diagnóstico avançado, rollback): `docs/OPERATIONS.md`. Gestão detalhada de
secrets: `docs/SECRETS.md`. Migrations: `docs/MIGRATIONS.md`.
