# PostgreSQL

Esta página é a fonte única de verdade para acessar o PostgreSQL do
AIShoppingAgent manualmente. Outros documentos ([Administração de
usuários](../administration/users.md), [Administração de
missões](../administration/missions.md), guias de instalação) apontam para
cá em vez de repetir o procedimento de acesso.

O banco roda no serviço `database` do `compose.yaml` (imagem
`postgres:18-alpine`), nome do banco `aishoppingagent`, usuário
`aishoppingagent` por padrão (`POSTGRES_DB`/`POSTGRES_USER` em
[Configuração](../installation/configuration.md)). A porta só é publicada
em `127.0.0.1:5432` — nunca exposta publicamente.

## Entrar no container e abrir o `psql`

```powershell
docker compose exec -T database sh -c 'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

Isso já abre uma sessão interativa `psql` conectada ao banco correto, sem
precisar digitar usuário/senha (o container já tem as variáveis de
ambiente). Para uma sessão interativa completa (com histórico de comandos),
remova o `-T`:

```powershell
docker compose exec database sh -c 'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
```

## Comandos básicos dentro do `psql`

| Comando | O que faz |
| --- | --- |
| `\l` | Lista os bancos |
| `\c aishoppingagent` | Conecta a um banco específico (normalmente já é o padrão) |
| `\dt` | Lista as tabelas do schema atual |
| `\d nome_da_tabela` | Descreve colunas, tipos, constraints e índices de uma tabela |
| `\di` | Lista os índices |
| `\dT` | Lista os tipos (enums) |
| `SELECT ...;` | Consulta registros |
| `\q` | Sai do `psql` |

Exemplo — ver a estrutura da tabela `missions`:

```sql
\d missions
```

## Tabelas principais

O schema completo (colunas, tipos, obrigatoriedade, FKs) é documentado em
[Schema do banco](schema.md). Resumo das tabelas mais usadas em
administração manual:

| Tabela | Conteúdo |
| --- | --- |
| `users` | Contas, papel (`role`), preferências de notificação, cadastro |
| `missions` | Missões de acompanhamento de preço |
| `mission_criteria` | Busca e preço-alvo de cada missão |
| `mission_sources` | Lojas selecionadas por missão |
| `mission_transitions` | Histórico append-only de mudança de estado de missão |
| `mission_schedules` | Agendamento de coleta por missão |
| `stores` | Lojas cadastradas (`code`, `is_active`) |
| `products` / `offers` | Produtos e ofertas identificadas |
| `price_observations` | Histórico append-only de preço coletado |
| `offer_installment_options` | Condições de parcelamento declaradas pela loja |
| `events` / `event_consumption_attempts` | Catálogo de eventos e tentativas de entrega (alertas) |

## Consultar sem entrar em modo interativo

Para rodar uma única consulta a partir do PowerShell, sem abrir uma sessão
`psql` interativa (útil para scripts ou para colar num comando só):

```powershell
docker compose exec -T database sh -c 'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT id, display_name, role FROM users ORDER BY created_at DESC LIMIT 5;"'
```

## Boas práticas para consultas administrativas

- **Sempre `SELECT` antes de `UPDATE`/`DELETE`.** Localize e confirme o
  registro certo antes de qualquer alteração.
- **Use o `id` (UUID)**, não nome ou texto livre, para alvo de uma
  alteração — texto livre pode casar mais de um registro.
- **Nunca copie dado pessoal para fora do servidor** (nome, e-mail,
  Telegram ID, texto de missão) — nem em logs, nem em mensagens, nem em
  documentação.
- Depois de qualquer alteração manual, **rode o mesmo `SELECT` de novo**
  para confirmar o resultado.
- Tabelas append-only (`mission_transitions`, `price_observations`,
  `events`, `event_consumption_attempts`, `purchase_trail_entries`,
  `audit_entries`, `telegram_update_receipts`) têm triggers que bloqueiam
  `UPDATE`/`DELETE` no banco — não tente alterá-las manualmente.

Para os procedimentos administrativos completos com SQL real por assunto,
veja [Administração de usuários](../administration/users.md) e
[Administração de missões](../administration/missions.md).
