# Changelog

## 2026-08-02 — TASK-014

- Criados os modelos SQLAlchemy `Store` e `Offer`, separando a origem nacional normalizada do anúncio estável de um produto.
- Adicionadas relações obrigatórias para produto e loja com `RESTRICT`, sem exclusão em cascata.
- Protegida a identidade por `(store_id, external_id)` quando informado e por `(store_id, url)`, com índices relacionais adicionais.
- Mantidos preço e disponibilidade fora da oferta para preservar o futuro histórico de observações.
- Adicionada a revisão reversível `20260802_0004` e atualizado o registro central de modelos.
- Detectada e corrigida durante a revisão uma expressão regex que o SQLAlchemy compilava incorretamente no DDL.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 33 testes aprovados e 99,43% de cobertura.

## 2026-08-02 — TASK-013

- Criado o modelo SQLAlchemy `Product` para a identidade canônica independente de loja, oferta e preço.
- Adicionados UUID gerado pela aplicação, nome, marca e modelo opcionais, timestamps UTC e restrições contra textos em branco.
- Centralizado o relógio UTC compartilhado pelos modelos de usuário e produto.
- Adicionada a revisão reversível `20260802_0003` e atualizado o registro central de modelos.
- Definida deduplicação conservadora, sem unicidade por nome nem mesclagem automática sem evidência suficiente.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 25 testes aprovados e 99,28% de cobertura.

## 2026-08-02 — TASK-012

- Criados o modelo SQLAlchemy `User` e o vocabulário tipado `UserRole` com `USER`, `ADMIN` e `DEV`.
- Adicionadas restrições para nome não vazio e papel válido, UUID gerado pela aplicação, ativação lógica e timestamps UTC.
- Criado registro central de modelos para manter a metadata do Alembic sincronizada.
- Adicionada a revisão reversível `20260802_0002` para a tabela `users`, sem credenciais, canais, API ou autorização.
- Validada em PostgreSQL 18 a inserção válida, a rejeição de `PLUS` e nome em branco, o downgrade e o novo upgrade.
- Ampliada a suíte para 20 testes aprovados e 99,17% de cobertura.

## 2026-08-02 — TASK-011

- Instalados e declarados SQLAlchemy 2.0.51, Alembic 1.18.5 e Psycopg 3.3.4 com suporte a Python 3.14 e PostgreSQL 18.
- Criadas configuração tipada da conexão, metadata declarativa única, construção de engine e fábrica de sessões sem conexão global antecipada.
- Configurado o ambiente Alembic e adicionada a baseline vazia `20260802_0001`, sem tabelas de domínio.
- Integradas as migrações à imagem da API e ao pipeline local, com comandos operacionais documentados.
- Validado em PostgreSQL 18 o ciclo upgrade, downgrade e novo upgrade em volume isolado; o volume existente do projeto foi preservado.
- Ampliada a suíte para 14 testes aprovados e 98,96% de cobertura.

## 2026-08-02 — TASK-010

- Definido o esquema relacional PostgreSQL para usuários, missões, critérios, transições, produtos, lojas, ofertas, coletas, preços, eventos e auditoria.
- Especificados tipos, chaves, relações, nulabilidade, unicidade, índices mínimos e regras monetárias e temporais.
- Incorporado o ciclo de vida da TASK-018 com estado atual, versão concorrente e histórico imutável de transições.
- Preservado o histórico de preços por observações anexadas, sem atualização destrutiva ou deduplicação de coletas repetidas.
- Delimitadas migrações, ORM e implementação persistente para as TASKs 011 a 017.

## 2026-08-02 — TASK-018

- Definidos os estados `draft`, `active`, `paused`, `completed`, `cancelled` e `expired` para missões.
- Documentados comandos, transições permitidas, condições e comportamento terminal.
- Estabelecidas invariantes para coleta, missões permanentes, concorrência, preservação de histórico e horários em UTC.
- Definido o registro mínimo e imutável de transições como entrada para a TASK-010, sem antecipar persistência ou implementação.
- Delimitadas as responsabilidades futuras das TASKs 010, 016 e 021.

## 2026-08-01 — TASK-009

- Criado pipeline local executável por `scripts\check.cmd`, compatível com a política de execução atual do Windows.
- Reunidas verificações de dependências, lint, formatação, testes, cobertura e Docker Compose com falha imediata.
- Garantida execução independente do diretório atual e restauração da senha temporária usada apenas para validar o Compose.
- Validado o pipeline completo com 9 testes aprovados e 98,65% de cobertura.

## 2026-08-01 — TASK-008

