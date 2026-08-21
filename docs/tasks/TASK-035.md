# TASK-035 — Criar comandos de missão

Status: Concluída em 2026-08-08

## Objetivo

Executar a ação de missão correspondente ao `Intent` que chega pelo webhook
do Telegram (criar, consultar ou comandar), com a identidade do usuário já
resolvida (TASK-056), respondendo ao Telegram — fechando o loop iniciado
pelas TASKs 032 a 034.

## Escopo

- Seed das quatro lojas selecionáveis da V1 (Pichau, Terabyte, Amazon,
  Kabum), necessário para `MissionSource.store_id` (`docs/architecture/providers.md`).
- `create_mission_from_criteria`: cria `Mission` + `MissionCriteria` +
  `MissionSource` e ativa imediatamente. Fontes vêm de
  `IntentParameters.sources` quando informadas; quando ausentes, usa
  automaticamente as quatro fontes da V1 — toda `CREATE_MISSION` válida sai
  `active`, nunca `draft` por falta de fonte.
- Resolução de missão por texto livre (`find_missions_by_reference`,
  `list_missions_for_user`, `resolve_mission_for_command`), já que a V1 não
  expõe identificador de missão ao usuário.
- Resposta síncrona mínima ao Telegram (`send_message`, sem SDK novo) para
  criação, consulta, comando e intenção desconhecida.
- Limite explícito entre erro conhecido de domínio (`204` + explicação) e
  falha inesperada (`500`, nunca mascarada) — `DEC-013`.
- **Sem teclado interativo**: a seleção de fontes vem do que o
  `IntentInterpreter` já extrai do texto livre, não de botões do Telegram
  (`DEC-012`). A apresentação visual do rótulo ***Futuro*** com Mercado
  Livre, Shopee e AliExpress desabilitados, mencionada no texto original
  desta tarefa, pressupõe um teclado interativo e **não foi implementada**;
  o que a TASK-035 garante é a restrição de dados equivalente: apenas as
  quatro fontes seedadas podem ser persistidas em `MissionSource`, e
  `IntentParameters.sources` já era um vocabulário fechado às quatro fontes
  desde a TASK-032. A apresentação interativa fica para uma evolução futura.
- Sem autenticação real, sem lógica nova de identidade (reaproveita a
  TASK-056), sem notificações proativas (TASK-036) e sem preferências de
  notificação (TASK-037).

## Critério de aceite

Escopo concluído, documentado em `docs/architecture/mission-commands.md`, coberto por
testes automatizados (`scripts\check.cmd` completo em Python 3.14.6: 298
testes, 94,73% de cobertura) e validado de ponta a ponta contra PostgreSQL
18, o Gemini e o Telegram reais: criação com fonte explícita e sem fonte
nenhuma (confirmando a fonte-padrão), consulta, comando (pausar) e mensagem
não reconhecida, todos com resposta real recebida no Telegram e o estado
correspondente confirmado em `missions`, `mission_criteria`, `mission_sources`
e `mission_transitions`.
