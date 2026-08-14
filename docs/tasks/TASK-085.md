# TASK-085 — Seleção numérica (única e múltipla) para cancelar/pausar missões

Status: **Implementada e testada (2026-08-14)**. Suíte não-integração
verde (1092 passed, 91,02% cobertura), ruff limpo. Validação real/Docker
fora do escopo desta rodada, por instrução explícita.

## Implementação (resumo objetivo)

- `parse_multi_numbered_choice` novo em `confirmation.py` — mesma regra
  de `parse_numbered_store_selection` (qualquer token inválido invalida
  a resposta inteira, sem execução parcial), generalizada para índices
  de missão; aceita seleção única (`"1"`) e múltipla (`"1,3"`/`"2, 4"`)
  com a mesma função; deduplica preservando a primeira ocorrência.
- `list_mission_command_candidates` novo em `missions/query.py` — reusa
  a mesma busca de candidatas de `resolve_mission_for_command` (extraída
  para `_candidates_for_command`), sem levantar erro de ambiguidade;
  `resolve_mission_for_command` continua com o comportamento idêntico
  para 0/1 candidata.
- `_stage_mission_command` (router.py): 0 candidata → mesmo erro de
  sempre; 1 → mesmo fluxo de confirmação de sempre; **>1 → lista
  numerada com status** (`stage_mission_command_choice`/
  `describe_mission_command_choice_prompt`), gravando o mapeamento
  número→`mission_id` em `pending_intent`.
- `_apply_mission_command_choice`/`_apply_single_mission_choice`
  (router.py): resolve a resposta numérica deterministicamente (sem
  IA), processa cada missão selecionada individualmente (nunca tudo-ou-
  nada), revalida ownership (`session.get`) e estado
  (`transition_mission_async`, reaproveitando `MissionVersionConflictError`/
  `InvalidMissionTransitionError`/`MissionNotFoundError` já existentes)
  antes de cada ação, e responde item a item (`✅`/`⚠️`/`❌`).

**Decisão do "Ponto de decisão em aberto" (opção B escolhida)**: a
seleção numérica **já é a confirmação** — não intercala o par
confirmar/cancelar da TASK-058. Justificativa: escolher números
específicos de uma lista já exibida é um ato deliberado, diferente de
uma frase livre ambígua; pedir confirmação de novo seria redundante e
diverge do exemplo literal do pedido original (execução direta após o
número).

## Testes adicionados

`tests/test_telegram_confirmation.py`: `parse_multi_numbered_choice`
(seleção única/múltipla, espaços, índice inválido, token inválido,
vazio, deduplicação); `stage_mission_command_choice`/
`describe_mission_command_choice_prompt` (mapeamento, status na lista).

`tests/test_telegram_router.py`: mais de uma candidata gera lista
numerada; seleção única executa sem IA (`adapter.calls == []`); seleção
múltipla processa individualmente; índice inválido mantém
`pending_intent` e pede de novo; resultados mistos (sucesso + conflito
de versão + não encontrada) numa única resposta, sem abortar as demais;
ownership (missão de outro usuário) marca `❌` sem executar nada.

---


Dependência: reaproveita (sem alterar) componentes da `TASK-070`
(`parse_numbered_store_selection`) e `TASK-071`
(`parse_single_numbered_choice`, `describe_mission_choice_prompt`,
`_stage_edit_mission_choice`), todos em
`backend/app/telegram/confirmation.py`/`router.py`.

Versão alvo: a definir pelo usuário.

## Verificação de duplicata (obrigatória antes de criar TASK nova)

O usuário mencionou "provavelmente TASK-080". Busca sistemática nesta
rodada (todo `docs/`, incluindo `docs/BACKLOG.md`, `docs/ROADMAP.md`,
histórico de TASKs 070-079) **não encontrou nenhuma menção a seleção
numérica de missões em nenhum documento**. `TASK-080` (criada nesta
mesma rodada, Etapa 1) é sobre um assunto diferente e não relacionado —
transação síncrona no `telegram_notifier`. **Não há TASK anterior para
atualizar; esta é uma TASK nova.**

## Estado atual — `mission_command` (cancelar/pausar) hoje

`resolve_mission_for_command` (`backend/app/missions/query.py:62-94`):

```python
if not candidates:
    raise MissionReferenceError("Não encontrei nenhuma missão correspondente.")
if len(candidates) > 1:
    raise MissionReferenceError(
        "Encontrei mais de uma missão correspondente; seja mais específico."
    )
```

