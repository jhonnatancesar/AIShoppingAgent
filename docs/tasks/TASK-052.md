# TASK-052 — Criar testes de integração

Status: Concluída

## Objetivo

Criar uma suíte permanente, reproduzível e fail-closed que integre migrations,
SQLAlchemy, serviços de domínio, transações e concorrência com PostgreSQL 18
real e descartável.

## Infraestrutura aprovada

- `tests/integration` marcado explicitamente no Pytest;
- runner Python multiplataforma usando container, volume, usuário, banco, senha,
  porta loopback e labels exclusivos por execução;
- PostgreSQL 18.4 Alpine fixado por digest revisável;
- readiness com timeout e diagnóstico sanitizado;
- cleanup exato em `finally`, sem prune ou remoção genérica;
- nenhuma leitura de `.env`, secrets, volume ou banco real;
- configuração externa de banco recusada antes de criar recursos.

## Migrations e isolamento

- executar `alembic upgrade head` sem hardcode permanente de revision;
- exigir exatamente um head, conferir o banco nesse head e executar
  `alembic check` para metadata/migrations;
- criar template migrado com os seeds oficiais;
- clonar banco limpo por teste e removê-lo depois, permitindo commits,
  conexões e concorrência reais sem dependência de ordem;
- execução individual e repetida deve produzir o mesmo resultado.

## Fronteiras

São reais: PostgreSQL, migrations, SQLAlchemy, domínio, locks, constraints,
Argon2id, autorização, eventos, consumo, confirmação, persistência e
resiliência dependente do banco. Telegram, Gemini, Groq, Store Providers e
Playwright externo permanecem doubles determinísticos apenas na fronteira;
nenhum serviço de domínio é mockado.

## Fluxos

- schema, metadata, seeds, constraints, FKs e triggers;
- missão, coleta, observações, alertas, eventos e consumo concorrente;
- recomendação, comparação, confirmação e trilha;
- autenticação, autorização, ownership e token single-use;
- replay, rate limit, retry/dead letter e privacidade.

## Pipeline e diagnóstico

- Docker/PostgreSQL/migration/preparação indisponível sempre falha a execução
  solicitada; não existe skip silencioso;
- o pipeline rápido ignora `tests/integration`, enquanto o pipeline oficial
  completo executa obrigatoriamente o runner;
- falhas informam teste, etapa, erro sanitizado, logs limitados do container e
  revision alcançada quando disponível, nunca credenciais ou dados reais.

## Critério de aceite

Suíte completa e teste individual aprovados; repetição determinística aprovada;
recursos interrompidos não contaminam nova execução; pipeline completo aprovado;
documentação e workflow sincronizados. TASK-053 permanece exclusivamente E2E.

## Resultado

- criado `scripts/run_integration_tests.py`, runner fail-closed e
  multiplataforma com PostgreSQL 18.4 fixado por digest;
- cada execução usa container, volume, usuário, senha, banco-template, porta
  loopback e labels exclusivos; cada teste recebe um clone limpo do template;
- migrations chegam dinamicamente ao único head, conferem `alembic_version` e
  executam `alembic check`, preservando os seeds oficiais;
- oito testes permanentes cobrem schema, fluxo de preço/evento, consumo
  concorrente, recomendação/compra, autenticação, autorização, resiliência e
  privacidade em PostgreSQL real;
- a suíte completa passou duas vezes, o teste de schema passou isoladamente e
  execução fora do runner falhou fechado;
- uma falha controlada confirmou diagnóstico sanitizado e remoção exata; após
  sucesso e falha ficaram zero containers e zero volumes da suíte;
- o pipeline oficial passou a executar a integração obrigatoriamente, enquanto
  a etapa rápida de cobertura a exclui explicitamente;
- pipeline final aprovado com 607 testes rápidos, 90,61% de cobertura, 8 testes
  de integração, Ruff, Alembic, Gitleaks e Docker Compose.

Próxima tarefa executável: TASK-053.

