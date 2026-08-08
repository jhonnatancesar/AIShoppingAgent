# Roadmap

| Fase | Tarefas | Resultado |
| --- | --- | --- |
| Fundação | TASK-000 | Estrutura e memória do projeto |
| Base técnica | TASK-001 a TASK-009 | Ambiente, aplicação e qualidade mínima |
| Ciclo de vida de missões | TASK-018 | Estados e transições definidos antes do modelo persistente |
| Dados | TASK-010 a TASK-017 | Modelo PostgreSQL e persistência, após a definição do ciclo de vida |
| Missões e coleta | TASK-019 a TASK-026 | Missões, coleta e histórico |
| Store Providers selecionados | TASK-055 | Busca em Pichau, Terabyte, Amazon e Kabum |
| Catálogo de eventos | TASK-042 | Eventos definidos antes dos alertas de preço |
| Alertas de preço | TASK-027 | Alertas baseados no catálogo de eventos |
| IA e interação | TASK-028 a TASK-037 | Gerenciador de IA e Telegram |
| Identidade do usuário no Telegram | TASK-056 | Vincula um `User` interno a uma pessoa do Telegram (`telegram_user_id`), pré-requisito da TASK-035 |
| Robustez da interpretação de intenção | TASK-057 | Refina o `IntentInterpreter` (TASK-032) para diferentes formas de escrita, sem alterar seu vocabulário |
| Confirmação da intenção interpretada | TASK-058 | Devolve a intenção interpretada e pede confirmação antes de executar comandos de missão (`DEC-015`) |
| Fallback de cota do AIProviderManager | TASK-059 | Avalia e, se aprovado, integra o Groq como fallback quando a cota do Gemini se esgotar (`DEC-016`) |
| Compra e eventos | TASK-038 a TASK-041 e TASK-043 a TASK-045 | Fluxos de compra, publicação, consumo e monitoramento |
| Segurança e entrega | TASK-046 a TASK-054 | Observabilidade, segurança e lançamento |
| Expansão de fontes (futuro) | Tarefas a definir | Mercado Livre, Shopee, AliExpress e outras fontes futuras |

As TASKs 000 a 035, a TASK-042, a TASK-055, a TASK-056 e a TASK-059 estão
concluídas. A próxima tarefa executável é a TASK-036. A TASK-057 está em execução: a
validação real contra o `USER`/Gemini cobre 3 dos 4 `IntentKind`, pausada
por nova exaustão de cota antes de confirmar `unknown`
(`docs/tasks/TASK-057.md`). A TASK-058 (`DEC-015`) foi registrada e aguarda
solicitação explícita. A TASK-059 (`DEC-016`) está concluída: Groq como
fallback opcional do `ADMIN/DEV` e perfil configurável de validação do
`IntentInterpreter`. As demais continuam pendentes e só podem ser iniciadas
por solicitação explícita. A V1 pesquisa Pichau,
Terabyte, Amazon e Kabum; Mercado Livre, Shopee e AliExpress permanecem
futuras.

A ordem de execução é a ordem apresentada nesta tabela; a numeração da TASK é um identificador estável e não substitui dependências explícitas.

Toda TASK do roadmap segue o workflow oficial e permanente definido em `AGENTS.md`, incluindo validação prévia, testes, revisão técnica, sincronização documental, commit convencional e push somente após autorização.

O escopo obrigatório da V1 está em `docs/MVP.md`. Evoluções futuras devem ser registradas em `docs/BACKLOG.md`, e exclusões explícitas da V1 estão em `docs/OUT_OF_SCOPE.md`.
