# TASK-086 — Corrigir drift de constraints no `alembic check`

Status: **Registrada; não iniciada.**

## Contexto conhecido

Em PostgreSQL 18.4 novo e descartável, as migrations chegam ao único head
`20260811_0001`, mas `scripts/run_integration_tests.py` falha na etapa
`alembic_upgrade_and_check`, antes de executar o Pytest. O `alembic check`
reporta operações envolvendo:

- `mission_command_values`, tabela `mission_transitions`;
- `store_source_type_values`, tabela `stores`;
- `user_role_values`, tabela `users`.

O problema é preexistente e já apareceu nas validações das TASKs 079 e 080.
Esta TASK não presume, antes de sua investigação formal, se a origem corrigível
está nas migrations, nos models/metadata SQLAlchemy, na representação ou
comparação de `CheckConstraint`, ou na configuração do Alembic/autogenerate.

## Escopo

1. Reproduzir o drift somente em PostgreSQL 18.4 descartável.
2. Comparar schema das migrations, constraints reais, metadata SQLAlchemy e
   comportamento do autogenerate.
3. Identificar e documentar a causa técnica.
4. Corrigir a origem real preservando a semântica das três constraints.
5. Não remover constraints apenas para silenciar o Alembic.
6. Não editar migrations antigas aplicadas sem análise de compatibilidade.
7. Não desabilitar permanentemente `alembic check`.
8. Não criar migration vazia ou genérica para esconder o problema.
9. Não usar o banco ativo em investigação destrutiva.

## Critérios de aceite

1. Causa identificada e documentada.
2. `alembic upgrade head` aprovado em PostgreSQL 18.4 novo.
3. Head final correto e único.
4. `alembic check` sem operações pendentes.
5. As três constraints presentes e semanticamente corretas.
6. Valores válidos e inválidos testados diretamente no PostgreSQL.
7. Downgrade e novo upgrade testados em banco descartável quando seguros.
8. `scripts/run_integration_tests.py` integral, sem bypass do check.
9. Suíte de integração aprovada pelo runner oficial.
10. Nenhuma alteração automática no banco ativo antes de revisão.

## Referências obrigatórias

- `docs/INTEGRATION_TESTS.md`, seção do erro conhecido do `alembic check`;
- `docs/CHANGELOG.md`, registro de 2026-08-15;
- `docs/tasks/TASK-079.md`;
- `docs/tasks/TASK-080.md`.

## Fora do escopo desta abertura

Nenhuma correção, migration, teste destrutivo, push, rebuild ou deploy é
autorizado pela criação deste documento.
