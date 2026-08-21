# TASK-072 — Impedir `/cadastro` para usuário autenticado + avisar username já em uso

Status: **Concluída em 2026-08-11**, desenho aprovado explicitamente pelo
usuário (com ajustes sobre a mensagem de bloqueio e o escopo exato da
checagem de username), implementada e validada com pipeline oficial.

Dependência: nenhuma. Item 6 da `v1.0.2` (`docs/internal/v1.0.2-scope.md`, `DEC-060`) —
último item pendente da versão.

## Contexto

O usuário ampliou o pedido original ("impedir `/cadastro` para usuário
já autenticado/logado") com mais três preocupações durante o desenho:
username duplicado entre contas, um telefone com mais de uma conta, e
uma conta "logando" num dispositivo que não é o dela. Uma auditoria
dedicada mostrou que as duas últimas **já são estruturalmente
garantidas** pelo sistema hoje — `User.telegram_user_id` tem constraint
`UNIQUE` no banco e `get_or_create_telegram_user` é seguro contra
corrida (`docs/architecture/users.md`); não existe nenhum caminho onde uma conta
diferente autentique através da identidade do Telegram de outra pessoa,
já que a sessão é sempre resolvida a partir do `telegram_user_id`
recebido, nunca de um dado informado pelo usuário. Nenhuma mudança foi
necessária para esses dois pontos.

O terceiro ponto (username duplicado) revelou uma lacuna real, mas
diferente do que parecia: `User.username` **já tem** constraint `UNIQUE`
no banco (`uq_users_username`) — nunca houve risco de duas contas
ficarem com o mesmo nome. O problema é de UX: o passo `username` do
`/cadastro` nunca consultava o banco antes de aceitar, então duas
pessoas escolhendo o mesmo nome ao mesmo tempo faziam a segunda avançar
normalmente até travar mais adiante (a escrita falhava silenciosamente
na constraint, sem nenhuma mensagem clara de "nome já em uso").

## Desenho aprovado (2026-08-11)

1. **Bloqueio do `/cadastro`**: condição é `has_active_session`
   (sessão/identidade do Telegram autenticada), não "já tem conta"
   nem "aparelho físico" — por decisão explícita do usuário, a mensagem
   evita a palavra "dispositivo": `"✅ Você já está cadastrado e
   autenticado neste Telegram."`. Sem sessão ativa (usuário novo ou
   sessão expirada), o comportamento continua idêntico ao de hoje.
   Bloquear não altera `registration_step` nem nenhum campo do perfil.
2. **Username duplicado**: checagem antecipada no passo `username`,
   melhoria de UX, **não substitui** a constraint `UNIQUE` do banco —
   essa continua sendo a proteção real contra corrida. Se o nome já
   pertence a outra conta, a pessoa permanece no passo `username` com
   mensagem clara pedindo outro nome; nada é escrito.
3. **Fora do escopo** (já garantido pela arquitetura, confirmado na
   auditoria, nenhuma mudança necessária): um telefone não pode ter mais
   de uma conta; nenhuma conta consegue autenticar através da identidade
   do Telegram de outra pessoa.

## Implementação (2026-08-11)

- **`backend/app/telegram/router.py`**: `_CADASTRO_ALREADY_AUTHENTICATED_REPLY`
  (mensagem fixa). O branch `_CADASTRO_COMMAND` em `_handle_message`
  passou a checar `has_active_session` antes de chamar
  `start_registration` — mesmo padrão de guarda já usado mais adiante no
  mesmo arquivo para os demais comandos que exigem sessão.
- **`backend/app/users/registration.py`**: `advance_registration` ganhou
  o parâmetro `session: Session`; nova `_ensure_username_available`,
  chamada só no passo `username`, entre `_validate_username` (formato) e
  a atribuição a `user.username` — consulta
  `select(User.id).where(User.username == username, User.id != user.id)`
  e levanta `RegistrationError` se encontrar outra conta.
- **Nenhuma alteração** em `backend/app/authentication/service.py`
  (`has_active_session` reaproveitado sem mudança), no modelo `User`
  (a constraint `UNIQUE` já existia), em `_parse_stores`/`_parse_categories`
  ou em qualquer outro passo do cadastro.
- **Nenhuma migration**: nenhum campo novo, nenhuma constraint nova.

## Validação (2026-08-11)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **880 testes (91,06% cobertura)**, migration head
  `20260810_0001` (sem alteração), **21 testes de integração PostgreSQL
  reais** — todos aprovados (`Pipeline local aprovado.`).
- **Testes novos de registration** (`tests/test_user_registration.py`):
  username já em uso mantém no passo `username` sem gravar nada; a
  consulta filtra por `username` e exclui o próprio usuário.
- **Testes novos de fluxo** (`tests/test_telegram_router.py`):
  `/cadastro` com sessão ativa bloqueia sem alterar `registration_step`
  nem nenhum campo já salvo (`username`/`email`/`favorite_stores`/
  `preferred_categories` preservados byte a byte); `/cadastro` sem
  sessão ativa continua funcionando como antes; username disponível
  avança normalmente; username já usado por outra conta mantém no
  mesmo passo com a mensagem de "já está em uso"; o fluxo completo de
  cadastro (`test_registration_full_flow_completes_and_clears_step`)
  continua passando ponta a ponta.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Escopo confirmado

1. `/cadastro` bloqueado com sessão ativa, mensagem fixa, sem alterar
   estado.
2. `/cadastro` sem sessão ativa: comportamento inalterado.
3. Passo `username` consulta disponibilidade antes de aceitar; mantém
   no mesmo passo se já estiver em uso.
4. Constraint `UNIQUE` do banco preservada como proteção final.
5. Nenhum outro fluxo de autenticação alterado; recuperação de senha
   fora do escopo.
6. Nenhuma migration; produção intocada; nenhuma tag `v1.0.2`; nenhuma
   outra TASK iniciada.

## Fora do escopo desta TASK

- Um telefone com mais de uma conta — já impedido pela constraint
  `UNIQUE` de `telegram_user_id` e pelo `get_or_create_telegram_user`
  seguro contra corrida.
- Uma conta autenticando através da identidade do Telegram de outra
  pessoa — arquitetonicamente impossível hoje, sem gap encontrado.
- Recuperação de senha (`/recuperar`) — comando já existente, não
  tocado.
- Qualquer comando novo de edição de perfil pós-cadastro.

## Encerramento

Concluída em 2026-08-11. `/cadastro` agora recusa reiniciar o fluxo
quando a sessão já está ativa, respondendo com uma mensagem fixa sem
tocar em nenhum campo salvo; sem sessão ativa, continua funcionando
exatamente como antes. O passo de nome de usuário passou a avisar
claramente quando o nome já pertence a outra conta, mantendo a pessoa
no mesmo passo em vez de deixá-la avançar rumo a uma falha silenciosa —
a constraint `UNIQUE` do banco continua sendo a proteção definitiva
contra corrida, intocada. As outras duas preocupações levantadas durante
o desenho (um telefone com mais de uma conta; uma conta autenticando em
identidade alheia) já eram garantidas pela arquitetura existente — nada
precisou ser implementado para elas. **Com esta TASK, os 7 itens da
`v1.0.2` estão implementados e validados** — nenhuma tag `v1.0.2`
criada ainda, publicação final da versão pendente de decisão explícita
do usuário. Produção da `v1.0.1` intocada; nenhuma outra TASK iniciada.
