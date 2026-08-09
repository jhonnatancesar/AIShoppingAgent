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
