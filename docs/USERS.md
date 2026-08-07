# Usuários

`User` é a identidade interna mínima do sistema. A entidade existe para possuir recursos futuros e identificar atores, sem implementar autenticação, autorização ou integração de canal nesta etapa.

## Campos

- `id`: UUID gerado pela aplicação;
- `display_name`: nome obrigatório, com até 160 caracteres e não composto apenas por espaços;
- `role`: um dos papéis `USER`, `ADMIN` ou `DEV`;
- `is_active`: indicador administrativo, verdadeiro por padrão;
- `telegram_user_id`: identificador opcional e único da **pessoa** no Telegram
  (`message.from.id` no `Update`), introduzido pela TASK-056;
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
- Não são armazenadas senhas, tokens, e-mails ou credenciais de autenticação real.
- Não existem endpoints, repositórios genéricos de CRUD ou usuário inicial automático.
- Exclusão e anonimização serão definidas pelas tarefas de segurança e privacidade, preservando referências históricas.
- `telegram_user_id` (TASK-056) não implementa login, senha, OAuth, sessão ou
  qualquer autenticação real — isso permanece reservado à TASK-046; a
  resolução get-or-create (`app.users.service.get_or_create_telegram_user`)
  não conhece missões nem qualquer outra lógica de domínio.

O modelo está em `backend/app/users/models.py`; sua criação reversível está na
revisão Alembic `20260802_0002` e `telegram_user_id` foi adicionado pela
revisão `20260807_0001`. A resolução get-or-create está em
`backend/app/users/service.py`.
