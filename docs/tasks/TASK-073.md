# TASK-073 — Bloquear `/cadastro` para quem já tem cadastro concluído, mesmo sem sessão ativa

Status: **Concluída em 2026-08-11**, desenho aprovado explicitamente pelo
usuário, implementada e validada com pipeline oficial. Item único da
release `v1.0.3`.

Dependência: TASK-072 (`docs/tasks/TASK-072.md`), cuja checagem
(`has_active_session`) esta TASK complementa sem substituir.

## Contexto

Durante a validação real de `v1.0.2` em produção (deploy da mesma sessão
desta TASK), o usuário testou o `/cadastro` depois de fazer login e
logout algumas vezes seguidas. A sessão caiu (`/sair` ou um segundo
`/entrar` revogando a anterior) e, ao rodar `/cadastro` sem sessão ativa,
o bot o deixou reentrar no fluxo de cadastro — mesmo ele já tendo um
cadastro completo (`username` preenchido, `registration_step is None`)
feito um dia antes.

Investigação em `docs/tasks/TASK-072.md` confirmou que isso é
exatamente o desenho aprovado naquela TASK: o critério de bloqueio foi
deliberadamente definido como `has_active_session` (sessão autenticada
agora), não "já tem conta" — texto explícito na seção "Desenho
aprovado" da TASK-072. Não é um bug de execução: é um critério mais
estreito do que o usuário queria.

O usuário confirmou o novo critério desejado: se a pessoa **já tem um
cadastro concluído**, não faz sentido deixá-la reiniciar o cadastro só
porque a sessão expirou ou ela saiu — ela deveria ser direcionada a
`/entrar`, `/senha` ou `/recuperar`. Um cadastro **em andamento**
(`registration_step` diferente de `None`) continua funcionando como
sempre — isso não é afetado por esta TASK.

## Desenho aprovado (2026-08-11)

1. **Novo bloqueio**: além do bloqueio existente por `has_active_session`
   (TASK-072), `/cadastro` passa a bloquear também quando
   `user.registration_step is None and user.username is not None`
   (cadastro concluído no passado, sem sessão ativa agora). Mensagem
   fixa nova: `"📋 Você já tem cadastro neste Telegram.\n\nUse /entrar
   para acessar, /senha se ainda não criou sua senha, ou /recuperar
   caso tenha esquecido."` — cobre os três casos (esqueceu senha, nunca
   chegou a criar senha, só quer entrar), já que `/entrar`, `/senha` e
   `/recuperar` cada um resolve seu próprio estado corretamente
   (`_authentication_link_reply`, já existente).
2. **Cadastro em andamento não é afetado**: `registration_step is not
   None` continua caindo direto em `start_registration`, com o mesmo
   comportamento de sempre (inclusive o reinício ao rodar `/cadastro`
   de novo em pleno andamento — comportamento pré-existente, fora do
   escopo desta TASK; o usuário confirmou que não é crítico corrigir
   isso agora).
3. **Ordem de checagem** em `_handle_message` (`/cadastro`):
   `has_active_session` (TASK-072) → novo critério "já tem cadastro
   concluído" (esta TASK) → `start_registration` (usuário novo ou
   cadastro em andamento).
4. **Fora do escopo**: qualquer mudança no reinício de um cadastro em
   andamento; qualquer mudança em `/entrar`, `/senha`, `/recuperar` ou
   em `_ensure_username_available` (TASK-072, intocada).

## Implementação (2026-08-11)

- **`backend/app/telegram/router.py`**: nova constante
  `_CADASTRO_ALREADY_REGISTERED_REPLY`. O branch `_CADASTRO_COMMAND` em
  `_handle_message` ganhou um segundo `if`, depois do bloqueio existente
  de `has_active_session`, checando
  `user.registration_step is None and user.username is not None` antes
  de cair em `start_registration`.
- **Nenhuma alteração** em `app/users/registration.py`,
  `app/authentication/service.py`, nem em nenhum outro fluxo. Nenhuma
  migration — nenhum campo novo, nenhuma constraint nova.

## Validação (2026-08-11)

- **Testes novos** (`tests/test_telegram_router.py`):
  `test_cadastro_command_blocked_when_already_registered_without_session`
  (cadastro concluído, sem sessão → bloqueia, nada do estado salvo é
  tocado, mensagem correta) e
  `test_cadastro_command_resumes_in_progress_registration_without_session`
  (cadastro em andamento, sem sessão → comportamento inalterado,
  reinicia como sempre).
- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, suíte completa com cobertura, migration head sem
  alteração (`20260810_0001`), integrações PostgreSQL reais.
- **Produção**: nenhuma alteração feita como parte da implementação em
  si; deploy tratado separadamente como release `v1.0.3` (ver
  `docs/CHANGELOG.md` e commit de publicação).

## Escopo confirmado

1. `/cadastro` bloqueado quando o cadastro já está concluído
   (`registration_step is None` e `username` preenchido), mesmo sem
   sessão ativa — mensagem nova, direciona para `/entrar`/`/senha`/
   `/recuperar`.
2. Cadastro em andamento continua funcionando exatamente como antes.
3. Bloqueio por `has_active_session` (TASK-072) preservado, sem
   alteração, e avaliado primeiro.
4. Nenhuma migration; nenhum outro fluxo de autenticação alterado.

## Encerramento

Concluída em 2026-08-11. `/cadastro` agora recusa reiniciar o cadastro
tanto para quem está autenticado agora (TASK-072) quanto para quem já
concluiu o cadastro antes e simplesmente perdeu a sessão (esta TASK) —
direcionando para `/entrar`, `/senha` ou `/recuperar` conforme o caso.
Cadastro em andamento não foi afetado. Publicação da release `v1.0.3`
e deploy em produção tratados em seguida, na mesma sessão.
