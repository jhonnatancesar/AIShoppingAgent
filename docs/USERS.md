# Usuários

`User` é a identidade interna mínima do sistema. A TASK-046 usa essa entidade
depois de autenticar o transporte e validar a identidade privada do Telegram;
a TASK-047 aplica autorização por papel e ownership; a TASK-061 vincula
credencial e sessão em tabelas separadas.

## Campos

- `id`: UUID gerado pela aplicação;
- `display_name`: nome obrigatório, com até 160 caracteres e não composto apenas por espaços;
- `role`: um dos papéis `USER`, `ADMIN` ou `DEV`;
- `is_active`: indicador administrativo, verdadeiro por padrão;
- `telegram_user_id`: identificador opcional e único da **pessoa** no Telegram
  (`message.from.id` no `Update`), introduzido pela TASK-056;
- `telegram_chat_id`: destino opcional e único de notificação, persistido pela
  TASK-036 somente para o chat `private` da mesma pessoa;
- `notify_price_decreases`: ativa notificações de queda de preço, `true` por
  padrão (TASK-037);
- `notify_target_reached`: ativa notificações de preço-alvo atingido, `true`
  por padrão (TASK-037);
- `username`: nome de usuário opcional e único do cadastro inicial (TASK-060),
  até 32 caracteres, não vazio quando presente;
- `email`: e-mail opcional do cadastro inicial (TASK-060), até 254
  caracteres, não vazio quando presente;
- `favorite_stores`: lista de códigos de loja preferidos (entre os quatro
  selecionáveis da V1), vazia por padrão;
- `preferred_categories`: lista livre de categorias preferidas (ex.: games,
  móveis), vazia por padrão;
- `registration_step`: passo pendente do fluxo `/cadastro` (TASK-060) —
  `null` quando não há cadastro em andamento;
- `created_at` e `updated_at`: horários conscientes de fuso, persistidos como UTC.

O banco reforça a nulabilidade, o tamanho do nome, o conjunto fechado de papéis e os valores padrão aplicáveis. Nomes não são únicos: identidade e autenticação futuras não devem depender de `display_name`.

### `telegram_user_id` identifica a pessoa, não a conversa

O Telegram distingue `user.id` (a pessoa, estável entre conversas) de
`chat.id` (a conversa — em chats privados os dois coincidem numericamente,
mas em grupos e canais divergem). `telegram_user_id` guarda exclusivamente o
`user.id` do remetente. Este campo não representa nem substitui um `chat_id`.
A TASK-036 adicionou `telegram_chat_id` separadamente e o banco exige que ele
coincida com `telegram_user_id`, pois somente a conversa privada direta é aceita
como destino. Mensagens em grupos, supergrupos e canais não alteram esse campo.

## Papéis

- `USER`: papel obrigatório e padrão para usuários normais;
- `ADMIN`: papel privilegiado de gestão operacional, atribuído somente de
  forma manual;
- `DEV`: superusuário técnico da V1, também atribuído somente de forma manual.

A política da TASK-047 implementa `USER ⊂ ADMIN ⊂ DEV` como herança de
permissões. DEV continua diferente de ADMIN, e nenhum dos dois contorna
ownership nas funcionalidades normais. Papel ausente ou desconhecido falha
fechado. O papel `PLUS`, planos e múltiplos papéis permanecem fora do MVP.

## Limites

- `is_active=false` representa desativação lógica e, desde a TASK-046, bloqueia
  toda operação recebida pelo Telegram antes de IA, domínio ou mutação; não
  exclui a conta e não substitui login por senha.
- A TASK-061 armazena somente hash Argon2id em `user_credentials`, hash SHA-256
  dos tokens temporários e sessões revogáveis; nenhum segredo bruto pertence
  a `users`. O e-mail continua sendo dado pessoal não verificado e não pode
  recuperar conta.
- Não existem endpoints, repositórios genéricos de CRUD ou usuário inicial automático.
- Todo usuário criado automaticamente pelo Telegram recebe `USER`. Nenhum
  fluxo público aceita papel por payload, texto, comando ou cadastro; não há
  autopromoção nem gestão de papéis pelo bot.
- A TASK-050 definiu desidentificação local e controlada: remove identificadores
  diretos, perfil, credenciais, sessões, tokens, preferências e textos mutáveis,
  desativa a conta e preserva referências históricas pelo UUID interno. Isso é
  pseudonimização operacional, não garantia de anonimização irreversível. O
  procedimento falha antes de qualquer mutação se encontrar PII em histórico
  append-only (`docs/PRIVACY.md`).
- `telegram_user_id` (TASK-056) é confiado pela TASK-046 somente após validar
  o segredo do webhook, chat privado e igualdade entre chat e remetente. Isso
  não implementa sozinha login ou senha; a TASK-061 acrescenta a camada de
  sessão posteriormente. A resolução get-or-create
  (`app.users.service.get_or_create_telegram_user`) não autentica sozinha e não
  conhece missões nem qualquer outra lógica de domínio.
- O papel (`role`) determina qual perfil de IA uma interação real usa
  (TASK-060): o webhook do Telegram escolhe entre `USER` (Gemini gratuito,
  sem fallback) e a cascata `ADMIN`/`DEV` (`AdminDevAIProviderManager`,
  TASK-059) a partir do `User.role` já resolvido — nunca por escolha do
  próprio usuário. O proprietário da V1 foi promovido uma única vez de ADMIN
  para DEV por UUID validado, com auditoria append-only. Não existe migration,
  rotina de startup nem conversão geral de ADMIN para DEV.
- O comando `/cadastro` (TASK-060) captura `username`, `email`,
  `favorite_stores` e `preferred_categories` em passos sequenciais,
  guardando o passo pendente em `registration_step`; ele intercepta a
  próxima mensagem do usuário diretamente, sem passar pelo
  `IntentInterpreter`. O comando `/upgrade` existe e é visível no menu do
  bot, mas responde apenas que a função está "em breve" — nenhuma lógica
  real de mudança de plano/perfil está implementada
  (`docs/OUT_OF_SCOPE.md`).
- O comando `/preferencias` (TASK-037) consulta ou altera exclusivamente
  `notify_price_decreases` e `notify_target_reached`, sem modificar nenhum
  campo do cadastro da TASK-060. O fluxo é textual, sem IA e sem botões.
- `/privacidade` apresenta aviso fixo sem IA e sem sessão por senha. Não existe
  comando público de exclusão, alteração de papel ou desidentificação.

O modelo está em `backend/app/users/models.py`; sua criação reversível está na
revisão Alembic `20260802_0002`, `telegram_user_id` foi adicionado pela
revisão `20260807_0001` e os campos do cadastro inicial (TASK-060) pela
revisão `20260808_0001`. A resolução get-or-create está em
`backend/app/users/service.py`; o fluxo de cadastro está em
`backend/app/users/registration.py`. O destino privado foi adicionado pela
revisão `20260808_0005` e é atualizado pelo webhook por meio de
`app.telegram.notifications.remember_private_notification_chat`. As duas
preferências foram adicionadas pela revisão `20260808_0006`.
A matriz e as regras de recusa da TASK-047 estão em
`backend/app/authorization/` e `docs/AUTHORIZATION.md`; nenhuma migration foi
necessária.
