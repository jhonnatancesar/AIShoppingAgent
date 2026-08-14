# TASK-078 — Simplifica UX do Telegram e funde `/senha` em `/recuperar`

Status: **Implementada e testada (2026-08-14)**. Suíte não-integração
verde (1101 passed, 1 skipped, 91,05% cobertura), ruff limpo. Validação
real/Docker fora do escopo desta rodada, por instrução explícita.

## Objetivo

Reduzir o número de comandos e mensagens redundantes do bot, dar boas-
vindas diferentes para primeiro contato vs. retorno, reorganizar
`/ajuda`, criar `/missao` com exemplos, e eliminar `/senha` como
comando público — fundindo seu comportamento em `/recuperar` (que passa
a cobrir tanto "criar a primeira senha" quanto "recuperar senha
existente").

## Implementação (resumo objetivo)

- **`created_now` sem consulta nova**: `get_or_create_telegram_user`/
  `_async` (`backend/app/users/service.py`) passam a retornar
  `tuple[User, bool]` — o segundo valor é `True` só quando a própria
  chamada inseriu o registro (nunca inferido de `registration_step`/
  `username`, que também valeriam para alguém que abandonou o cadastro
  no meio). `TelegramAuthenticationResult` (`telegram/authentication.py`)
  ganhou o campo `created_now: bool = False`, propagado por
  `authenticate_telegram_user`/`_async`.
- **Primeiro contato (A)**: em `_handle_message` (`telegram/router.py`),
  `created_now and lowered not in _ALWAYS_AVAILABLE_COMMANDS` intercepta
  com `_FIRST_CONTACT_REPLY` — cobre `/start`, texto livre e qualquer
  comando que ainda não faz sentido sem cadastro. `_ALWAYS_AVAILABLE_COMMANDS
  = {/cadastro, /privacidade}`; `/ajuda` é resolvido antes dessa checagem
  (sempre disponível). Nenhum comando válido é interceptado incorretamente.
- **Retorno sem sessão (B)**: `/start` de quem já tem cadastro concluído
  (`registration_step is None` e `username` preenchido) sem sessão ativa
  responde `_RETURNING_NO_SESSION_REPLY`; com sessão ativa ou cadastro
  incompleto, cai no `_HELP_REPLY` normal.
- **`/senha` removido, fundido em `/recuperar` (C)**: `_authentication_link_reply`
  não mapeia mais `_RECOVERY_COMMAND` direto para `RECOVER_PASSWORD` —
  agora só `_LOGIN_COMMAND` tem mapeamento fixo (`LOGIN`); qualquer outro
  comando (hoje só `/recuperar`, e a chamada interna pós-cadastro) passa
  pela checagem de existência de `UserCredential`, escolhendo
  `SET_PASSWORD` (não existe ainda) ou `RECOVER_PASSWORD` (já existe) —
  nunca `CHANGE_PASSWORD`, que permanece no enum só por compatibilidade,
  sem nenhum caminho que o selecione mais. TTL, rate limit e política de
  senha são os já existentes de `RECOVER_PASSWORD`/`SET_PASSWORD` em
  `authentication/service.py` — nenhuma mudança lá.
- **`/cadastro` para já cadastrado (D)**: `_CADASTRO_ALREADY_REGISTERED_REPLY`
  atualizado para não citar `/senha`, aponta para `/entrar`/`/recuperar`.
- **`/missao` novo (E)**: comando dedicado com exemplos concretos
  (Ryzen 7 9800X3D, RTX 5070 Ti, mouse gamer), sem exigir sessão ativa,
  orientando a escrever o produto na mensagem seguinte.
- **`/ajuda` reorganizado (F)**: `_HELP_REPLY` agrupado em COMPRAS/CONTA/
  CONFIGURAÇÕES, sem tom de repreensão, sem `/senha`.
- **Menu final (G)**: `backend/scripts/register_telegram_commands.py`
  atualizado para `/start /ajuda /missao /editar-missao /cadastro
  /entrar /recuperar /sair /preferencias /privacidade` — `/senha`
  removido, `/upgrade` continua funcionando internamente mas fica fora
  do menu (já estava assim antes).

## Testes adicionados

`tests/test_telegram_router.py`: primeiro contato via `/start`; primeiro
contato via texto livre (sem chamar IA); `/cadastro` e `/ajuda` não
interceptados em primeiro contato; `/start` de usuário já cadastrado sem
sessão; `/ajuda` agrupado sem `/senha`; `/missao` com exemplo concreto;
`/recuperar` sem `UserCredential` → `SET_PASSWORD`; `/recuperar` com
`UserCredential` → `RECOVER_PASSWORD` (nunca `CHANGE_PASSWORD`).

`tests/test_user_telegram_identity.py`/`_async.py`: `created_now` `True`
só na inserção real, `False` no achado existente e nos dois ramos de
corrida (`IntegrityError`).

`tests/test_telegram_authentication.py`: `created_now` propagado de
`get_or_create_telegram_user` para `TelegramAuthenticationResult`
apenas quando o usuário está ativo.

`tests/test_telegram_router.py::_patch_user`: ganhou parâmetro
`created_now: bool = False`, mantendo os ~90 testes pré-existentes
inalterados por padrão.

## Fora de escopo (confirmado nesta rodada)

- Providers, coleta, TASK-084, fotos, link curto, TASK-076, TASK-077,
  banco/schema, migrations, ranking, identidade de produto — nenhum
  tocado.
- Documentação solta em `docs/` (`AUTHENTICATION.md`, `CHANGELOG.md`,
  `DECISION_LOG.md`, `TELEGRAM.md`, etc.) ainda cita `/senha` em texto
  narrativo — não corrigido nesta rodada por economia de tokens; fica
  registrado aqui como pendência de limpeza futura, sem impacto em
  comportamento.

## Critérios de aceite

1. ✅ Primeiro contato (`created_now=True`) mostra a apresentação do
   Cláudio para `/start` e texto livre; `/cadastro`, `/ajuda` e
   `/privacidade` continuam funcionando normalmente mesmo aí.
2. ✅ Usuário já cadastrado sem sessão recebe mensagem de boas-vindas de
   retorno em `/start`, orientando `/entrar`/`/recuperar`.
3. ✅ `/senha` não existe mais como comando público (nem no menu, nem em
   `_handle_message`); `/recuperar` cobre primeira senha e recuperação
   real, escolhendo a ação certa pela presença de `UserCredential`.
4. ✅ TTL, rate limit e política de senha preservados (nenhuma mudança em
   `authentication/service.py`/`models.py`).
5. ✅ `/ajuda` agrupado (COMPRAS/CONTA/CONFIGURAÇÕES); `/missao` com
   exemplos concretos.
6. ✅ Menu final igual ao especificado; `/upgrade` funciona internamente,
   fora do menu.
7. ✅ Suíte não-integração + ruff aprovados antes do commit. Validação
   real/Docker fora do escopo desta rodada.

## Impacto em banco/migration

Nenhum — `created_now` é derivado do controle de fluxo já existente em
`get_or_create_telegram_user`/`_async`, sem coluna nova; `CredentialAction`
não ganhou/perdeu valores.