Hoje, sem `mission_reference` (texto livre) suficiente para reduzir a
**exatamente uma** missão não terminal, o comando falha e pede pro
usuário "ser mais específico" — não existe nenhuma lista numerada nem
seleção múltipla neste caminho. `docs/MISSION_COMMANDS.md` confirma
isso explicitamente: *"a V1 nunca expõe um identificador de missão"*.

## Componentes reutilizáveis já existentes (evitar duplicação)

Auditados em `backend/app/telegram/confirmation.py`:

- **`parse_single_numbered_choice(raw, *, count)`** (linha 378) — já
  usado por `/editar-missao` (TASK-071) quando há mais de uma missão
  `PAUSED`/`ACTIVE` candidata (`_stage_edit_mission_choice`,
  `router.py:864-880`; aplicação em `_apply_edit_mission_choice`,
  `router.py:883-889`). **Só seleção única** — não serve como está para
  `"1,3"`/`"2, 4"`.
- **`describe_mission_choice_prompt(titles, *, header)`** (linha 390) —
  já renderiza lista numerada de títulos de missão. Reaproveitável
  quase diretamente, só precisa incluir o status de cada missão na
  linha (o exemplo do usuário mostra `"1. Ryzen 7 9800X3D — ATIVA"`,
  hoje só mostra o título).
- **`parse_numbered_store_selection`** (linha 198, TASK-070) — já
  implementa o princípio "qualquer token não reconhecido invalida a
  resposta inteira" para uma lista de múltiplos itens separados por
  vírgula (lojas, não missões) — **é o precedente mais próximo do
  comportamento multi-seleção pedido aqui**, só que para um domínio
  diferente (nomes de loja fixos, não índices de missão dinâmicos).
- `User.pending_intent` (JSONB) — já é o mecanismo usado para guardar
  `mission_ids`/`mission_titles`/`mission_state_versions` durante uma
  escolha pendente (`_stage_edit_mission_choice`) — mesmo padrão a
  reaproveitar aqui.
- `state_version` por missão (já existe em `Mission`, já usado por
  `transition_mission`/`MissionVersionConflictError`) — mecanismo de
  concorrência otimista já pronto para "detectar estado alterado desde
  a listagem", sem precisar de nada novo.

**Nenhum desses precisa ser alterado** — a TASK nova adiciona um
componente irmão (`parse_multi_numbered_choice` ou nome equivalente),
não modifica os existentes.

## Objetivo

Quando `resolve_mission_for_command` encontrar mais de uma missão
candidata para `cancelar`/`pausar` (hoje um erro fechado), oferecer uma
lista numerada com status, aceitar seleção única ou múltipla
(`"1"`, `"1,3"`, `"2, 4"`, espaços opcionais), processar cada missão
selecionada **individualmente**, sem IA na resolução do número.

## Requisitos funcionais (do pedido do usuário)

1. Resolução do número é **determinística, sem IA** — mesmo princípio
   já usado em `parse_single_numbered_choice`/
   `parse_numbered_store_selection`.
2. Aceita seleção única e múltipla, separada por vírgula, espaços
   opcionais.
3. Índice inexistente invalida a resposta (mesmo princípio do
   `parse_numbered_store_selection`: **nunca aceita parcialmente** —
   um índice inválido rejeita a resposta inteira, pede para tentar de
   novo, não executa nada dos válidos).
4. **Nunca resolve a seleção usando uma nova ordenação do banco** — o
   mapeamento número→`mission_id` precisa vir exatamente da lista já
   mostrada ao usuário (gravada em `pending_intent` no momento da
   listagem, mesmo padrão de `_stage_edit_mission_choice`), nunca de
   uma nova consulta reordenada no momento da resposta.
5. Ownership obrigatório: toda missão selecionada precisa pertencer ao
   usuário que está respondendo — já garantido por construção, já que a
   lista original só contém missões do próprio usuário
   (`list_missions_for_user`/`find_missions_by_reference`, ambas
   filtradas por `user_id`).
6. Revalidar estado antes da ação — cada missão processada
   individualmente precisa reconferir `state_version`/status atual
   contra o que foi listado (reaproveitando `transition_mission`'s
   `MissionVersionConflictError` já existente) — **detecta estado
   alterado desde a listagem**, sem mecanismo novo.
