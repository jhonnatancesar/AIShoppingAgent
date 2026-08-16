# TASK-088 — Listar missões e sincronizar menu do Telegram

Status: **Concluída e validada (2026-08-16).**

## Classificação

**Nova TASK do MVP.** Completa a operação cotidiana das missões já existentes e
corrige a publicação operacional do menu nativo do Telegram.

## Objetivo

Adicionar o comando determinístico `/listar_missoes`, autenticado e sem IA,
para mostrar as missões recentes do proprietário nos estados `active`,
`paused` e `cancelled`. Atualizar o menu oficial e reaplicar `setMyCommands`
uma única vez depois do deploy.

## Escopo

- comando formal `/listar_missoes` e entradas determinísticas
  `/listar-missoes`, `missoes` e `missões`;
- listagem restrita ao proprietário autenticado;
- somente missões ativas, pausadas e canceladas, mais recentes primeiro;
- título, ícone e status localizado pelo formatter existente;
- limite operacional explícito para respeitar o tamanho de mensagem do Telegram;
- inclusão no `/ajuda`, em `register_telegram_commands.py` e documentação;
- testes sem chamadas reais a provedores de IA.

## Fora de escopo

- mudar estados de missão, schedules, transitions, coleta ou alertas;
- listar missões de outro usuário;
- reativar/cancelar/pausar pela listagem;
- paginação interativa ou `InlineKeyboard`;
- qualquer mudança em IA, banco ou migration.

## Critérios de aceite

1. `/listar_missoes` funciona sem IA e exige sessão válida.
2. Lista `active`, `paused` e `cancelled`, sem `completed` ou `expired`.
3. Ownership é aplicado na consulta.
4. Estado vazio recebe resposta controlada.
5. Menu nativo contém os comandos atuais e `/listar_missoes`.
6. `setMyCommands` é reaplicado com sucesso sem expor token.
7. Testes focados, suíte aplicável, Ruff e `git diff --check` aprovados.

## Resultado

- `/listar_missoes`, `/listar-missoes`, `missoes` e `missões` usam o mesmo
  caminho determinístico, autenticado e sem IA.
- A resposta é numerada no padrão `1 — Produto — ativa` e mostra até as 15
  missões recentes elegíveis, avisando quando houver mais.
- Consulta filtra no PostgreSQL por proprietário e por `active`, `paused` e
  `cancelled`; `completed`, `expired` e missões alheias ficam fora.
- Menu oficial inclui `/listar_missoes`; sua publicação por `setMyCommands`
  ocorre no deploy desta TASK.
- Validação: 152 testes focados; 1.192 testes não-integração, 1 ignorado,
  cobertura 90,69%; teste real no PostgreSQL 18.4 descartável; Ruff e
  `git diff --check` aprovados.
