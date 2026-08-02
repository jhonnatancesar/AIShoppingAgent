# Critérios de missão

`MissionCriteria` mantém os critérios editáveis separados do ciclo de vida da
missão. A entidade foi introduzida na TASK-020 e existe no máximo uma vez por
missão.

## Contrato

- `id`: UUID gerado pela aplicação;
- `mission_id`: missão obrigatória e única, protegida por `RESTRICT`;
- `search_query`: texto de busca obrigatório e não vazio;
- `target_amount`: total-alvo opcional em `numeric(19,4)`, nunca negativo e comparado ao preço com frete conhecido;
- `target_currency`: código ISO 4217 opcional em `char(3)`;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Preço e moeda formam um par: ambos ficam nulos quando não há preço-alvo ou ambos
devem ser informados. A moeda aceita exatamente três letras ASCII maiúsculas,
como `BRL`.

## Ativação e edição

Uma missão em `draft` pode existir sem critérios. A ativação e a retomada exigem
um registro válido; essa validação e a mudança atômica de estado foram
implementadas na TASK-021. Critérios continuam editáveis e `updated_at` não
substitui a auditoria das ações relevantes.

As fontes de busca são relações tipadas em `mission_sources`, não filtros JSONB.
Uma missão pode selecionar Pichau, Terabyte, Amazon e/ou Kabum; ativação e
retomada exigem pelo menos uma fonte persistida.

## Limites

Filtros adicionais não foram antecipados porque não há requisitos concretos no
MVP atual. Recorrência e frequência pertencem à agenda da TASK-022 e não ficam em
JSONB. A TASK-020 não cria API, comandos, transições, coleta ou alertas.
