# AIShoppingAgent

Agente inteligente de compras construído incrementalmente. O projeto está na fase de base técnica inicial, com o esqueleto FastAPI e a gestão tipada de configuração implementados.

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
