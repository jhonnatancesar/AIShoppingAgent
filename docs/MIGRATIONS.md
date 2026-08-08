# Migrações de Banco de Dados

O projeto usa Alembic sobre SQLAlchemy 2.0 e Psycopg 3. As revisões ficam em `backend/migrations/versions/` e usam a metadata única de `app.database.base.Base`.

## Configuração

A aplicação monta a conexão sem concatenar credenciais em texto. As variáveis são:

- `AISHOPPING_DATABASE_HOST`;
- `AISHOPPING_DATABASE_PORT`;
- `AISHOPPING_DATABASE_NAME`;
- `AISHOPPING_DATABASE_USER`;
- `AISHOPPING_DATABASE_PASSWORD`, obrigatória para qualquer acesso ao banco.

A senha usa o tipo secreto da configuração e não possui valor padrão. O arquivo `backend/.env.example` contém somente um placeholder e pode ser copiado para execução direta no host. No Docker Compose, os valores são derivados da configuração PostgreSQL do `.env` da raiz.

## Execução pelo Docker Compose

Depois de preparar o `.env` da raiz e iniciar o PostgreSQL, consulte e aplique as revisões com:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini current
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
```

Para reverter exatamente uma revisão:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini downgrade -1
```

Um downgrade pode destruir estruturas ou dados quando revisões futuras criarem tabelas. O diff da revisão e o alvo exato devem ser conferidos antes da execução fora de um banco descartável.

## Execução no host

Com as dependências instaladas e `backend/.env` configurado:

```powershell
python -m alembic -c backend/alembic.ini current
python -m alembic -c backend/alembic.ini upgrade head
```

## Criação de revisões

Depois que uma TASK adicionar ou alterar modelos na metadata compartilhada:

```powershell
python -m alembic -c backend/alembic.ini revision --autogenerate -m "descricao curta"
```

Toda revisão autogerada deve ser revisada manualmente. Renomes podem ser interpretados como remoção e criação, e o Alembic não conhece todas as regras de domínio.

Regras permanentes:

- uma revisão já compartilhada não é reescrita; correções recebem nova revisão;
- `upgrade()` e `downgrade()` devem ser explícitos e transacionais quando o PostgreSQL permitir;
- revisões não importam serviços de domínio nem executam integrações externas;
- nenhuma revisão remove histórico de preços, transições, eventos ou auditoria sem política explícita aprovada;
- deve existir uma única cabeça linear, salvo decisão arquitetural registrada.

## Revisões atuais

- `20260802_0001`: baseline vazia que valida a infraestrutura e cria apenas o controle interno `alembic_version`;
- `20260802_0002`: cria a tabela `users` e suas restrições;
- `20260802_0003`: cria a tabela `products` e suas restrições, sem unicidade artificial por nome;
- `20260802_0004`: cria `stores` e `offers`, suas relações, restrições e índices de identidade;
- `20260802_0005`: cria `audit_entries`, seus índices e a proteção append-only contra alteração e exclusão;
- `20260802_0006`: cria o enum `mission_status`, a tabela `missions`, suas restrições e índices;
- `20260802_0007`: cria `mission_criteria`, sua relação única com missões e as restrições monetárias.
- `20260802_0008`: cria `mission_transitions`, a constraint de comandos, seus índices e a proteção append-only.
- `20260802_0009`: adiciona seleção de fontes por missão, tipo de fonte, vendedores de marketplace e identidade de oferta por vendedor.
- `20260802_0010`: cria uma agenda recorrente por missão, suas constraints e o índice parcial de execuções vencidas.
- `20260802_0011`: cria execuções persistentes de coleta.
- `20260802_0012`: cria observações imutáveis de preço.
- `20260807_0001`: adiciona `telegram_user_id` (opcional, único) a `users` — identifica exclusivamente a pessoa no Telegram, nunca a conversa (`chat_id` não é persistido).
- `20260807_0002`: semeia as quatro lojas selecionáveis da V1 (`pichau`, `terabyte`, `amazon`, `kabum`), conforme `docs/MARKETPLACE_SOURCES.md`, necessárias para `MissionSource.store_id`. O downgrade remove exatamente essas quatro linhas por código, nunca um `DELETE` genérico.
- `20260808_0001`: adiciona os campos de cadastro inicial a `users`.
- `20260808_0002`: adiciona `pending_intent` a `users` para confirmação durável de intenção.
- `20260808_0003`: cria `events`, seus índices e proteção append-only.
- `20260808_0004`: cria o enum `consumption_outcome`, a tabela
  `event_consumption_attempts`, o índice parcial de sucessos e sua proteção
  append-only; o downgrade remove também o enum.

O downgrade de `20260802_0009` não apaga ofertas para forçar compatibilidade. Se
existirem ofertas de vendedores diferentes com a mesma identidade antiga, elas
devem ser migradas de forma explícita antes da reversão; a recriação dos índices
anteriores falhará em vez de descartar histórico silenciosamente.

Novas tabelas ou colunas serão introduzidas pelas TASKs futuras conforme suas
dependências explícitas.
