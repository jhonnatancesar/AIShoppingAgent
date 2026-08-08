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
| Perfil de IA por papel, cadastro e placeholder de upgrade | TASK-060 | Webhook escolhe o perfil de IA a partir de `User.role`, cadastro inicial não sensível e opção de upgrade visível porém inativa (`DEC-018`) |
| Autenticação real por usuário e senha | TASK-061 | Hashing seguro, verificação e recuperação de conta, além da identidade do Telegram (`DEC-019`) |
| Compra e eventos | TASK-038 a TASK-041 e TASK-043 a TASK-045 | Fluxos de compra, publicação, consumo e monitoramento |
| Segurança e entrega | TASK-046 a TASK-054 | Observabilidade, segurança e lançamento |
| Expansão de fontes (futuro) | Tarefas a definir | Mercado Livre, Shopee, AliExpress e outras fontes futuras |

As TASKs 000 a 037, as TASKs 042 a 044 e as TASKs 055 a 060 estão
concluídas. O preflight
da TASK-036 revelou dependências reais não satisfeitas (pipeline de eventos
persistidos/publicados e `chat_id` do Telegram, nenhum dos dois existente
antes desta sessão — `DEC-022`); TASK-043 (persistência e publicação de
eventos) e TASK-044 (consumo at-least-once por consumidor, `DEC-023`) foram
implementadas primeiro. A TASK-036 (`DEC-024`) persistiu somente o chat
privado, implementou o consumidor dos alertas de preço e o validou contra
Telegram, PostgreSQL e Docker reais. A TASK-037 (`DEC-025`) adicionou
preferências independentes para quedas e preço-alvo, com `skipped` terminal,
e foi validada contra PostgreSQL e Telegram reais. A próxima tarefa executável
é a TASK-038 (fluxo de recomendação). A TASK-057 (`DEC-017`): validação real contra o
`USER`/Gemini cobre 3 dos 4 `IntentKind`, e o usuário aceitou explicitamente
encerrar nesse estado, adiando mais variedade de linguagem para a V2
(`docs/tasks/TASK-057.md`, `docs/BACKLOG.md`). A TASK-058 (`DEC-015`):
`create_mission`/`mission_command` ficam encenados e só executam após
confirmação via classificador de IA dedicado, validado de ponta a ponta
contra o Telegram real. A TASK-059 (`DEC-016`): Groq como fallback opcional
do `ADMIN/DEV` e perfil configurável de validação do `IntentInterpreter`. A
TASK-060 (`DEC-018`): webhook escolhe o perfil de IA a partir de
`User.role`, `/cadastro` e `/upgrade` (inativo), validados de ponta a ponta
contra o Telegram real. A TASK-043 (`DEC-022`): publicação durável de
eventos — tabela `events` append-only e serviço genérico `publish_event`,
validado contra PostgreSQL real com candidatos reais de
`evaluate_price_alerts` (TASK-027); não inclui detecção dos demais tipos de
evento nem qualquer worker/consumidor. A TASK-044 (`DEC-023`): histórico
append-only de tentativas e reivindicação concorrente com
`FOR UPDATE SKIP LOCKED`, validada em PostgreSQL real; não inclui worker,
backoff, dead-letter queue ou notificação em seu próprio escopo — o consumidor
e a notificação foram adicionados depois pela TASK-036. A TASK-061 (`DEC-019`, autenticação
real por usuário e senha) foi registrada e aguarda solicitação explícita.
As demais continuam pendentes e só podem ser iniciadas por solicitação
explícita. A
V1 pesquisa Pichau, Terabyte, Amazon e Kabum; Mercado Livre, Shopee e
AliExpress permanecem futuras.

A ordem de execução é a ordem apresentada nesta tabela; a numeração da TASK é um identificador estável e não substitui dependências explícitas.

Toda TASK do roadmap segue o workflow oficial e permanente definido em `AGENTS.md`, incluindo validação prévia, testes, revisão técnica, sincronização documental, commit convencional e push somente após autorização.

O escopo obrigatório da V1 está em `docs/MVP.md`. Evoluções futuras devem ser registradas em `docs/BACKLOG.md`, e exclusões explícitas da V1 estão em `docs/OUT_OF_SCOPE.md`. Uma lista priorizada de evoluções para depois da V1 e antes da V2 está em `docs/V1_2.md` (`DEC-021`).
