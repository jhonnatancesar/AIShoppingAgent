# TASK-074 — Corrigir digitação/completar marca-modelo no `search_query` do `IntentInterpreter`

Status: **Concluída em 2026-08-11**, desenho aprovado explicitamente pelo
usuário, implementada e validada com pipeline oficial e validação manual
real contra o `AdminDevAIProviderManager`.

Dependência: nenhuma. Complementa a TASK-057 (robustez de classificação),
mas resolve um problema diferente (ver Contexto).

## Contexto

Durante a validação real da `v1.0.2`/`v1.0.3` em produção, o usuário criou
uma missão digitando só "9950x3d" (sem "Ryzen 9") e a confirmação/busca
saiu exatamente assim, sem completar o nome do produto. Investigação
mostrou que `search_query` é usado **literalmente** como termo de busca em
cada loja (`app/collection/providers/base.py:131`,
`build_url(request.search_query)`) — não é só um detalhe da mensagem de
confirmação.

O usuário então perguntou o que aconteceria com "9951x3d" (erro de
digitação de um dígito, produto que não existe) — o sistema ficaria mudo
para sempre: nenhuma oferta encontrada, nenhuma notificação, e a consulta
de missões (`app/telegram/router.py:676-693`) não mostra quantidade de
ofertas, só o status — não há nenhum sinal de que algo deu errado.

O usuário esperava que isso já estivesse coberto pela TASK-057 ("agente
inteligente para ler a mensagem"). Auditoria do `docs/tasks/TASK-057.md`
confirmou que **não estava**: aquela TASK melhorou a robustez da
**classificação** de `IntentKind` (reconhecer que uma mensagem torta é um
pedido de criar missão), não a **correção do conteúdo** extraído para
`search_query`. São problemas relacionados mas distintos — a TASK-057
nunca tratou de limpar/corrigir o texto do produto em si.

## Desenho aprovado (2026-08-11)

Ajustar o prompt de sistema do `IntentInterpreter`
(`backend/app/intent/interpreter.py`) para **permitir e instruir**
explicitamente:

1. **Correção de digitação óbvia** no `search_query` (ex.: "logitek" →
   "logitech").
2. **Completar marca/modelo reconhecível** para a forma usual (ex.:
   "9950x3d" → "ryzen 9 9950x3d").
3. **Continua proibido**: adicionar especificação, cor, variante,
   quantidade ou característica que a pessoa não mencionou — a correção é
   só do nome do produto/marca já citado, nunca invenção de atributo novo.
   A regra geral de "nunca inventar comando, fonte, valor ou moeda" não
   muda; este ajuste é específico e aditivo, só para o conteúdo do
   `search_query`.

Dois exemplos novos foram adicionados ao few-shot do prompt (mesmo padrão
dos exemplos existentes) para reforçar o comportamento esperado.

## Fora do escopo desta TASK

- **Feedback de "zero resultados"** (o caso "9951x3d" de produto
  inexistente ficar mudo para sempre) — problema real e distinto,
  registrado para TASK futura se o usuário quiser tratá-lo: exigiria
  expor contagem de ofertas na consulta de missões e/ou uma notificação
  proativa após N tentativas sem `MATCH`.
- Normalização de `mission_reference` (usado para localizar uma missão já
  existente por substring, `app/missions/query.py`) — só `search_query`
  (usado na criação/busca) foi tratado aqui.
- Qualquer mudança em `AIProviderManager`, `Settings` ou nos perfis de IA.
- Vocabulário fechado de `IntentKind`/`MissionCommand`/`IntentParameters`
  — inalterado.

## Implementação (2026-08-11)

- **`backend/app/intent/interpreter.py`**: novo parágrafo no
  `_SYSTEM_PROMPT` autorizando correção de digitação/marca no
  `search_query`, com a ressalva explícita de nunca inventar
  característica nova. Dois exemplos few-shot novos (`"9950x3d"` →
  `"ryzen 9 9950x3d"`; `"logitek"` → `"logitech"`).
- **`backend/scripts/validate_intent_interpreter.py`**: as duas mensagens
  de teste adicionadas ao conjunto diverso de validação manual real
  (`_DIVERSE_MESSAGES`), para ficarem cobertas em validações futuras da
  robustez do interpretador.
- **Nenhuma mudança** em `parse_intent_response`, no contrato de
  `IntentParameters`, no `AIProviderManager` ou em qualquer outro módulo —
  o comportamento novo vem inteiramente do prompt.

## Validação (2026-08-11)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, testes e cobertura, migration head sem alteração, integrações
  PostgreSQL reais — aprovado.
- **Validação manual real** (`python -m backend.scripts.validate_intent_interpreter
  --profile admin --message "..."`, perfil `ADMIN/DEV` — nunca `USER`,
  conforme regra permanente do projeto) contra as duas mensagens novas:
  confirma `search_query` corrigido/completado como esperado, sem invenção
  de especificação não mencionada.

## Encerramento

Concluída em 2026-08-11. `search_query` agora corrige erro de digitação
óbvio e completa marca/modelo reconhecível para a forma usual, tanto na
mensagem de confirmação quanto no termo real de busca usado nas lojas —
sem abrir espaço para a IA inventar especificação, cor, variante ou
característica não mencionada pelo usuário. O caso de "zero resultados
silencioso" (produto que não existe de fato) permanece como lacuna
conhecida, fora do escopo desta TASK, registrado para decisão futura.
