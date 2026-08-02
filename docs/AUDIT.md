# Auditoria

`AuditEntry` registra ações relevantes como fatos duráveis, separados dos logs
operacionais. A estrutura foi introduzida pela TASK-016.

## Contrato

- `id`: UUID gerado pela aplicação;
- `actor_type`: origem obrigatória da ação, com até 32 caracteres;
- `actor_id`: usuário interno opcional, protegido por `RESTRICT`;
- `action`: identificador estável e obrigatório, com até 120 caracteres;
- `resource_type`: tipo obrigatório do recurso, com até 64 caracteres;
- `resource_id`: UUID obrigatório do recurso;
- `metadata`: objeto JSONB obrigatório, com padrão `{}` e sem segredos;
- `created_at`: instante obrigatório e consciente de fuso em UTC.

No modelo Python, a coluna `metadata` é acessada como `entry_metadata`, pois
`metadata` é reservado pela base declarativa do SQLAlchemy.

## Imutabilidade

A tabela é append-only: não possui `updated_at`, e um trigger PostgreSQL rejeita
`UPDATE` e `DELETE`. Correções devem ser registradas como novas entradas, nunca
reescrever o fato original. A futura política de retenção ou anonimização exige
decisão explícita e migration própria.

O índice `(resource_type, resource_id, created_at, id)` permite histórico
determinístico por recurso. O índice parcial `(actor_id, created_at)` atende
consultas por usuário sem indexar atores não vinculados a uma identidade interna.

## Segurança e limites

`metadata` deve conter apenas contexto mínimo e sanitizado. Credenciais, tokens,
corpos integrais de requisição e dados pessoais desnecessários são proibidos.
A TASK-016 não cria catálogo de ações, API, autenticação, autorização, eventos ou
instrumentação automática dos fluxos futuros.
