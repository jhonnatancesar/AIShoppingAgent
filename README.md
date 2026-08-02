# AIShoppingAgent

Agente inteligente de compras construído incrementalmente. O projeto possui a base FastAPI e a infraestrutura de persistência PostgreSQL preparadas para a implementação dos módulos de domínio.

O desenvolvimento usa a versão estável mais recente do Python disponível. A versão validada atualmente está registrada em `docs/DEPENDENCIES.md`.

Consulte `AGENTS.md` antes de executar tarefas e `docs/ROADMAP.md` para a sequência planejada.

## Ambiente local com Docker Compose

Copie `.env.example` para `.env`, substitua a senha de exemplo e inicie os serviços:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

A API ficará disponível em `http://localhost:8000` e o PostgreSQL em `localhost:5432`, salvo alteração das portas no `.env`. Para encerrar os contêineres sem apagar o volume do banco, execute `docker compose down`.

Com a API em execução, verifique sua vivacidade em `http://localhost:8000/health`. A resposta esperada é:

```json
{"status":"ok"}
```

## Qualidade de código

Instale as dependências de desenvolvimento e execute as verificações a partir da raiz do projeto:

```powershell
python -m pip install -r backend/requirements-dev.txt
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

Os logs da aplicação são emitidos como JSON em `stdout`. Use `docker compose logs --follow api` para acompanhá-los e consulte `docs/LOGGING.md` para o contrato dos eventos.

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
