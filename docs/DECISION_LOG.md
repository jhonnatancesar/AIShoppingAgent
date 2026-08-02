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

### DEC-006 — Corrigir a TASK-055 para fontes selecionadas pelo usuário

- **Data:** 2026-08-02
- **Ideia:** substituir o escopo exclusivo da Kabum por coleta em todas as lojas e marketplaces explicitamente selecionados pelo usuário para a V1.
- **Classificação:** Implementar agora
- **Justificativa:** a TASK-055 registrada anteriormente não representa o requisito informado pelo usuário. A correção amplia arquitetura, testes e manutenção, pois cada fonte exige um Store Provider próprio, mas continua limitada à seleção explícita e não autoriza descoberta ou integração automática de qualquer marketplace.
- **Próxima ação:** corrigir o escopo da V1, o roadmap e a TASK-055; registrar a lista concreta de fontes antes de executar a tarefa e implementar somente os providers selecionados.

### DEC-005 — Ordenar observações de preço após coletas persistidas

- **Data:** 2026-08-02
- **Ideia:** corrigir a ordem de execução da TASK-015 para respeitar sua FK obrigatória para `collection_runs`.
- **Classificação:** Implementar agora
- **Justificativa:** executar a TASK-015 antes da TASK-026 exigiria antecipar persistência de coletas ou violar o contrato relacional e a rastreabilidade histórica definidos na TASK-010.
- **Próxima ação:** executar a TASK-016 antes do bloco de missões, seguir da TASK-019 à TASK-026, executar então a TASK-015 e, na sequência, a TASK-017.

### DEC-001 — Instituir governança de novas funcionalidades

- **Data:** 2026-08-01
- **Ideia:** registrar decisões e classificar previamente toda nova funcionalidade sugerida.
- **Classificação:** Implementar agora
- **Justificativa:** a política protege o escopo definido em `docs/MVP.md`, evita aumento de complexidade não planejado e cria rastreabilidade para decisões futuras.
- **Próxima ação:** aplicar a política no `AGENTS.md`; registrar ideias futuras em `docs/BACKLOG.md`, `docs/OUT_OF_SCOPE.md` ou no roadmap conforme sua classificação.

### DEC-002 — Instituir workflow permanente de execução de TASKs

- **Data:** 2026-08-01
- **Ideia:** padronizar preparação, validação, implementação, testes, revisão, documentação, commit e push para toda TASK.
- **Classificação:** Implementar agora
- **Justificativa:** o workflow preserva o escopo do MVP, aumenta a rastreabilidade das entregas e garante que código, documentação e repositório permaneçam sincronizados.
- **Próxima ação:** aplicar automaticamente o workflow definido em `AGENTS.md` a toda TASK futura; solicitar autorização explícita antes de cada push.

### DEC-003 — Inventariar dependências para novas máquinas

- **Data:** 2026-08-01
- **Ideia:** registrar dependências e verificar a compatibilidade do ambiente antes de iniciar TASKs em outra máquina.
- **Classificação:** Implementar agora
- **Justificativa:** evita instalações desnecessárias, mantém o ambiente reproduzível e preserva a autorização do usuário para qualquer download ou instalação.
- **Próxima ação:** manter `docs/DEPENDENCIES.md` e `backend/requirements.txt` atualizados; comparar o ambiente antes de cada nova TASK.

### DEC-004 — Acompanhar a versão estável mais recente do Python

- **Data:** 2026-08-01
- **Ideia:** manter o projeto na versão estável mais recente do Python, em vez de fixá-lo permanentemente em uma série menor antiga.
- **Classificação:** Implementar agora
- **Justificativa:** a alteração afeta somente a política de ambiente, não amplia o escopo funcional do MVP e evita instalar uma versão antiga quando a versão estável atual é compatível. Cada atualização continua condicionada à validação das dependências e dos testes aplicáveis.
- **Próxima ação:** registrar em `docs/DEPENDENCIES.md` a versão mais recente efetivamente validada e repetir a validação quando uma nova versão estável for adotada.
