# Pipeline Local

O pipeline local reúne as verificações obrigatórias da base técnica em um único comando reproduzível para Windows PowerShell.

## Preparação

Instale as dependências de desenvolvimento após autorização:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
```

Python, Docker Desktop e Docker Compose devem estar disponíveis no `PATH`, conforme `docs/DEPENDENCIES.md`. O Docker Engine não precisa estar ativo para validar o arquivo Compose.

## Execução

A partir de qualquer diretório, execute:

```powershell
C:\caminho\para\AIShoppingAgent\scripts\check.cmd
```

A partir da raiz do projeto, a forma curta é:

```powershell
.\scripts\check.cmd
```

O wrapper aplica `ExecutionPolicy Bypass` somente ao processo que executa o pipeline. A política permanente do Windows não é modificada.

O script interrompe na primeira falha e executa, nesta ordem:

1. integridade das dependências com `pip check`;
2. lint com Ruff;
3. verificação de formatação com Ruff;
4. testes e cobertura com Pytest, sem gravar cache local;
5. validação do grafo de migrações Alembic;
6. validação estrutural do Docker Compose.

Quando `POSTGRES_PASSWORD` não está definido, o script usa um valor temporário somente no processo para permitir a validação do Compose e o remove ao terminar. O pipeline não inicia contêineres, não altera dados, não faz commits e não acessa o repositório remoto.
