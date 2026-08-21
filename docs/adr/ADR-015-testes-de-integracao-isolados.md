# ADR-015 — Testes de integração isolados em PostgreSQL descartável

## Contexto

Os validadores reais criados nas tarefas anteriores dependiam de preparação
manual e de um banco compartilhado. Isso não formava uma suíte permanente nem
garantia execução individual, repetição, concorrência real ou proteção contra
um endereço de banco configurado pelo operador.

## Decisão

- usar PostgreSQL 18.4 Alpine fixado por digest em container e volume exclusivos
  para cada execução;
- publicar uma porta aleatória somente em `127.0.0.1` e recusar configuração de
  banco ou ambiente de produção herdados;
- obter dinamicamente o único head Alembic, aplicar `upgrade head`, conferir a
  revisão persistida e executar `alembic check`;
- migrar um banco-template e cloná-lo em um banco limpo por teste, sem transação
  global ou rollback compartilhado;
- manter PostgreSQL, SQLAlchemy, domínio, transações e concorrência reais, com
  doubles determinísticos apenas nas bordas externas reservadas à TASK-053;
- remover nominalmente os recursos próprios em `finally`, nunca por prune;
- executar a suíte como etapa obrigatória do pipeline completo.

## Consequências

- testes podem fazer commit e usar sessões/conexões distintas sem contaminar os
  demais casos;
- Docker e a imagem fixada passam a ser requisitos do pipeline completo;
- falhas de infraestrutura deixam de ser skip e reprovam a validação;
- uma atualização do patch/digest do PostgreSQL exige decisão e nova validação;
- Telegram, IA, lojas e navegador reais continuam pertencendo ao E2E da
  TASK-053.
