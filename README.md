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
