# Decision Log

Este arquivo registra decisões arquiteturais e funcionais tomadas durante o desenvolvimento. Ele preserva o motivo de cada escolha e direciona a atualização documental necessária, sem substituir os ADRs para decisões arquiteturais formais.

## Processo obrigatório para novas funcionalidades

Antes de implementar qualquer funcionalidade sugerida, analisar seu impacto no escopo, nas dependências, na arquitetura, nos dados, na operação e na complexidade do projeto. A ideia deve receber exatamente uma das classificações abaixo:

- Implementar agora
- Nova TASK do MVP
- Backlog
- Versão futura
- Out of Scope
- Rejeitada

Após a classificação, registrar a decisão neste arquivo e atualizar a documentação correspondente. A classificação não autoriza implementação fora de uma TASK explicitamente solicitada.

## Formato de registro

### DEC-AAA — Título da decisão

- **Data:** AAAA-MM-DD
- **Ideia:** descrição objetiva da proposta ou decisão.
- **Classificação:** uma das seis classificações permitidas.
- **Justificativa:** impacto avaliado e motivo da classificação.
- **Próxima ação:** documento a atualizar, TASK a criar quando aplicável, ou ação de não implementação.

## Registros

### DEC-001 — Instituir governança de novas funcionalidades

- **Data:** 2026-08-01
- **Ideia:** registrar decisões e classificar previamente toda nova funcionalidade sugerida.
- **Classificação:** Implementar agora
- **Justificativa:** a política protege o escopo definido em `docs/MVP.md`, evita aumento de complexidade não planejado e cria rastreabilidade para decisões futuras.
- **Próxima ação:** aplicar a política no `AGENTS.md`; registrar ideias futuras em `docs/BACKLOG.md`, `docs/OUT_OF_SCOPE.md` ou no roadmap conforme sua classificação.
