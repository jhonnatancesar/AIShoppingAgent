# Project Context

## Estado

Fase: ciclo de vida de missões definido. TASK-018 concluída em 2026-08-02.

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
- Documentos de visão, arquitetura, dados, módulos-alvo, escopo do MVP, backlog, itens fora de escopo, governança de decisões e workflow permanente de execução.
- ADRs, RFCs e 56 tarefas planejadas.

## O que não existe

Não há modelo ou integração de banco de dados, automações, integrações externas, testes de integração ou ponta a ponta nem credenciais reais configuradas.

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
