# Pipeline Local

O pipeline local reúne as verificações obrigatórias da base técnica em um único comando reproduzível para Windows PowerShell.

## Preparação

Instale as dependências de desenvolvimento após autorização:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
```

Python e Docker Compose devem estar disponíveis conforme
`docs/DEPENDENCIES.md`. No Windows, o pipeline resolve também a instalação
oficial por usuário do Docker Desktop quando o processo atual ainda não herdou
o PATH persistente. O Docker Engine não precisa estar ativo para validar o
arquivo Compose.

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

1. instalação do Gitleaks 8.29.1 com SHA-256 fixado e verificado;
2. varredura do working tree, arquivos versionados e histórico Git;
3. confirmação de detecção em repositório temporário com canário gerado;
4. integridade das dependências com `pip check`;
5. lint com Ruff;
6. verificação de formatação com Ruff;
7. testes e cobertura com Pytest, sem gravar cache local;
8. validação do grafo de migrações Alembic;
9. validação estrutural do Docker Compose com secret files temporários.

O primeiro uso do Gitleaks exige rede para baixar o release oficial. O binário
verificado permanece em `.tools/`, ignorado. A varredura usa redação total,
não grava relatórios e não imprime descobertas potencialmente sensíveis.

O script cria seis secret files não reais em diretório temporário somente para
validar o Compose e remove o diretório ao terminar. O pipeline não inicia
contêineres, não altera dados, não faz commits e não acessa o repositório
remoto.
