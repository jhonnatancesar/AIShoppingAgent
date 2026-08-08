# AIShoppingAgent

Agente inteligente de compras construído incrementalmente. O projeto possui a base FastAPI e a infraestrutura de persistência PostgreSQL preparadas para a implementação dos módulos de domínio.

O desenvolvimento usa a versão estável mais recente do Python disponível. A versão validada atualmente está registrada em `docs/DEPENDENCIES.md`.

> **Observação de segurança:** use somente a instalação oficial do Python da máquina.
> Não instale dependências nem rode o projeto com runtimes internos do Codex, plugins
> ou caches. Um alerta do antivírus deve interromper a execução; não restaure o objeto
> nem crie exceções automaticamente. Consulte o histórico e as medidas adotadas no
> [log de incidentes de segurança](docs/SECURITY_INCIDENT_LOG.md).

Consulte `AGENTS.md` antes de executar tarefas e `docs/ROADMAP.md` para a sequência planejada.

## Ambiente local com Docker Compose

Copie os exemplos de configuração, substitua a senha no `.env` da raiz e as
credenciais Telegram em `backend/.env`, inicie o banco, aplique as migrações e
então suba os serviços:

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
docker compose up -d database
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose up --build
```

A API ficará disponível em `http://localhost:8000`, o PostgreSQL em
`localhost:5432`, o Prometheus em `http://localhost:9090` e o Jaeger em
`http://localhost:16686`. O serviço `telegram_notifier` consumirá continuamente
os alertas de preço. Para encerrar os contêineres sem apagar o volume do banco,
execute `docker compose down`.

No Telegram, `/preferencias` consulta as notificações. Use
`/preferencias quedas ativar|desativar` e
`/preferencias alvo ativar|desativar` para configurá-las separadamente.
Ambas começam ativadas; eventos bloqueados pela preferência não são reenviados
quando ela for reativada.

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
`docker compose logs --follow api telegram_notifier` para acompanhá-los e
consulte `docs/LOGGING.md` para o contrato dos eventos.

## Pipeline local

Execute todas as verificações obrigatórias com:

```powershell
.\scripts\check.cmd
```

O detalhamento e os pré-requisitos estão em `docs/LOCAL_PIPELINE.md`.

## Migrações

Após configurar o `.env` e iniciar o PostgreSQL, aplique as migrações pelo ambiente reproduzível do Compose:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
```

O ciclo completo, os comandos de inspeção e os cuidados com downgrade estão em `docs/MIGRATIONS.md`.
