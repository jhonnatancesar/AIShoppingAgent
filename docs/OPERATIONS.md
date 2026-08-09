# Runbook operacional da V1

Este documento é o ponto de entrada para operar o AIShoppingAgent em um único
Ubuntu Server headless. Ele descreve o comportamento existente e procedimentos
manuais reproduzíveis. Não é uma declaração de prontidão para produção nem uma
estratégia completa de disaster recovery.

## Estado e limites atuais

Antes de disponibilizar a instância a usuários, considere estes bloqueios:

- não existe scheduler/orquestrador que execute automaticamente as coletas das
  missões; os Store Providers funcionam isoladamente, mas uma missão ativa não
  pesquisa preços sozinha;
- não há suíte permanente de integração/E2E das TASKs 052 e 053;
- não há release final da TASK-054, CI/CD ou deploy automático;
- não há domínio, TLS ou reverse proxy permanentes;
- Prometheus apenas avalia regras. Sem Alertmanager, não existe entrega externa
  de alertas operacionais;
- o backup deste runbook é manual e não define RPO, RTO, replicação, cópia
  externa, automação ou recuperação completa de desastre.

`cloudflared` é permitido somente para desenvolvimento e validação temporária.
Uma URL efêmera não é endpoint definitivo de produção. A implantação real
exige domínio estável, TLS válido, reverse proxy adequado e endpoint HTTPS
permanente para o webhook e os formulários de autenticação.

## Topologia e portas

O Compose publica portas administrativas somente no loopback por padrão. A
comunicação entre serviços usa a rede interna do Compose.

| Porta | Componente | Exposição permitida |
| --- | --- | --- |
| `22/tcp` | SSH | Pública somente com origem restrita, chaves e hardening do host. |
| `443/tcp` | HTTPS futuro | Pública quando domínio, TLS e reverse proxy forem implantados. |
| `80/tcp` | HTTP futuro | Somente se necessário para redirect/ACME; não serve a aplicação diretamente. |
| `8000/tcp` | API | Loopback/rede interna; nunca abrir diretamente para a Internet. |
| `5432/tcp` | PostgreSQL | Loopback/rede interna necessária ao stack; nunca pública. |
| `9090/tcp` | Prometheus | Loopback/rede administrativa; nunca pública. |
| `16686/tcp` | Jaeger | Loopback/rede administrativa; nunca pública. |
| `13133/tcp` | Health do Collector | Loopback; nunca público. |
| `9464/tcp` | Métricas do worker | Somente rede interna do Compose; não é publicada no host. |

Mantenha `API_BIND_ADDRESS`, `POSTGRES_BIND_ADDRESS`,
`PROMETHEUS_BIND_ADDRESS` e `JAEGER_BIND_ADDRESS` como `127.0.0.1`. Não use
`0.0.0.0` para contornar acesso administrativo. Para consultar Prometheus e
Jaeger remotamente, prefira um canal protegido, por exemplo:

```bash
ssh -L 9090:127.0.0.1:9090 -L 16686:127.0.0.1:16686 operador@servidor
```

Depois, abra `http://127.0.0.1:9090` e `http://127.0.0.1:16686` na máquina do
operador. Tailscale ou solução equivalente também pode fornecer uma rede
administrativa privada, mas não faz parte do stack da V1.

## Pré-requisitos do Ubuntu Server

- Ubuntu Server x86-64 suportado e atualizado;
- Git;
- Docker Engine estável e plugin Docker Compose;
- Python oficial do host somente para os utilitários locais de secrets;
- espaço disponível para imagens, volume PostgreSQL, Prometheus e backups;
- relógio sincronizado e acesso de saída HTTPS para Telegram, IA, lojas e
  download das imagens;
- usuário operacional dedicado com acesso mínimo necessário.

Valide o ambiente:

```bash
git --version
docker --version
docker compose version
python3 --version
docker info
```

Use a documentação oficial do Docker para instalar o Engine e o plugin Compose.
Participar do grupo `docker` equivale, na prática, a acesso privilegiado ao
host; limite esse grupo aos operadores autorizados.

## Preparação do checkout

Não execute produção a partir de branch mutável sem revisão. Quando a
TASK-054 produzir uma release, prefira a tag correspondente; até lá, use apenas
um commit explicitamente revisado.

```bash
git clone https://github.com/jhonnatancesar/AIShoppingAgent.git
cd AIShoppingAgent
git fetch --tags --prune
git checkout --detach <tag-ou-commit-revisado>
git status --short --branch
```

O working tree deve estar limpo. Nunca use `git reset --hard`, remova volumes
ou descarte arquivos do servidor como parte de uma atualização automática.

## Configuração e secrets

Crie a configuração não sensível e mantenha os binds privados:

```bash
cp .env.example .env
chmod 600 .env
```

Em uma instalação nova, prepare os seis secret files sem colocá-los na linha de
comando, stdout, histórico do shell, Git ou tickets:

