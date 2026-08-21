# Erro resolvido: drift no `alembic check`

> Resolvido pela TASK-086 em 2026-08-16. Causa e validação completas em
> `docs/tasks/TASK-086.md`.

## Resumo

O pipeline de integração chega ao head Alembic `20260811_0001` em um
PostgreSQL 18.4 novo e descartável, mas o comando `alembic check` detecta
diferenças entre o schema criado pelas migrations e a metadata atual do
SQLAlchemy.

O erro é preexistente e já foi observado nas TASKs 079 e 080. Ele não foi
causado pelas alterações recentes dos fluxos de Telegram, autenticação ou
missões.

## Estado

- **Situação:** aberto;
- **Efeito:** bloqueia o runner padrão antes do Pytest;
- **Integridade do banco:** não há evidência de perda ou corrupção de dados;
- **Ambiente confirmado:** PostgreSQL 18.4 descartável;
- **Head confirmado:** `20260811_0001`.

## Comportamento observado

Depois de aplicar todas as migrations, o `alembic check` reporta operações de
remoção para três constraints existentes:

| Constraint | Tabela |
|---|---|
| `mission_command_values` | `mission_transitions` |
| `store_source_type_values` | `stores` |
| `user_role_values` | `users` |

Isso mostra que o schema produzido pelas migrations e a metadata usada pelo
autogenerate não estão sendo considerados equivalentes para essas constraints.
A causa exata ainda deve ser diagnosticada; não se deve presumir se ela está
nos models, nas migrations ou na configuração de comparação sem inspecionar os
dois lados.

## Comportamento esperado

Em um banco PostgreSQL novo:

1. `alembic upgrade head` aplica todas as migrations;
2. a revisão persistida corresponde ao único head atual;
3. `alembic check` termina sem sugerir operações;
4. nenhuma constraint necessária é removida ou recriada indevidamente.

## Reprodução segura

Usar exclusivamente PostgreSQL 18.4 novo e descartável. Nunca reproduzir
diretamente no banco ativo da aplicação.

```text
1. Criar um PostgreSQL 18.4 descartável.
2. Configurar a aplicação com credenciais sintéticas desse banco.
3. Executar: alembic upgrade head
4. Confirmar o head: 20260811_0001
5. Executar: alembic check
6. Inspecionar as operações propostas para as três constraints.
```

O runner oficial `scripts/run_integration_tests.py` reproduz o erro na etapa
`alembic_upgrade_and_check`, antes de iniciar o Pytest.

## Impacto

- o pipeline oficial falha fechado antes dos testes de integração;
- validações não relacionadas exigiram um desvio temporário controlado;
- manter o desvio indefinidamente poderia mascarar um drift futuro;
- o pipeline não deve ser declarado aprovado enquanto esse check falhar.

## Contorno usado anteriormente

Uma cópia temporária do runner pulou **somente** o subpasso `alembic check`.
Foram preservados PostgreSQL 18.4 descartável, `upgrade head`, `downgrade -1`,
novo upgrade, confirmação do head, guard, bancos clonados, Pytest real e
cleanup de container e volume.

Esse contorno não corrige nem encerra o erro. O runner padrão permanece falho
nessa etapa.

## Restrições para a correção

- não editar migrations antigas aplicadas sem analisar compatibilidade;
- não remover constraints apenas para silenciar o autogenerate;
- não criar migration vazia ou genérica sem confirmar o drift real;
- não executar downgrade destrutivo no banco ativo;
- não desabilitar permanentemente o `alembic check`;
- preservar os valores válidos e a semântica dos três domínios.

## Critérios de aceite

1. causa do drift identificada e documentada;
2. `upgrade head` aprovado em PostgreSQL 18.4 novo;
3. `alembic check` aprovado sem operações pendentes;
4. downgrade e novo upgrade validados em banco descartável, quando seguros;
5. três constraints corretas no schema final;
6. valores válidos e inválidos testados diretamente no banco;
7. suíte completa aprovada pelo runner oficial, sem contorno;
8. nenhuma alteração aplicada ao banco ativo antes de revisão e autorização.

## Referências

- `docs/development/integration-tests.md`, seção **Erro conhecido do `alembic check`**;
- `docs/releases/changelog.md`, registros de 2026-08-15;
- `docs/tasks/TASK-079.md`;
- `docs/tasks/TASK-080.md`.
