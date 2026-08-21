# Sistema de Missões

Uma missão representa uma intenção persistente de compra, com critérios, ciclo de vida, resultados e eventos associados. Este documento define o contrato de ciclo de vida que deverá orientar o modelo persistente e a implementação futura.

## Estados

| Estado | Significado | Terminal |
| --- | --- | --- |
| `draft` | A missão foi criada, mas ainda pode estar incompleta e não participa de coletas. | Não |
| `active` | A missão está apta a ser agendada, coletada e avaliada. | Não |
| `paused` | O acompanhamento foi suspenso temporariamente sem perder critérios nem histórico. | Não |
| `completed` | O objetivo da missão foi encerrado com sucesso por decisão explícita. | Sim |
| `cancelled` | A missão foi encerrada sem sucesso por decisão explícita. | Sim |
| `expired` | A missão temporária alcançou seu limite de tempo antes de ser concluída. | Sim |

Toda missão nasce em `draft`. Uma missão permanente é representada pela ausência de prazo de expiração; ela não recebe tratamento especial no conjunto de estados e permanece ativa até uma transição explícita.

Encontrar uma oferta ou atingir um preço-alvo não conclui automaticamente a missão. Esses fatos poderão gerar resultados, eventos e alertas, mas a conclusão exige o comando específico; isso preserva missões recorrentes e permanentes.

## Comandos e transições permitidas

| Estado atual | Comando | Próximo estado | Condição |
| --- | --- | --- | --- |
| `draft` | `activate` | `active` | Os critérios são válidos e existe ao menos uma fonte selecionada. |
| `draft` | `cancel` | `cancelled` | Nenhuma condição adicional. |
| `draft` | `expire` | `expired` | Existe prazo e ele foi alcançado. |
| `active` | `pause` | `paused` | Nenhuma condição adicional. |
| `active` | `complete` | `completed` | Encerramento explícito do objetivo. |
| `active` | `cancel` | `cancelled` | Encerramento explícito sem sucesso. |
| `active` | `expire` | `expired` | Existe prazo e ele foi alcançado. |
| `paused` | `resume` | `active` | Critérios e fontes continuam válidos e o prazo não expirou. |
| `paused` | `complete` | `completed` | Encerramento explícito do objetivo. |
| `paused` | `cancel` | `cancelled` | Encerramento explícito sem sucesso. |
| `paused` | `expire` | `expired` | Existe prazo e ele foi alcançado. |

Qualquer combinação não listada é inválida. Estados terminais não possuem transições de saída; reabrir ou arquivar uma missão não faz parte da V1. Repetir um comando que já produziu seu estado não cria nova transição silenciosa: a operação futura deverá informar conflito de estado.

## Invariantes

- Somente missões `active` podem entrar em novos ciclos de coleta e avaliação.
- Pausar ou encerrar uma missão impede novos ciclos, mas não cancela trabalho já iniciado nem remove resultados ou histórico.
- Nenhuma transição apaga critérios, resultados, observações de preço, eventos ou registros de auditoria.
- A expiração é válida apenas para missões com prazo; missões permanentes nunca expiram implicitamente.
- A validação da transição e a alteração de estado deverão ser atômicas quando houver persistência.
- Comandos concorrentes deverão usar o estado persistido como fonte de verdade; apenas uma transição poderá vencer.
- Horários usam UTC e devem representar quando a transição foi efetivamente aceita.

## Registro mínimo de uma transição

O modelo de dados da TASK-010 deve permitir que cada mudança registre, no mínimo:

- identificador da missão;
- estado anterior e novo estado;
- comando que originou a mudança;
- instante da transição;
- origem ou ator responsável;
- motivo opcional, sem dados sensíveis.

O estado atual pertence à missão, enquanto o histórico de transições é anexado e imutável. Os nomes físicos, tipos, chaves e índices foram definidos na TASK-010; a auditoria geral foi aprofundada na TASK-016, e a execução dos comandos e transições foi implementada na TASK-021.

## Limites desta definição

Esta definição original não antecipou critérios, recorrência ou agenda. Critérios foram implementados na TASK-020 e a agenda recorrente na TASK-022; API, eventos, coleta e alertas permanecem nas tarefas específicas do roadmap.
