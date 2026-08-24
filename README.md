# AIShoppingAgent

Agente de compras que monitora preços em várias lojas e avisa você no
Telegram na hora certa de comprar.

> **Observação de segurança:** use somente a instalação oficial do Python da
> máquina. Não instale dependências nem rode o projeto com runtimes internos
> do Codex, plugins ou caches. Um alerta do antivírus deve interromper a
> execução; não restaure o objeto nem crie exceções automaticamente.
> Consulte o histórico em
> [Log de incidentes de segurança](docs/internal/security-incident-log.md).

## O que é

Você descreve, em linguagem natural pelo Telegram, o produto que quer
comprar. O AIShoppingAgent interpreta o pedido com IA, cria uma missão de
acompanhamento e monitora continuamente o preço nas lojas que você
selecionar. Quando o preço cai ou atinge o valor-alvo definido, você recebe
um alerta com o link direto para a oferta — a compra em si é sempre feita
por você, direto na loja.

## Como funciona

1. Você descreve o produto pelo bot do Telegram.
2. A IA interpreta o pedido e cria a missão.
3. O `collection_worker` monitora continuamente as lojas selecionadas.
4. Cada preço coletado é preservado no histórico (nada é sobrescrito).
5. Ao detectar queda de preço ou preço-alvo atingido, o `telegram_notifier`
   envia o alerta.
6. Você decide comprar, direto na loja.

## Principais recursos

- Criação de missão por linguagem natural, sem formulário.
- Monitoramento contínuo, sem prazo de expiração por padrão.
- Alertas de queda de preço e de preço-alvo atingido, configuráveis
  separadamente.
- Histórico completo de preço, append-only.
- Exibição de preço à vista e, quando a loja informa, condições de
  parcelamento — nunca calculado.
- Autenticação por sessão, papéis `USER`/`ADMIN`/`DEV` e isolamento por
  proprietário.
- Observabilidade própria (logs estruturados, métricas Prometheus, traces
  Jaeger).

## Arquitetura resumida

Monólito modular em FastAPI, com PostgreSQL como fonte de verdade
transacional. Sete serviços via Docker Compose: `api` (HTTP + webhook do
Telegram), `collection_worker` (coleta agendada via Playwright),
`telegram_notifier` (entrega de alertas), `database` (PostgreSQL),
`otel-collector`, `prometheus` e `jaeger` (observabilidade). Todo acesso a
provedores de IA passa pelo `AIProviderManager` — nenhum módulo fala
diretamente com Gemini, Groq ou OpenRouter. Detalhes completos em
[Arquitetura → Visão geral](docs/architecture/overview.md).

## Plataforma atual

A produção roda em **Windows Server**, com Docker Desktop (WSL2) e
Tailscale Funnel para o HTTPS público do webhook do Telegram. Ubuntu Server
foi a plataforma original e permanece documentado como instalação legada.
Veja [Instalação → Windows Server](docs/installation/windows-server.md) e
[Instalação → Linux (legado)](docs/installation/linux.md).

## Instalação rápida

Ambiente local, para desenvolvimento:

```powershell
Copy-Item .env.example .env
python -m backend.scripts.manage_secrets init
python -m backend.scripts.manage_secrets check
docker compose up -d database
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose up --build
```

A API fica em `http://localhost:8000`. Detalhes completos em
[Instalação → Docker Compose local](docs/installation/docker.md).

Para produção do zero, siga
[Instalação → Windows Server](docs/installation/windows-server.md).

## Documentação

A documentação técnica completa vive em [`docs/`](docs/):

| Área | Conteúdo |
| --- | --- |
| [`docs/installation/`](docs/installation/) | Instalação (Windows Server, Docker local, Linux legado), configuração, atualização. |
| [`docs/architecture/`](docs/architecture/) | Como cada parte do sistema funciona (missões, ofertas, Telegram, IA, privacidade). |
| [`docs/administration/`](docs/administration/) | Administração manual de usuários e missões. |
| [`docs/operations/`](docs/operations/) | Runbook, containers, backup/restauração, health checks. |
| [`docs/database/`](docs/database/) | Schema, acesso ao PostgreSQL, migrations. |
| [`docs/development/`](docs/development/) | Ambiente de desenvolvimento, testes, convenções. |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Problemas comuns e como resolvê-los. |
| [`docs/releases/`](docs/releases/) | Changelog, checklist de release, índice de versões. |
| [`docs/adr/`](docs/adr/), [`docs/rfc/`](docs/rfc/), [`docs/tasks/`](docs/tasks/), [`docs/internal/`](docs/internal/) | Decisões de arquitetura, propostas, tarefas e contexto interno do projeto. |

A apresentação pública do projeto (site institucional, sem documentação
técnica/administrativa) vive em um repositório próprio, separado deste:
[`jhonnatancesar/AIShoppingAgent-site`](https://github.com/jhonnatancesar/AIShoppingAgent-site),
publicado em `https://jhonnatancesar.github.io/AIShoppingAgent-site/`.

## Administração

Operar o sistema sem depender do bot — containers, PostgreSQL, usuários e
missões — está documentado em [`docs/administration/`](docs/administration/)
e no [Runbook de operação](docs/operations/runbook.md).

## Releases / Downloads

Versões são publicadas como tags Git anotadas. Veja o
[índice de releases](docs/releases/index.md) para a versão atual, o
histórico completo e como atualizar produção para uma nova tag.

## Desenvolvimento

Instale as dependências de desenvolvimento e execute as verificações a
partir da raiz do projeto:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Pipeline completo (inclui varredura de segredos):

```powershell
.\scripts\check.cmd
```

Suíte de integração PostgreSQL isolada:

```powershell
.\scripts\check-integration.cmd
```

Testes exigem cobertura mínima de 90% do pacote `app`. Convenções de API,
ambiente de desenvolvimento, testes e estrutura do projeto estão em
[`docs/development/`](docs/development/).

## Licença

Ainda não definida. Este repositório é privado e não deve ser tratado como
disponível para reuso até que uma licença seja explicitamente escolhida.