- Configurado logging JSON em `stdout` para a aplicação e os loggers do Uvicorn.
- Adicionado nível configurável por `AISHOPPING_LOG_LEVEL`.
- Criados eventos HTTP com método, caminho, status e duração, sem query string, corpo, cabeçalhos ou credenciais.
- Adicionados testes do formatador e dos fluxos HTTP de sucesso e falha.
- Validado o evento JSON real no ambiente Docker Compose.

## 2026-08-01 — TASK-007

- Definidas convenções para versionamento, rotas, métodos, códigos HTTP, JSON, erros, coleções e OpenAPI.
- Reservado `/api/v1` para endpoints de negócio e mantidos endpoints operacionais fora do prefixo.
- Alinhado `GET /health` com `operation_id`, código, descrição e resposta explícitos no OpenAPI.
- Validada a aderência do endpoint existente por testes automatizados.

## 2026-08-01 — TASK-006

- Criado módulo de saúde com `GET /health` e resposta estável `{"status":"ok"}`.
- Alterado o healthcheck do contêiner da API para usar o endpoint de vivacidade.
- Adicionados testes do contrato e do registro da rota no OpenAPI.
- Validado o endpoint por HTTP no ambiente Docker Compose, com API e PostgreSQL saudáveis.

## 2026-08-01 — TASK-005

- Configurado Pytest com descoberta explícita, validação estrita e cobertura mínima de 90%.
- Adicionados testes para os metadados FastAPI e para padrões, variáveis de ambiente e rejeição de configuração inválida.
- Separadas e documentadas as dependências de teste no conjunto de desenvolvimento.
- Validada a suíte com 4 testes aprovados e 100% de cobertura da base atual.

## 2026-08-01 — TASK-004

- Configurado Ruff para lint, ordenação de imports, modernização compatível com Python 3.14 e formatação.
- Separadas as dependências de desenvolvimento das dependências de runtime.
- Documentados comandos de verificação e correção automática.
- Corrigido o espaçamento dos blocos de importação existentes e validada toda a base atual.

## 2026-08-01 — TASK-003

- Criados Dockerfile da aplicação e Docker Compose para FastAPI e PostgreSQL 18.
- Adicionados volume persistente, healthchecks da API e do PostgreSQL e configuração local por `.env` não versionado.
- Documentados os comandos para iniciar e encerrar o ambiente local.
- Validado o ciclo completo com build sem cache, inicialização dos serviços, resposta HTTP da API, conexão do PostgreSQL e encerramento sem remoção do volume.

## 2026-08-01 — Consistência documental e versão do Python

- Confirmada pelo histórico e pelos critérios de aceite a conclusão das TASKs 000, 001 e 002.
- Sincronizados README, instruções, roadmap e índice de tarefas com o estado real do projeto.
- Adotada a política de uso da versão estável mais recente do Python, com Python 3.14.3 como versão atualmente validada.

## 2026-08-01 — Ambiente de desenvolvimento

- Criado `docs/DEPENDENCIES.md` como inventário de ferramentas e dependências da aplicação.
- Incluída no workflow a comparação de dependências em novas máquinas, com instalação somente após autorização.

## 2026-08-01 — TASK-002

- Adicionada gestão tipada de configuração por variáveis de ambiente.
- Criado exemplo seguro de configuração local e proteção para `backend/.env`.
- Validada a configuração padrão, a leitura de ambiente e a rejeição de valores inválidos.

## 2026-08-01 — TASK-001

- Criado o esqueleto mínimo da aplicação FastAPI.
- Declaradas as dependências FastAPI e Uvicorn.
- Validada a compilação, a integridade das dependências e a inicialização da aplicação.

## 2026-08-01 — Documentação

- Registrada a fase futura Marketplace Module para suporte a marketplaces.
- Mantido o escopo da V1 exclusivamente em lojas nacionais.
- Criados `docs/BACKLOG.md`, `docs/MVP.md` e `docs/OUT_OF_SCOPE.md` para controlar ideias futuras, escopo da V1 e exclusões explícitas.
- Adicionadas referências cruzadas a esses documentos no contexto do projeto e no roadmap.
- Criado `docs/DECISION_LOG.md` e adicionada política permanente de classificação prévia de novas funcionalidades no `AGENTS.md`.
- Reordenadas as dependências de ciclo de vida de missões e de catálogo de eventos; criada a TASK-055 para o Store Provider Kabum.
- Instituído o workflow oficial e permanente de execução de TASKs, com validação, testes, revisão técnica, documentação, commit convencional e push condicionado à autorização.
- Nenhuma funcionalidade foi implementada.

## 2026-08-01 — TASK-000

- Criada a estrutura inicial de diretórios.
- Criados documentos de contexto, visão, arquitetura e módulos-alvo.
- Registrados ADRs e RFCs iniciais.
- Criados arquivos individuais TASK-000 a TASK-054.
- Nenhuma funcionalidade de aplicação foi implementada.
