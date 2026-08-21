# Administração de usuários

Procedimentos manuais sobre a tabela `users`. Para o procedimento genérico
de acessar o `psql`, veja [PostgreSQL](../database/postgresql.md).

Papéis reais (`users.role`, `VARCHAR(16)` com `CHECK`, não é `ENUM`
nativo do PostgreSQL): **`USER`**, **`ADMIN`**, **`DEV`**, numa hierarquia
estrita `USER ⊂ ADMIN ⊂ DEV` (`DEV` contém todas as permissões de `ADMIN`).
O cadastro normal pelo bot (`/cadastro`) sempre cria um usuário `USER` —
não existe, hoje, nenhum fluxo automatizado de promoção de papel.

## Listar usuários recentes

```sql
SELECT id, display_name, username, role, is_active, telegram_user_id, created_at
FROM users
ORDER BY created_at DESC
LIMIT 20;
```

## Encontrar um usuário específico

Pelo `telegram_user_id` (identificador numérico da conta do Telegram — a
forma mais confiável):

```sql
SELECT id, display_name, username, role, is_active, telegram_user_id, created_at
FROM users
WHERE telegram_user_id = <telegram_user_id>;
```

Ou por `username`/`display_name` (cadastrados no `/cadastro`):

```sql
SELECT id, display_name, username, role, is_active, created_at
FROM users
WHERE username = '<username>' OR display_name ILIKE '%<nome>%';
```

## Ver dados e status completos de um usuário

```sql
SELECT id, display_name, username, email, role, is_active,
       telegram_user_id, telegram_chat_id,
       notify_price_decreases, notify_target_reached,
       favorite_stores, preferred_categories,
       created_at, updated_at
FROM users
WHERE id = '<UUID_DO_USUARIO>';
```

## Promover o primeiro usuário `DEV`

Não há mecanismo automatizado de promoção. O procedimento é manual,
auditado, feito diretamente no banco — é o mesmo usado para criar o
primeiro `DEV` da instalação:

1. Peça para a pessoa se cadastrar normalmente pelo bot (`/start`,
   `/cadastro`) e confirmar login (`/entrar`) como `USER` comum.
2. Localize o usuário (seção acima) e confirme o `id` (UUID) correto.
3. Confira o papel atual antes de alterar:

   ```sql
   SELECT id, display_name, role FROM users WHERE id = '<UUID_DO_USUARIO>';
   ```

   O resultado esperado antes da promoção é `role = 'USER'`.

4. Promova (para `DEV`, que já contém as permissões de `ADMIN`; use
   `'ADMIN'` em vez de `'DEV'` se quiser um papel intermediário):

   ```sql
   UPDATE users
   SET role = 'DEV', updated_at = now()
   WHERE id = '<UUID_DO_USUARIO>';
   ```

5. Confirme a alteração:

   ```sql
   SELECT id, display_name, role, updated_at FROM users WHERE id = '<UUID_DO_USUARIO>';
   ```

A pessoa promovida precisa fazer `/entrar` novamente no Telegram para que a
sessão reflita o novo papel — interações seguintes passam a usar a cascata
de IA `ADMIN`/`DEV` em vez do perfil `USER`.

## Ativar ou desativar uma conta

`is_active=false` bloqueia IA, missões, cadastro, preferências, resposta e
atualização do destino de notificação para essa conta — sem apagar
nenhum dado.

```sql
-- Confirmar o estado atual antes de alterar
SELECT id, display_name, is_active FROM users WHERE id = '<UUID_DO_USUARIO>';

-- Desativar
UPDATE users SET is_active = false, updated_at = now() WHERE id = '<UUID_DO_USUARIO>';

-- Reativar
UPDATE users SET is_active = true, updated_at = now() WHERE id = '<UUID_DO_USUARIO>';
```

## Sobre exclusão de usuários

Não use `DELETE` em `users`. Várias tabelas (`missions`,
`mission_transitions`, `price_observations` associadas via ofertas, etc.)
referenciam `users.id` com `RESTRICT` — a exclusão falharia por integridade
referencial ou, se forçada em cascata, destruiria histórico append-only que
o projeto preserva por design. O mecanismo suportado para remover dados
pessoais de uma conta é a **desidentificação controlada**, documentada em
[Privacidade](../architecture/privacy.md) — uma operação irreversível,
confirmada explicitamente, que preserva integridade referencial e histórico
técnico sem manter identificadores pessoais.
