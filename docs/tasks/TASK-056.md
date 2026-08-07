# TASK-056 — Vincular identidade do usuário ao Telegram

Status: Concluída em 2026-08-07

## Objetivo

Resolver, sem senha, e-mail ou OAuth, qual `User` interno corresponde a uma
pessoa no Telegram, criando um novo `User` no primeiro contato quando
necessário, para que a TASK-035 em diante consiga persistir missões com um
proprietário válido.

## Escopo

- Adicionar `telegram_user_id`, identificador único e opcional da **pessoa**
  no Telegram (`message.from.id`, nunca `message.chat.id`), ao modelo de
  dados de usuário, com migração Alembic reversível, preservando as
  restrições já existentes em `docs/USERS.md` (nome obrigatório, papel
  fechado, ativação lógica). Nenhum `chat_id` é persistido nesta tarefa, por
  falta de necessidade funcional definida.
- Implementar resolução get-or-create (`get_or_create_telegram_user`): dado
  o `telegram_user_id`, devolver o `User` existente ou criar um novo com
  papel `USER`, de forma determinística e idempotente em chamadas repetidas,
  inclusive sob corrida entre duas mensagens simultâneas do mesmo usuário
  novo (via `SAVEPOINT`).
- Não implementar login, senha, OAuth, sessão, token ou qualquer mecanismo
  de autenticação real — isso pertence à TASK-046. Não implementar
  autorização (TASK-047).
- Não implementar preferências de usuário (permanece na TASK-037, que passa
  a poder assumir um `User` já vinculado) nem qualquer lógica de missão:
  esta tarefa só garante a existência e a resolução do `User`, sem criar,
  consultar ou transicionar missões.
- Não conectar o resolver ao webhook `POST /telegram/webhook` (TASK-034):
  como a sessão de banco será gerenciada por requisição HTTP é uma decisão
  da TASK-035, que é quem de fato vai persistir `Mission` a partir do
  `Intent`.

## Critério de aceite

Dado um `telegram_user_id`, o sistema resolve de forma determinística e
idempotente o mesmo `User` em chamadas repetidas, criando-o apenas na
primeira ocorrência. Coberto por testes automatizados (`scripts\check.cmd`
completo em Python 3.14.6: 272 testes, 94,50% de cobertura, `users/service.py`
e `users/models.py` a 100%) e validado em PostgreSQL 18 real via Docker
Compose: `get_or_create_telegram_user` chamado duas vezes devolveu o mesmo
`User.id`; uma inserção direta duplicada, contornando o serviço, foi
rejeitada pela constraint `UNIQUE` do banco; a cadeia de migração
(`upgrade`, `downgrade -1`, novo `upgrade`) foi confirmada até a revisão
`20260807_0001`.
