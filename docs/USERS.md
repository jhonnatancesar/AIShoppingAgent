# Usuários

`User` é a identidade interna mínima do sistema. A entidade existe para possuir recursos futuros e identificar atores, sem implementar autenticação, autorização ou integração de canal nesta etapa.

## Campos

- `id`: UUID gerado pela aplicação;
- `display_name`: nome obrigatório, com até 160 caracteres e não composto apenas por espaços;
- `role`: um dos papéis `USER`, `ADMIN` ou `DEV`;
- `is_active`: indicador administrativo, verdadeiro por padrão;
- `telegram_user_id`: identificador opcional e único da **pessoa** no Telegram
  (`message.from.id` no `Update`), introduzido pela TASK-056;
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
`user.id` do remetente. Este campo **não** representa nem substitui um
`chat_id`; nenhum identificador de conversa é persistido em `User` — se uma
tarefa futura precisar endereçar uma conversa (por exemplo, para enviar
notificações), ela deve definir e justificar esse campo separadamente, com
sua própria necessidade funcional.

## Papéis

- `USER`: perfil de uso comum previsto para a V1;
- `ADMIN`: perfil administrativo previsto;
- `DEV`: perfil de desenvolvimento previsto.

O papel `PLUS` permanece fora do MVP. O campo `role` ainda não concede permissões; autorização será implementada somente na TASK-047.

## Limites

- `is_active=false` representa desativação lógica, não exclusão nem regra de autenticação pronta.
- Não são armazenadas senhas, tokens nem credenciais de autenticação real
  (TASK-061, `DEC-019`, ainda não implementada). O e-mail deixou de fazer
  parte dessa lista a partir da TASK-060 (`DEC-020`): é dado pessoal comum
  de cadastro, não uma credencial.
- Não existem endpoints, repositórios genéricos de CRUD ou usuário inicial automático.
- Exclusão e anonimização serão definidas pelas tarefas de segurança e privacidade, preservando referências históricas.
- `telegram_user_id` (TASK-056) não implementa login, senha, OAuth, sessão ou
  qualquer autenticação real — isso permanece reservado à TASK-061; a
  resolução get-or-create (`app.users.service.get_or_create_telegram_user`)
  não conhece missões nem qualquer outra lógica de domínio.
- O papel (`role`) determina qual perfil de IA uma interação real usa
  (TASK-060): o webhook do Telegram escolhe entre `USER` (Gemini gratuito,
  sem fallback) e a cascata `ADMIN`/`DEV` (`AdminDevAIProviderManager`,
  TASK-059) a partir do `User.role` já resolvido — nunca por escolha do
  próprio usuário. Não existe autopromoção: elevar um usuário para
  `ADMIN`/`DEV` é uma ação manual e pontual, fora deste fluxo.
- O comando `/cadastro` (TASK-060) captura `username`, `email`,
  `favorite_stores` e `preferred_categories` em passos sequenciais,
  guardando o passo pendente em `registration_step`; ele intercepta a
  próxima mensagem do usuário diretamente, sem passar pelo
  `IntentInterpreter`. O comando `/upgrade` existe e é visível no menu do
  bot, mas responde apenas que a função está "em breve" — nenhuma lógica
  real de mudança de plano/perfil está implementada
  (`docs/OUT_OF_SCOPE.md`).

O modelo está em `backend/app/users/models.py`; sua criação reversível está na
revisão Alembic `20260802_0002`, `telegram_user_id` foi adicionado pela
revisão `20260807_0001` e os campos do cadastro inicial (TASK-060) pela
revisão `20260808_0001`. A resolução get-or-create está em
`backend/app/users/service.py`; o fluxo de cadastro está em
`backend/app/users/registration.py`.
