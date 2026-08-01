# Roadmap

| Fase | Tarefas | Resultado |
| --- | --- | --- |
| Fundação | TASK-000 | Estrutura e memória do projeto |
| Base técnica | TASK-001 a TASK-009 | Ambiente, aplicação e qualidade mínima |
| Ciclo de vida de missões | TASK-018 | Estados e transições definidos antes do modelo persistente |
| Dados | TASK-010 a TASK-017 | Modelo PostgreSQL e persistência, após a definição do ciclo de vida |
| Missões e coleta | TASK-019 a TASK-026 | Missões, coleta e histórico |
| Primeiro Store Provider | TASK-055 | Kabum como prova de conceito da arquitetura de coleta |
| Catálogo de eventos | TASK-042 | Eventos definidos antes dos alertas de preço |
| Alertas de preço | TASK-027 | Alertas baseados no catálogo de eventos |
| IA e interação | TASK-028 a TASK-037 | Gerenciador de IA e Telegram |
| Compra e eventos | TASK-038 a TASK-041 e TASK-043 a TASK-045 | Fluxos de compra, publicação, consumo e monitoramento |
| Segurança e entrega | TASK-046 a TASK-054 | Observabilidade, segurança e lançamento |
| Marketplace Module (futuro) | Tarefas a definir | Suporte a marketplaces, incluindo AliExpress, Shopee e Amazon Marketplace |

As TASKs 000 a 007 estão concluídas. A próxima tarefa na ordem planejada é a TASK-008; as posteriores permanecem pendentes e só podem ser iniciadas por solicitação explícita. A V1 permanece focada em lojas nacionais; Marketplace Module não pertence ao escopo da V1.

A ordem de execução é a ordem apresentada nesta tabela; a numeração da TASK é um identificador estável e não substitui dependências explícitas.

Toda TASK do roadmap segue o workflow oficial e permanente definido em `AGENTS.md`, incluindo validação prévia, testes, revisão técnica, sincronização documental, commit convencional e push somente após autorização.

O escopo obrigatório da V1 está em `docs/MVP.md`. Evoluções futuras devem ser registradas em `docs/BACKLOG.md`, e exclusões explícitas da V1 estão em `docs/OUT_OF_SCOPE.md`.
