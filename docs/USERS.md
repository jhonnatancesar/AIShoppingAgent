# Usuários

`User` é a identidade interna mínima do sistema. A entidade existe para possuir recursos futuros e identificar atores, sem implementar autenticação, autorização ou integração de canal nesta etapa.

## Campos

- `id`: UUID gerado pela aplicação;
- `display_name`: nome obrigatório, com até 160 caracteres e não composto apenas por espaços;
- `role`: um dos papéis `USER`, `ADMIN` ou `DEV`;
- `is_active`: indicador administrativo, verdadeiro por padrão;
- `created_at` e `updated_at`: horários conscientes de fuso, persistidos como UTC.

O banco reforça a nulabilidade, o tamanho do nome, o conjunto fechado de papéis e os valores padrão aplicáveis. Nomes não são únicos: identidade e autenticação futuras não devem depender de `display_name`.

## Papéis

- `USER`: perfil de uso comum previsto para a V1;
- `ADMIN`: perfil administrativo previsto;
- `DEV`: perfil de desenvolvimento previsto.

O papel `PLUS` permanece fora do MVP. O campo `role` ainda não concede permissões; autorização será implementada somente na TASK-047.

## Limites

- `is_active=false` representa desativação lógica, não exclusão nem regra de autenticação pronta.
- Não são armazenadas senhas, tokens, e-mails ou identificadores do Telegram.
- Não existem endpoints, repositórios, serviços de CRUD ou usuário inicial automático.
- Exclusão e anonimização serão definidas pelas tarefas de segurança e privacidade, preservando referências históricas.

O modelo está em `backend/app/users/models.py` e sua criação reversível está na revisão Alembic `20260802_0002`.
