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

## Workflow oficial e permanente de execução de TASKs

Este é o workflow obrigatório para toda TASK solicitada, em qualquer conversa futura. Ele deve ser iniciado automaticamente, sem necessidade de nova solicitação do usuário.

### 1. Preparação

Antes de qualquer alteração, ler integralmente:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/PROJECT_CONTEXT.md`
- `docs/ROADMAP.md`
- `docs/CHANGELOG.md`
- `docs/MVP.md`
- `docs/BACKLOG.md`
- `docs/OUT_OF_SCOPE.md`
- `docs/DECISION_LOG.md`
- `docs/DEPENDENCIES.md`
- o arquivo da TASK solicitada em `docs/tasks/`

Em uma nova máquina, comparar também as dependências descritas em `docs/DEPENDENCIES.md` com o ambiente disponível. Instalar somente dependências ausentes ou incompatíveis, sempre após autorização explícita para downloads ou instalações.

### 2. Validação

Antes de escrever código, verificar se a TASK é consistente com a arquitetura, pertence ao MVP, possui dependências satisfeitas e não conflita com outras TASKs. Havendo qualquer inconsistência, interromper a implementação, explicar o problema e propor a correção.

### 3. Implementação

Quando a validação estiver aprovada, implementar somente a TASK solicitada. Não implementar funcionalidades futuras, não alterar outras TASKs e manter os padrões arquiteturais definidos.

### 4. Testes

Após a implementação, executar todos os testes aplicáveis. Corrigir automaticamente as falhas encontradas dentro do escopo da TASK e repetir os testes até que sejam aprovados.

### 5. Revisão técnica

Revisar integralmente a implementação antes de encerrar, verificando bugs, lógica, arquitetura, desempenho, segurança, organização, duplicação de código, aderência aos padrões e oportunidades de simplificação. Corrigir os problemas encontrados dentro do escopo da TASK antes de continuar.

### 6. Revisão da documentação

Confirmar que código e documentação permanecem sincronizados. Atualizar, quando aplicável, `docs/PROJECT_CONTEXT.md`, `docs/CHANGELOG.md`, `docs/DECISION_LOG.md`, `docs/ROADMAP.md` e o status da TASK.

### 7. Controle de versão

Criar automaticamente um commit com Conventional Commits após a revisão final. Apresentar a mensagem do commit antes da confirmação final.

### 8. Repositório remoto

Perguntar sempre se o usuário deseja realizar o push. Só realizar o push após autorização explícita; em caso de autorização, verificar a sincronização do repositório remoto e informar o resultado.

### 9. Encerramento

Nunca iniciar automaticamente a próxima TASK. Encerrar apresentando resumo da implementação, arquivos criados e modificados, testes executados e seus resultados, problemas encontrados e correções realizadas, documentação atualizada, hash e mensagem do commit, status do repositório Git e status do repositório remoto.

## Estado atual

TASKs 000 a 004 concluídas: estrutura e memória documental, esqueleto FastAPI, gestão tipada de configuração, ambiente Docker Compose e qualidade de código implementados. A próxima tarefa planejada é a TASK-005.
