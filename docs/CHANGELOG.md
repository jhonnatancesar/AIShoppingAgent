# Changelog

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