```bash
python3 -m backend.scripts.manage_secrets init
python3 -m backend.scripts.manage_secrets check
chmod 700 .secrets
chmod 600 .secrets/*
stat -c '%a %n' .secrets .secrets/*
```

Em produção, defina `AISHOPPING_ENVIRONMENT=production` e uma URL HTTPS estável
em `AISHOPPING_AUTH_PUBLIC_BASE_URL`. Enquanto domínio/TLS permanente não
existir, a instância não deve ser apresentada como implantação definitiva.

Produção aceita secrets exclusivamente por `*_FILE`. Cada serviço monta apenas
os arquivos necessários em `/run/secrets`; conflito, valor vazio, arquivo
ilegível ou valor direto falha fechado. O procedimento completo de rotação está
em [SECRETS.md](SECRETS.md).

## Build, migration e primeira inicialização

Primeiro valide a configuração, construa a imagem e inicie somente o banco:

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d database
docker compose ps database
```

Espere o PostgreSQL ficar `healthy`, consulte a revisão e aplique as migrations
antes de liberar a aplicação:

```bash
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose up -d
docker compose ps
```

Uma migration compartilhada nunca é reescrita. Revise seu código e impacto
antes de aplicá-la. Não execute downgrade para “testar” em banco com dados.

## Verificação funcional e operacional

No próprio servidor:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
curl --fail --silent --show-error http://127.0.0.1:8000/ready
curl --fail --silent --show-error http://127.0.0.1:8000/metrics >/dev/null
curl --fail --silent --show-error http://127.0.0.1:9090/-/ready >/dev/null
curl --fail --silent --show-error http://127.0.0.1:16686/api/services >/dev/null
curl --fail --silent --show-error http://127.0.0.1:13133/ >/dev/null
docker compose exec -T database sh -c \
  'pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

- `/health=200` prova apenas que o processo da API está vivo;
- `/ready=200` inclui uma consulta mínima real ao PostgreSQL;
- `/ready=503` com `/health=200` indica API viva, mas banco indisponível;
- Prometheus, Jaeger e Collector não determinam readiness funcional da API.

Consulte logs com janela e volume limitados:

```bash
docker compose logs --since=10m --tail=200 api telegram_notifier database
```

Não copie logs completos para canais públicos. Mesmo com sanitização na
aplicação, revise qualquer trecho antes de compartilhá-lo.

## Telegram

O webhook definitivo precisa de URL HTTPS estável. Depois de configurar essa
infraestrutura externa, registre o endpoint e os comandos:

```bash
docker compose run --rm api python -m scripts.register_telegram_webhook \
  --action set --url https://dominio.exemplo/telegram/webhook
docker compose run --rm api python -m scripts.register_telegram_webhook \
  --action info
docker compose run --rm api python -m scripts.register_telegram_commands
```

Não coloque token ou segredo do webhook na URL. `--action info` não deve exibir
credenciais. Para desenvolvimento/validação, uma URL temporária do cloudflared
pode ser registrada e removida ao final:

```bash
docker compose run --rm api python -m scripts.register_telegram_webhook \
  --action delete
```

## Operação rotineira

### Iniciar e parar

```bash
docker compose up -d
docker compose ps
docker compose stop
docker compose start
```

`docker compose down` remove containers e rede, mas preserva volumes se não
receber `--volumes`. Nunca acrescente `--volumes` em ambiente com dados sem uma
decisão destrutiva explícita e backup restaurado/testado.

### Restart controlado

```bash
docker compose restart api telegram_notifier
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8000/ready
```

Após reboot do host, confirme primeiro Docker e PostgreSQL, depois suba o stack
com `docker compose up -d`. A V1 não instala automaticamente uma unidade
systemd do projeto.

### Atualização

1. registre o commit/tag atual e confirme que o working tree está limpo;
2. gere e valide um backup operacional conforme a seção seguinte;
3. leia as migrations entre a versão atual e a candidata;
4. obtenha a versão candidata com `git fetch --tags --prune`;
5. faça checkout somente do commit/tag revisado;
6. execute `docker compose config --quiet` e `docker compose build --pull`;
7. aplique `alembic upgrade head`;
8. recrie os serviços com `docker compose up -d`;
9. valide health, readiness, logs, worker e observabilidade.

Não automatize essa sequência na V1 e não use `git pull` cego em produção.

## Backup operacional manual

O backup contém dados potencialmente pessoais. Crie um diretório privado,
force `umask 077` e nunca inclua secret no nome, comando ou saída:

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

O resultado esperado é arquivo não vazio com modo `600`. Guarde-o em local
controlado; copiar para armazenamento externo exige criptografia, retenção e
controle de acesso definidos pelo operador, que permanecem fora desta V1.

### Restauração validada em banco limpo

Backup não testado não é considerado validado. Faça a prova em ambiente
descartável ou em um banco de validação vazio, nunca sobre o banco ativo:

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
```

