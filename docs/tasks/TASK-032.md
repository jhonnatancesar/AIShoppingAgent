# TASK-032 — Criar interpretação de intenção

Status: Concluída

## Objetivo

Traduzir uma mensagem livre do usuário em uma intenção estruturada e
tipada, agnóstica de canal e sem lógica de domínio, usando exclusivamente o
`AIProviderManager` do perfil `USER`.

## Escopo

- Contrato imutável `Intent`/`IntentKind` com vocabulário fechado:
  `create_mission`, `query_mission`, `mission_command` (reaproveitando
  diretamente `MissionCommand` de `docs/MISSION_SYSTEM.md`) e `unknown`.
- `IntentParameters` reaproveitando campos já existentes (`search_query`,
  `target_amount`/`target_currency` de `MissionCriteria`, `sources`
  restrito às quatro fontes selecionáveis da V1 e `mission_reference`),
  sem introduzir nenhum campo novo de domínio.
- Serviço `IntentInterpreter` que monta a `AIRequest` (perfil `USER`,
  propósito `interpret_purchase_intent`) e faz parsing estrito da resposta,
  com fallback seguro para `unknown` em qualquer resposta fora do contrato.
- Zero acoplamento com Telegram, webhook, comandos ou teclado (TASK-033 em
  diante) e nenhuma execução de comando na máquina de missões.
- Script manual de validação real contra o Gemini do perfil `USER`, seguindo
  o padrão das TASKs 029 a 031.

## Critério de aceite

- Escopo concluído, documentado em `docs/INTENT_INTERPRETATION.md` e
  verificado conforme os critérios da tarefa.
- Testes unitários cobrindo contrato, parsing estrito e fallback `unknown`
  aprovados com Ruff (lint e formatação) e com execução real do `pytest` em
  Python 3.14.6 (236 testes aprovados, 94,79% de cobertura).
- Validação real contra o Gemini executada com o perfil `USER`, cobrindo os
  quatro valores de `IntentKind`: `create_mission`, `query_mission`,
  `mission_command` e `unknown` (ver `docs/CHANGELOG.md`).

