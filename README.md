# AIShoppingAgent

Agente inteligente de compras construído incrementalmente. O projeto possui a base FastAPI e a infraestrutura de persistência PostgreSQL preparadas para a implementação dos módulos de domínio.

O desenvolvimento usa a versão estável mais recente do Python disponível. A versão validada atualmente está registrada em `docs/DEPENDENCIES.md`.

> **Observação de segurança:** use somente a instalação oficial do Python da máquina.
> Não instale dependências nem rode o projeto com runtimes internos do Codex, plugins
> ou caches. Um alerta do antivírus deve interromper a execução; não restaure o objeto
> nem crie exceções automaticamente. Consulte o histórico e as medidas adotadas no
> [log de incidentes de segurança](docs/SECURITY_INCIDENT_LOG.md).

Consulte `AGENTS.md` antes de executar tarefas e `docs/ROADMAP.md` para a sequência planejada. O procedimento de instalação e manutenção em Ubuntu Server está em [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Ambiente local com Docker Compose

Copie o exemplo de configuração não sensível e inicialize os secrets com
entrada oculta. O Compose monta os valores em `/run/secrets`; não os coloque
no `.env` usado pelos contêineres:

```powershell
Copy-Item .env.example .env
python -m backend.scripts.manage_secrets init
python -m backend.scripts.manage_secrets check
docker compose up -d database
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose up --build
```

Quem já possui valores em `.env` pode usar
`python -m backend.scripts.manage_secrets migrate` sem imprimir nem apagar os
arquivos de origem. Desenvolvimento Python fora do Docker ainda aceita
`backend/.env`; produção aceita somente `*_FILE`. Consulte
`docs/SECRETS.md`, especialmente antes de rotacionar a senha de um banco já
inicializado.

A API ficará disponível em `http://localhost:8000`, o PostgreSQL em
`localhost:5432`, o Prometheus em `http://localhost:9090` e o Jaeger em
`http://localhost:16686`. O serviço `collection_worker` pesquisa as missões
agendadas e publica eventos; `telegram_notifier` consome continuamente os
alertas de preço. Para encerrar os contêineres sem apagar o volume do banco,
execute `docker compose down`.

Essas portas usam `127.0.0.1` por padrão. PostgreSQL, Prometheus, Jaeger,
Collector e métricas do worker nunca devem ser abertos diretamente para a
Internet; consulte o runbook antes de alterar qualquer bind ou firewall.

No Telegram, `/preferencias` consulta as notificações. Use
`/preferencias quedas ativar|desativar` e
`/preferencias alvo ativar|desativar` para configurá-las separadamente.
As duas preferências começam ativadas; eventos bloqueados não são reenviados
quando a preferência correspondente for reativada.
`/privacidade` apresenta, sem IA, um resumo do uso e proteção de dados. O
inventário, as retenções operacionais e a desidentificação controlada estão em
[`docs/PRIVACY.md`](docs/PRIVACY.md).

Operações de usuário pelo bot são aceitas somente no chat privado direto da
própria pessoa e para uma conta interna ativa. Depois da autenticação, a
política `USER ⊂ ADMIN ⊂ DEV` autoriza a operação sem remover o isolamento por
proprietário. Papel inválido ou recurso alheio falha fechado e termina sem
resposta funcional. Grupos, supergrupos e canais são ignorados. A TASK-061
acrescenta `/senha`, `/entrar`, `/sair` e `/recuperar`: senhas só entram no
formulário HTTPS e comandos funcionais exigem sessão absoluta de 12 horas.
Consulte `docs/AUTHORIZATION.md` e `docs/AUTHENTICATION.md`.

Com a API em execução, verifique sua vivacidade em `http://localhost:8000/health`. A resposta esperada é:

```json
{"status":"ok"}
```

`/health` não consulta dependências. Use `/ready` para verificar o PostgreSQL
real (`200` com `{"status":"ready"}` ou `503` com
`{"status":"not_ready"}`). `/metrics` é operacional e não aparece no
OpenAPI. A arquitetura, as regras de privacidade e a distinção entre estado
Prometheus e notificação externa estão em `docs/OBSERVABILITY.md`.

## Qualidade de código

Instale as dependências de desenvolvimento e execute as verificações a partir da raiz do projeto:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Para aplicar automaticamente correções seguras e formatação:

```powershell
python -m ruff check . --fix
python -m ruff format .
```

Os testes geram relatório de cobertura no terminal e exigem cobertura mínima de 90% do pacote `app`.

As regras para novos endpoints estão em `docs/API_CONVENTIONS.md`. O contrato executável da aplicação pode ser consultado em `http://localhost:8000/openapi.json` quando a API estiver ativa.

Os logs da aplicação são emitidos como JSON em `stdout`. Use
`docker compose logs --follow api collection_worker telegram_notifier` para acompanhá-los e
consulte `docs/LOGGING.md` para o contrato dos eventos.

## Pipeline local

Execute todas as verificações obrigatórias com:

```powershell
.\scripts\check.cmd
```

O pipeline também instala o Gitleaks 8.29.1 com checksum verificado, examina
working tree, arquivos versionados e histórico, e valida um canário gerado em
repositório temporário. O detalhamento está em `docs/LOCAL_PIPELINE.md`.

A suíte PostgreSQL real também pode ser executada isoladamente com
`.\scripts\check-integration.cmd` no Windows ou
`python scripts/run_integration_tests.py` no Ubuntu. Ela cria e remove somente
recursos sintéticos exclusivos; consulte `docs/INTEGRATION_TESTS.md`.

## Migrações

Após configurar os secret files e iniciar o PostgreSQL, aplique as migrações
pelo ambiente reproduzível do Compose:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
```

O ciclo completo, os comandos de inspeção e os cuidados com downgrade estão em `docs/MIGRATIONS.md`.
