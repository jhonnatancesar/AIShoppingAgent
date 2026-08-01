# Dependências de Desenvolvimento

Este documento é a referência de ambiente para qualquer nova máquina. Antes de iniciar uma TASK, o agente deve lê-lo, comparar os requisitos com a máquina atual e instalar somente o que estiver ausente ou incompatível, sempre solicitando autorização antes de baixar ou instalar algo.

## Ferramentas locais

| Ferramenta | Requisito atual | Verificação |
| --- | --- | --- |
| Git | Git for Windows 2.55.0 ou compatível | `git --version` |
| Python | Versão estável mais recente, atualmente Python 3.14.3, com `pip` | `python --version` e `python -m pip --version` |

Docker e PostgreSQL ainda não são requisitos locais. Eles só serão preparados na TASK-003 e não devem ser instalados ou baixados sem autorização explícita.

## Política de versão do Python

O projeto acompanha a versão estável mais recente do Python, sem permanecer fixado em uma série menor antiga. Em cada nova máquina ou nova versão estável, a compatibilidade das dependências deve ser validada com `python -m pip check` e com os testes aplicáveis antes do uso. A versão informada na tabela é a mais recente efetivamente validada pelo projeto e deve ser atualizada após cada validação.

## Dependências da aplicação

A fonte de verdade para dependências Python é `backend/requirements.txt`:

- `fastapi>=0.115,<1.0`
- `pydantic-settings>=2.0,<3.0`
- `uvicorn[standard]>=0.30,<1.0`

Para comparar e instalar em uma nova máquina, após autorização:

```powershell
python -m pip install -r backend/requirements.txt
python -m pip check
```

## Configuração local

- Copiar `backend/.env.example` para `backend/.env` quando for necessário configurar valores locais.
- Nunca versionar `backend/.env`, credenciais, ambientes virtuais, cache ou imagens Docker.
- Atualizar este documento e `backend/requirements.txt` quando uma TASK introduzir uma dependência nova ou alterar uma versão suportada.
