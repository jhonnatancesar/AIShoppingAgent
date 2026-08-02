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

Em uma nova máquina, comparar também as dependências descritas em `docs/DEPENDENCIES.md` com o ambiente disponível. Instalar somente dependências ausentes ou incompatíveis. O usuário concede autorização permanente para baixar e instalar o que for necessário para executar e validar o projeto; ainda devem ser respeitadas confirmações obrigatórias do sistema operacional, segurança, licenças e o princípio do menor impacto.

Use exclusivamente a instalação oficial do Python da máquina para instalar dependências e executar o projeto. Nunca instale pacotes nem execute validações com runtimes Python internos do Codex, de plugins, caches ou de outras ferramentas hospedeiras. Qualquer alerta do antivírus interrompe imediatamente a execução; o objeto não deve ser restaurado nem incluído em exceções sem investigação e autorização explícita.

### 2. Validação

Antes de escrever código, verificar se a TASK é consistente com a arquitetura, pertence ao MVP, possui dependências satisfeitas e não conflita com outras TASKs. Havendo qualquer inconsistência, interromper a implementação, explicar o problema e propor a correção.

### 3. Implementação

Quando a validação estiver aprovada, implementar somente a TASK solicitada. Não implementar funcionalidades futuras, não alterar outras TASKs e manter os padrões arquiteturais definidos.

### 4. Testes

Após a implementação, executar todos os testes aplicáveis. Corrigir automaticamente as falhas encontradas dentro do escopo da TASK e repetir os testes até que sejam aprovados.

Testes unitários, mocks, análise estática, SQL offline e validadores de código não substituem testes reais de integração. Quando a TASK envolver banco de dados, contêiner, API, fila, serviço externo ou outra infraestrutura, preparar um ambiente real e isolado, baixar ou instalar as ferramentas necessárias e validar nele o fluxo implementado, incluindo os principais casos de sucesso, falha e reversão aplicáveis. Se uma validação real for tecnicamente impossível mesmo após esgotar as alternativas seguras, registrar exatamente o impedimento e nunca apresentar a TASK como plenamente validada.

Ambientes temporários de teste devem usar nomes, portas, credenciais e volumes isolados, sem alterar ou apagar dados reais do usuário, e devem ser encerrados e limpos após a validação. A validação real complementa, e não elimina, a execução da suíte automatizada.

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

TASKs 000 a 014, TASK-016 e TASKs 018 a 024 concluídas: fundação, base técnica, ciclo de vida, modelo de dados, migrações, usuários, produtos, fontes, vendedores, ofertas, auditoria, missões, critérios, seleção de fontes, transições, agenda, adaptador de coleta e Playwright base preparados. A próxima tarefa executável é a TASK-055; a TASK-025 depende dela, e a TASK-015 depende da TASK-026.
