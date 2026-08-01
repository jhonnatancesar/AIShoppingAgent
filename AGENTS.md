# Instruções para agentes

## Leitura obrigatória

Antes de qualquer tarefa, leia integralmente `CLAUDE.md`, todos os documentos em `docs/`, `docs/ROADMAP.md` e o arquivo da tarefa em `docs/tasks/`.

## Regras de execução

- Execute exclusivamente a tarefa solicitada.
- Não antecipe funcionalidades, integrações ou infraestrutura previstas para tarefas futuras.
- Atualize `docs/PROJECT_CONTEXT.md` e `docs/CHANGELOG.md` ao concluir uma tarefa.
- Mantenha decisões arquiteturais em `adr/` e propostas relevantes em `rfc/`.
- Preserve todo o histórico de preços; descarte somente com política explícita aprovada.
- Todo acesso futuro a provedores de IA deve passar pelo AI Provider Manager.

## Governança de novas funcionalidades

Sempre que o usuário sugerir uma nova funcionalidade, analise primeiro o impacto no escopo, dependências, arquitetura, dados, operação e complexidade do projeto. A funcionalidade nunca deve ser implementada imediatamente.

Classifique a sugestão em exatamente uma categoria:

- Implementar agora
- Nova TASK do MVP
- Backlog
- Versão futura
- Out of Scope
- Rejeitada

Após classificar, registre a decisão em `docs/DECISION_LOG.md` e atualize automaticamente a documentação correspondente. Proteja sempre o escopo definido em `docs/MVP.md` e evite aumento de complexidade desnecessário. A classificação não substitui a necessidade de uma TASK explicitamente solicitada para implementar qualquer funcionalidade.

## Estado atual

TASK-000 concluída: estrutura e memória documental criadas. A aplicação ainda não possui código funcional.
