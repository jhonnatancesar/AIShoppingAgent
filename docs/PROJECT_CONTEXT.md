# Project Context

## Estado

Fase: missões e coleta em implementação. TASK-019 concluída em 2026-08-02.

## O que existe

- Estrutura de diretórios do projeto.
- Esqueleto mínimo da aplicação FastAPI em `backend/app/`, com dependências declaradas em `backend/requirements.txt`.
- Gestão de configuração tipada em `backend/app/core/config.py`, com variáveis de ambiente, exemplo local e arquivo `.env` ignorado pelo Git.
- Inventário de dependências e procedimento de preparação de novas máquinas em `docs/DEPENDENCIES.md`.
- Política de uso da versão estável mais recente do Python; Python 3.14.3 é a versão atualmente validada.
- Ambiente Docker Compose com contêineres FastAPI e PostgreSQL 18, volume persistente e configuração local protegida.
- Qualidade de código configurada com Ruff para lint, imports, modernização Python 3.14 e formatação.
- Testes base configurados com Pytest e cobertura mínima de 90% para o pacote da aplicação.
- Módulo de saúde com endpoint de vivacidade `GET /health`, integrado ao healthcheck do contêiner da API.
- Convenções HTTP e OpenAPI definidas em `docs/API_CONVENTIONS.md`, com endpoints de negócio versionados sob `/api/v1`.
- Logging JSON em `stdout`, com nível configurável e eventos HTTP sem captura de dados sensíveis.
- Pipeline local único para dependências, lint, formatação, testes, cobertura e validação do Docker Compose.
- Contrato de ciclo de vida de missões com estados, comandos, transições, invariantes e auditoria mínima definidos antes do modelo persistente.
- Modelo relacional PostgreSQL do MVP definido com entidades, tipos, relações, restrições, índices e regras de preservação histórica.
- SQLAlchemy, Psycopg e Alembic configurados com conexão tipada, metadata compartilhada, sessões explícitas e baseline reversível.
- Entidade `User` persistente com UUID, nome, papel tipado, ativação lógica, timestamps e restrições de integridade.
- Entidade `Product` persistente com identidade canônica, nome, marca e modelo opcionais, timestamps e restrições de integridade.
- Entidades `Store` e `Offer` persistentes com origem nacional normalizada, relações restritivas e identidade estável por loja.
- Entidade `AuditEntry` persistente e append-only, com JSONB sanitizado, referência opcional de ator e índices históricos.
- Entidade `Mission` persistente com proprietário, seis estados, prazo opcional, versão concorrente e índices operacionais.
- Documentos de visão, arquitetura, dados, módulos-alvo, escopo do MVP, backlog, itens fora de escopo, governança de decisões e workflow permanente de execução.
- ADRs, RFCs e 56 tarefas planejadas.

## O que não existe

Além de `users`, `products`, `stores`, `offers`, `audit_entries` e `missions`, não há outras tabelas de domínio nem repositórios implementados. Também não existem critérios ou transições persistentes, autenticação, autorização, APIs de negócio, instrumentação automática de auditoria, automações, Store Providers, integrações externas, testes de integração ou ponta a ponta nem credenciais reais configuradas.

## Invariantes

- Monólito modular.
- PostgreSQL no MVP.
- Preços são históricos, não um valor substituível.
- IA é acessada somente via AI Provider Manager.
- Funcionalidades devem seguir a tarefa explicitamente solicitada.
- A V1 é focada em lojas nacionais; o Marketplace Module é uma evolução futura, fora do escopo atual.
- `docs/MVP.md` é a definição completa do escopo da V1; `docs/OUT_OF_SCOPE.md` previne aumento de escopo e `docs/BACKLOG.md` registra evoluções futuras.
- `docs/DECISION_LOG.md` registra decisões arquiteturais e funcionais; toda nova funcionalidade deve ser analisada e classificada antes de qualquer implementação.
- A TASK-055 planeja o primeiro Store Provider nacional, usando a Kabum como prova de conceito da arquitetura de coleta.
- O workflow oficial de execução de TASKs está definido em `AGENTS.md` e deve ser seguido automaticamente em todas as conversas futuras.
- Antes de iniciar uma TASK em uma máquina nova, as dependências devem ser comparadas com `docs/DEPENDENCIES.md`; instalações exigem autorização explícita.
- O projeto acompanha a versão estável mais recente do Python e exige nova validação de compatibilidade a cada atualização.
- O ciclo de vida definido em `docs/MISSION_SYSTEM.md` é entrada obrigatória para o modelo de dados da TASK-010; sua execução será implementada apenas na TASK-021.
- `docs/DATABASE.md` é o contrato do modelo relacional; a TASK-011 deve preparar sua evolução por migrações antes da implementação das entidades.
- Toda alteração persistente deve usar a metadata compartilhada e receber uma revisão Alembic revisada; credenciais de banco não possuem padrão inseguro.
- Usuários aceitam somente os papéis `USER`, `ADMIN` e `DEV`; `PLUS` permanece fora do MVP, e `is_active` não substitui as regras futuras de autenticação e autorização.
- Produtos são identidades canônicas independentes de loja; nomes não são únicos e nenhuma deduplicação automática ocorre sem evidência suficiente.
- Ofertas identificam anúncios estáveis por loja e nunca armazenam preço ou disponibilidade corrente; lojas persistentes não implementam providers de coleta.
- Auditoria é append-only; correções geram novas entradas, e metadata nunca contém segredos ou dados pessoais desnecessários.
- Missões nascem em `draft`; alterações de estado e de `state_version` só serão implementadas atomicamente na TASK-021.
