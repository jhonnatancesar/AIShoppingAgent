# Testes de integração PostgreSQL

A TASK-052 mantém uma suíte permanente contra PostgreSQL 18 real. Ela integra
migrations, SQLAlchemy, domínio, transações, locks, constraints, triggers,
Argon2id e concorrência sem usar banco ou credenciais do operador.

## Execução

Windows:

```powershell
.\scripts\check-integration.cmd
```

Windows ou Ubuntu Server com o Python oficial do host:

```bash
python scripts/run_integration_tests.py
```

Um teste isolado usa o mesmo runner e recebe ambiente novo:

```bash
python scripts/run_integration_tests.py tests/integration/test_schema_integration.py::test_schema_head_metadata_and_store_seeds
```

Não execute `pytest tests/integration` diretamente. A fixture falha fechado sem
o guard criado pelo runner.

## Ambiente descartável

Cada execução cria nomes aleatórios e labels `com.aishopping.integration.*`
para exatamente um container e um volume. A imagem é
`postgres:18.4-alpine` fixada por digest no runner. A porta é atribuída pelo
Docker e publicada somente em `127.0.0.1`.

O runner recusa `DATABASE_URL` ou qualquer `AISHOPPING_DATABASE_*` herdado,
gera configuração sintética, aguarda readiness por no máximo 60 segundos e
executa `alembic upgrade head`, downgrade de uma revisão e novo upgrade. Exige
um único head dinâmico, confere a revision persistida e roda `alembic check`;
migrations futuras não exigem editar um número fixo na suíte.

Depois da migration, o runner cria um guard secreto sintético no banco-template.
Cada teste clona esse template em banco próprio, verifica guard/head, executa
com conexões e commits reais e remove o banco. Seeds das quatro lojas vêm das
migrations. Não há rollback global nem dependência de ordem.

A TASK-062 acrescentou integração permanente de agenda/backfill, missões
inativas, falha isolada, persistência/eventos e duas conexões concorrentes no
claim. A suíte atual possui 11 integrações reais.

Container e volume são removidos nominalmente no `finally`, inclusive após
falha ou Ctrl+C quando o processo ainda consegue executar cleanup. Nunca são
usados `docker system prune`, globs, volumes reais, `.env` ou secret files do
projeto.

## Fronteiras e falhas

PostgreSQL, migrations, domínio e persistência são reais. Telegram, IA, lojas e
navegador externo não recebem tráfego; somente essas bordas são determinísticas
e locais. A integração externa completa pertence à TASK-053.

Docker ausente, readiness vencido, migration divergente, preparação impossível
ou teste obrigatório não executado são falhas. O diagnóstico contém etapa,
teste do Pytest, logs limitados e revision alcançada quando disponível,
redigindo senha e guard
sintéticos. Nenhuma credencial ou dado pessoal real participa da suíte.

## Erro conhecido do `alembic check`

**Resolvido em 2026-08-16 pela TASK-086.** O Alembic 1.19.1 filtrava da
metadata as constraints `_type_bound` geradas por `Enum`, embora refletisse os
mesmos checks nomeados do PostgreSQL. As constraints passaram a ser explícitas
nos models, preservando nomes e expressões. O runner oficial agora aprova sem
bypass `upgrade head`, `downgrade -1`, novo upgrade, `alembic check` e toda a
suíte de integração.

Em 2026-08-15, durante esta correção pontual, o runner padrão voltou a falhar
antes do Pytest em `alembic_upgrade_and_check`. O PostgreSQL 18.4 descartável
chegou ao head `20260811_0001`, mas `alembic check` reportou operações de
remoção para três constraints de enum já existentes:

- `mission_command_values` em `mission_transitions`;
- `store_source_type_values` em `stores`;
- `user_role_values` em `users`.

É o mesmo drift preexistente registrado nas TASKs 079/080; os arquivos desta
correção não alteram models nem migrations. Para não mascarar nem ampliar o
escopo, o erro não foi corrigido. A validação necessária repetiu o runner
descartável pulando **somente** o subpasso `alembic check`: preservou imagem
fixada, loopback, `upgrade head`, `downgrade -1`, novo upgrade, verificação do
head, guard, bancos clonados por teste e cleanup. Os dois alvos desta correção
(senha/token e cancelamento com desativação da agenda) passaram. Container e
volume descartáveis foram removidos; o banco e os containers do stack ativo
não foram alterados.

O relatório independente para diagnóstico e critérios de correção está em
`docs/ALEMBIC_CHECK_ISSUE.md`.