Compare no banco original e no restaurado as contagens das tabelas críticas e
confirme a leitura de um registro sintético conhecido. Não imprima nomes,
e-mails, Telegram IDs, tokens, hashes ou textos reais durante a conferência.
Depois da validação e somente quando tiver certeza de que esse é o banco
descartável:

```bash
docker compose exec -T database sh -c \
  'dropdb --username="$POSTGRES_USER" aishoppingagent_restore_validation'
```

Esse processo comprova backup operacional manual e restauração naquele teste.
Ele não oferece disaster recovery completo, replicação, point-in-time recovery,
automação, cópia off-site nem garantias de RPO/RTO.

## Rollback

### Rollback de código

Voltar para commit/tag/container anterior só é permitido quando essa aplicação
é compatível com o schema já aplicado. Antes de trocar:

1. identifique as revisions Alembic conhecidas pelas duas versões;
2. revise alterações de colunas, constraints, enums e contratos;
3. confirme que a aplicação anterior aceita o schema atual;
4. mantenha o backup operacional validado;
5. somente então reconstrua e reinicie a versão anterior.

Se a compatibilidade não puder ser demonstrada, pare. `git checkout` seguido de
restart não resolve rollback de forma geral e pode corromper comportamento ou
impedir a inicialização.

### Rollback de schema

Nunca execute downgrade Alembic destrutivo automaticamente. Cada downgrade
deve ser avaliado manualmente, principalmente quando remove ou transforma
dados, enums, constraints, FKs ou estruturas append-only. Se a aplicação
anterior não aceitar o schema atual, interrompa o procedimento e exija
intervenção técnica manual; não improvise downgrade no banco ativo.

## Diagnóstico e recuperação básica

| Sintoma | Verificações | Ação segura inicial |
| --- | --- | --- |
| `/health` falha | `docker compose ps api`, logs limitados | Reiniciar apenas a API e investigar crash/configuração. |
| `/health=200`, `/ready=503` | health do banco, espaço em disco, logs | Recuperar PostgreSQL; não culpar observabilidade. |
| Worker sem métricas | `docker compose ps telegram_notifier`, logs | Reiniciar worker após encerrar transações e investigar secret/rede. |
| Dead letters | regra Prometheus e histórico append-only | Identificar erro permanente; não alterar/apagar tentativas. |
| Circuito aberto | métricas por componente/evento | Corrigir dependência e aguardar/observar a sonda half-open. |
| Cota de IA | telemetria sanitizada do provider | USER aguarda renovação da cota gratuita; ADMIN/DEV usa apenas fallback configurado. |
| Telegram não entrega | webhook info, API/worker, token e HTTPS | Validar URL/secret sem imprimi-los; não repetir envio ambíguo cegamente. |
| Disco pressionado | `docker system df`, volumes e filesystem | Não apagar volume; identificar imagens/logs seguros antes de qualquer limpeza. |

Prometheus mostra estado `pending`/`firing`, mas não notifica externamente.
Jaeger é volátil e limitado; restart perde traces. Logs Docker rotacionam em
cinco arquivos de 10 MB. Consulte [OBSERVABILITY.md](OBSERVABILITY.md) e
[RESILIENCE.md](RESILIENCE.md).

## Segurança, privacidade e manutenção

- Rotação de credenciais: [SECRETS.md](SECRETS.md).
- Incidente de vazamento: interrompa a operação afetada, revogue/rotacione no
  provedor, execute Gitleaks e registre somente metadados sanitizados em
  [SECURITY_INCIDENT_LOG.md](SECURITY_INCIDENT_LOG.md).
- Limpeza manual de tokens/sessões antigos e desidentificação controlada:
  [PRIVACY.md](PRIVACY.md). Desidentificação é operação sensível, confirmada e
  irreversível quanto aos identificadores removidos; não a use como limpeza
  rotineira.
- Autenticação: [AUTHENTICATION.md](AUTHENTICATION.md).
- Migrações: [MIGRATIONS.md](MIGRATIONS.md).

## Checklist rotineiro

### Diário

- containers esperados em execução/healthy;
- `/health` e `/ready` respondendo corretamente;
- worker expondo métricas internamente;
- regras Prometheus sem estado inesperado;
- ausência de dead letters/circuitos persistentemente abertos;
- espaço de disco e volume PostgreSQL sob controle.

### Antes de atualização

- commit/tag atual registrado e checkout limpo;
- release candidata revisada;
- migrations avaliadas;
- backup restrito, não vazio e restaurado com sucesso em banco limpo;
- compatibilidade de rollback de código conhecida;
- janela operacional e operador responsável definidos.

### Depois de atualização

- revision Alembic esperada;
- API e worker saudáveis;
- PostgreSQL acessível;
- Prometheus coletando targets;
- traces funcionais chegando ao Jaeger;
- webhook/comandos do Telegram conferidos quando afetados;
- logs revisados sem dados sensíveis;
- commit/tag implantado registrado fora de qualquer secret.
