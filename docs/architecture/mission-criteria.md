# Critérios de missão

`MissionCriteria` mantém os critérios editáveis separados do ciclo de vida da
missão. A entidade foi introduzida na TASK-020 e existe no máximo uma vez por
missão.

## Contrato

- `id`: UUID gerado pela aplicação;
- `mission_id`: missão obrigatória e única, protegida por `RESTRICT`;
- `search_query`: texto de busca obrigatório e não vazio;
- `target_amount`: preço-alvo opcional em `numeric(19,4)`, nunca negativo. Na V1 (`DEC-045`), representa o preço-alvo do **produto** (`PriceObservation.amount`) para fins de monitoramento/alerta — não exige frete conhecido e não afirma custo final entregue; custo final com frete continua exclusivo de `app.purchase`;
- `target_currency`: código ISO 4217 opcional em `char(3)`;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Preço e moeda formam um par: ambos ficam nulos quando não há preço-alvo ou ambos
devem ser informados. A moeda aceita exatamente três letras ASCII maiúsculas,
como `BRL`.

## Ativação e edição

Uma missão em `draft` pode existir sem critérios. A ativação e a retomada exigem
um registro válido; essa validação e a mudança atômica de estado foram
implementadas na TASK-021.

`target_amount`/`target_currency` e as fontes selecionadas de uma missão já
criada são editáveis de verdade via `edit_mission_criteria`
(`backend/app/missions/service.py`, TASK-069) — só quando a missão está
`PAUSED`. Uma missão `ACTIVE` precisa ser pausada antes (o bot oferece pausar
automaticamente ao receber um pedido de edição); depois de editada, a missão
permanece `PAUSED` e só volta a coletar quando o usuário a retomar. A edição
pode limpar o preço-alvo (par `NULL`/`NULL`) ou trocar as fontes, mas nunca
`search_query`/`title`, nunca `status`, e nunca a agenda (`MissionSchedule`).
`updated_at` é tocado a cada edição e não substitui a auditoria das ações
relevantes.

**`state_version` (corrigido por `DEC-075`, TASK-092):** toda edição
bem-sucedida também incrementa `state_version`, exatamente como uma
transição de `transition_mission(_async)` -- este documento antes afirmava
o oposto ("nunca `state_version`"), o que descrevia uma proteção
incompleta: a função já exigia e conferia `expected_state_version` como
pré-condição, mas nunca avançava o contador em caso de sucesso, então duas
edições concorrentes baseadas na mesma versão passavam as duas pela
checagem e a segunda sobrescrevia a primeira em silêncio sempre que
tocassem o mesmo campo. `state_version` representa a versão concorrente da
missão inteira (lifecycle **e** critérios), não só do lifecycle.

As fontes de busca são relações tipadas em `mission_sources`, não filtros JSONB.
Uma missão pode selecionar Pichau, Terabyte, Amazon e/ou Kabum; ativação e
retomada exigem pelo menos uma fonte persistida, e a edição rejeita zerar todas
as fontes selecionadas. Remover uma fonte apaga só a relação `MissionSource`
— o histórico de `CollectionRun`/`PriceObservation` daquela loja nunca é
apagado.

## Limites

Filtros adicionais não foram antecipados porque não há requisitos concretos no
MVP atual. Recorrência e frequência foram implementadas como agenda tipada na
TASK-022 e não ficam em JSONB. A TASK-020 não cria API, comandos, transições,
coleta ou alertas.