7. Funciona para `cancel` e `pause` (os dois comandos de
   `mission_command` mais afetados pela ambiguidade; os demais —
   `activate`/`resume`/`complete`/`expire` — usam o mesmo
   `resolve_mission_for_command`, então ganham o mesmo comportamento
   por construção, sem trabalho extra).
8. Resultado processado item a item, com resposta detalhada por missão
   (formato do pedido do usuário):

   ```
   ✅ 1. Ryzen 7 9800X3D — pausada
   ✅ 3. RTX 5070 Ti — pausada
   ⚠️ 4. Mouse Logitech — já estava pausada
   ```

## Ponto de decisão em aberto — confirmação final

**Não decidido nesta auditoria, registrado para o usuário decidir**: a
arquitetura atual (TASK-058) sempre intercala uma confirmação
sim/não explícita entre "ação encenada" e "ação executada" — inclusive
quando `/editar-missao` já usa seleção numerada (`_apply_edit_mission_choice`
só avança para o próximo estágio do menu, nunca executa direto). Duas
opções:

**A. Manter o padrão existente**: a resposta numérica só resolve *quais*
missões, e o fluxo ainda pede confirmação final antes de executar
(consistente com todo o resto do sistema, um passo a mais que o exemplo
literal do usuário, que mostra execução direta após o número).

**B. Seleção numérica já é a confirmação**: como o usuário já escolheu
explicitamente números específicos (um ato deliberado, diferente de uma
frase livre ambígua), pular a confirmação extra só para este caminho —
mais perto do exemplo literal do pedido, mas é uma exceção ao padrão
estabelecido pela TASK-058 em todo o resto do sistema, que precisa ser
justificada explicitamente se escolhida.

## Reaproveitamento entre telas (pedido explícito do usuário)

`/editar-missao` (escolha de PAUSED/ACTIVE) e este novo fluxo
(cancelar/pausar ambíguos) compartilham a mesma forma — lista numerada,
`pending_intent` com mapeamento, parser determinístico. Diferença real:
edição é seleção única, este pedido é seleção múltipla. Proposta: extrair
o parser de índice único (`parse_single_numbered_choice`) e o novo
parser múltiplo para uma função-base comum (parsing de token por token,
validação de intervalo), evitando duas implementações paralelas da mesma
regra de validação — decisão de refatoração menor, a confirmar na
implementação, sem mudar o comportamento observável de `/editar-missao`.

## Fora de escopo

- Mudar `resolve_mission_for_command` para os casos já determinísticos
  (0 ou 1 candidata) — comportamento inalterado.
- `create_mission`/seleção de lojas (`parse_numbered_store_selection`,
  TASK-070) — só usado como referência de padrão, não modificado.
- `/editar-missao` (TASK-071) — não modificado, só uma possível extração
  de função-base compartilhada, sem mudança de comportamento.
- Teclado interativo do Telegram (`InlineKeyboard`) — fora do escopo
  desta TASK, que mantém o padrão de texto livre determinístico já
  usado em todo o projeto.

## Critérios de aceite

1. ✅ `"1"` (seleção única) e `"1,3"`/`"2, 4"` (múltipla, com/sem espaço)
   funcionam para `cancel` e `pause` (e, por construção via
   `resolve_mission_for_command`/`MissionCommand`, para os demais
   comandos também).
2. ✅ Qualquer índice fora do intervalo ou token não numérico invalida a
   resposta inteira — nenhuma das missões válidas é processada
   parcialmente.
3. ✅ Mapeamento número→`mission_id` vem exclusivamente do que foi
   gravado em `pending_intent` no momento da listagem.
4. ✅ Cada missão selecionada é revalidada individualmente antes da
   ação — conflito de versão/comando inválido marca ⚠️, missão ausente
   ou de outro usuário marca ❌, sem abortar as demais.
5. ✅ Ownership revalidada por item (`session.get` + comparação de
   `user_id`) antes de qualquer transição.
6. ✅ Resposta final no formato item a item (✅/⚠️/❌ por missão).
7. ✅ Decisão tomada: opção B (seleção numérica já é a confirmação).
8. ✅ Suíte não-integração + ruff aprovados antes do commit. Validação
   real/Docker fora do escopo desta rodada.

## Impacto em banco/migration

Nenhum esperado — reaproveita `pending_intent` (JSONB já existente) e
`state_version` (já existente); nenhuma tabela/coluna nova.
