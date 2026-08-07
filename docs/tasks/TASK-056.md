# TASK-056 — Vincular identidade do usuário ao Telegram

Status: Pendente

## Objetivo

Resolver, sem senha, e-mail ou OAuth, qual `User` interno corresponde a um
chat do Telegram, criando um novo `User` no primeiro contato quando
necessário, para que a TASK-035 em diante consiga persistir missões com um
proprietário válido.

## Escopo

- Adicionar um identificador único e opcional do Telegram (`chat_id`) ao
  modelo de dados de usuário, com migração Alembic reversível, preservando
  as restrições já existentes em `docs/USERS.md` (nome obrigatório, papel
  fechado, ativação lógica).
- Implementar resolução get-or-create: dado o `chat_id`/`user_id` do
  Telegram recebido pelo webhook (TASK-034), devolver o `User` existente ou
  criar um novo com papel `USER`, de forma determinística e idempotente em
  chamadas repetidas.
- Não implementar login, senha, OAuth, sessão, token ou qualquer mecanismo
  de autenticação real — isso pertence à TASK-046. Não implementar
  autorização (TASK-047).
- Não implementar preferências de usuário (permanece na TASK-037, que passa
  a poder assumir um `User` já vinculado) nem qualquer lógica de missão:
  esta tarefa só garante a existência e a resolução do `User`, sem criar,
  consultar ou transicionar missões.

## Critério de aceite

Dado um `chat_id`/`user_id` do Telegram, o sistema resolve de forma
determinística e idempotente o mesmo `User` em chamadas repetidas, criando-o
apenas na primeira ocorrência. Coberto por testes automatizados e validado
em PostgreSQL real, incluindo a cadeia de migração (upgrade, downgrade, novo
upgrade).
