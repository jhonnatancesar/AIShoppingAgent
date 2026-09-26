# TASK-128 — Produto nunca fica sem vínculo + "não entendi, descreva melhor" para pedido vago

Status: **Concluída (2026-09-26), commitada localmente** — etapa 1
(`ae43149`), etapa 2 (`9f0ae2d`), etapa 3 (commit desta TASK). Etapa 4
**descartada por decisão do usuário** (ver "Decisões"). Sem push, sem
tag: entra na `v1.3.26` junto com 124/126/127, tag só depois da
TASK-125.

Validação final (etapa 3): integração 380/380 (migrations `20260926_0001`
e `20260926_0002` com upgrade → downgrade → upgrade → `alembic check`),
unitária 2767 passando / 0 falhas, cobertura 90,25%, `ruff` limpo,
varredura de segredos limpa, zero chamada real ao César Core na suíte
unitária.

## Problema real (confirmado no código)

- Quando a extração por IA não fechava marca + família + modelo (caso
  típico de produto genérico — "cadeira gamer", "mouse gamer"),
  `resolve_or_learn_product_variant` não gravava nada: na coleta
  seguinte o mesmo título chamava a IA de novo, a cada ciclo, para
  sempre. Com `product_identity_learning_enabled` nascendo `true`
  (2026-09-25), isso virava gasto recorrente de IA.
- O backfill da TASK-123 selecionava `identity_key IS NULL` — produtos
  genéricos nunca saíam do backlog e cada `--apply` pagava IA por eles
  de novo.
- `identity_key` tem índice ÚNICO (`uq_products_identity_key`) e é a
  chave da fusão de duplicatas (`apply_learned_identity`) — um vínculo
  genérico ali (ex.: `cpu:amd`) fundiria todos os processadores AMD não
  resolvidos num único produto, misturando preços e históricos. O
  vínculo genérico precisava morar em outro lugar.
- Pedido de missão vago e IA sem cota davam a MESMA resposta no
  Telegram ("não consegui entender... use /criar_missao"), e o site não
  conferia a descrição da missão de jeito nenhum.

## Decisões do usuário

- **2026-09-25**: "ela não pode não resolver, ela sempre vai ter que
  criar um vínculo de alguma coisa, seja vincular mais de uma
  informação, tipo modelo e família, tipo um processador que não
  resolveu ele vincula a CPU, AMD" — principalmente para produtos
  genéricos (cadeira gamer, mouse gamer), a não ser que o título
  especifique.
- **2026-09-26**:
  1. O vínculo parcial fica no banco. ~~DEV vê que é parcial~~ —
     **revisto no fechamento**: "descarta a parte 4, não precisa, deixa
     só para o sistema ver mesmo" (etapa 4 cancelada; o vínculo parcial
     é só para o sistema — "gráficos e vincular cards").
  2. Título de loja que a IA não consegue nem categorizar: o GG
     registra que não entendeu, **abre o worker, lê a página do produto e
     passa mais detalhes para a IA**. "Não existe" a IA não resolver.
  3. Pedido vago digitado pelo **usuário** ao abrir missão: o GG
     responde que não entendeu e **pede mais descrição** — **nos dois
     canais, site do GG e Telegram**.
  4. Se a IA falhar por **cota esgotada** (nenhum fallback da cascata
     resolveu), o GG avisa o usuário para **tentar abrir a missão mais
     tarde** — **nos dois canais**.
  5. No site, quem decide que o pedido é vago: **"IA confere ao
     criar"** (1 chamada de IA por missão criada no site, mesma
     interpretação do `/criar_missao` do Telegram).

## O que foi feito

### Etapa 1 — vínculo parcial + cache + backlog (`ae43149`)

- Níveis de vínculo: **exato** (`identity_key`, como antes) / **parcial**
  (categoria + marca/família aterradas no título, no MESMO slug da
  identidade exata — "cadeira-gamer", "hyperx"; nunca `identity_key`,
  então nunca funde produtos) / **aguardando página** (nem a categoria
  saiu do título).
- Cache por título para todos os níveis (`product_identity_candidates`,
  status `partial`/`awaiting_page`, migration `20260926_0001`): o mesmo
  título nunca chama a IA de novo. `None` da extração = só falha
  passageira (nunca cacheada).
- Fase C da coleta despacha por tipo (parcial → `apply_partial_link`,
  só campos vazios; exato → `apply_learned_identity`).
- Backlog do backfill = produto sem vínculo nenhum
  (`unlinked_product_criteria()`), mesmo critério no reprocessamento e
  no script (`--count-only`).
- Bug achado e corrigido: no backfill item a item, o `awaiting_page` se
  perdia no `rollback` antes da IA do produto seguinte.

### Etapa 2 — o worker lê a página do produto (`9f0ae2d`)

- O "não entendi" guarda o Product de origem. No início de cada ciclo, o
  worker reserva até `identity_page_read_budget` (padrão 3) títulos, em
  sequência, abre a página de uma Offer do produto pelo provider da loja
  (mesma aba de detalhe Edge/CDP do enriquecimento,
  `CollectionAdapter.read_product_page`) e a IA tenta de novo com título
  + dados do PRÓPRIO produto (JSON-LD, trilha de categorias, descrição —
  nunca o texto da página inteira, que traria "produtos relacionados").
- O grounding anti-invenção passa a valer contra título + esses dados
  (guardados em `page_context`); a decisão nova substitui o
  `awaiting_page` de forma atômica, sem transação aberta durante chamada
  de IA, e vincula o Product (parcial ou exato).
- Nunca um laço: loja sem página de produto (Magalu, por decisão
  antiga) e página que não ajuda viram `unrecognized` (terminal); falha
  passageira tenta de novo após 30 min, no máximo 3 vezes (migration
  `20260926_0002`). Loja com circuito aberto (bloqueando) não é aberta.
- Os "não entendi" gravados pelo backfill (sem navegador) também são
  resolvidos por essa varredura do worker.

### Etapa 3 — "não entendi, descreva melhor" e aviso de cota (Telegram + site)

- Verificador único (`app/intent/mission_description.py`): entendido /
  vago / IA sem resposta depois da cascata — os dois últimos nunca se
  confundem. A regra de "vago" entrou no prompt do `IntentInterpreter`
  (quer comprar mas não diz o quê → `unknown`; nome ou código solto do
  produto continua sendo pedido). A descrição vai crua (a detecção de
  "só o código do modelo" depende do texto original).
- **Telegram** (`/criar_missao`): vago → "🤔 Não entendi o que você quer
  encontrar. Descreva melhor o produto…" e o fluxo continua esperando a
  descrição (prazo renovado, sem repetir o comando); IA sem resposta →
  "⚠️ A cota de IA foi excedida no momento — nenhum provedor de IA
  conseguiu responder. Tente abrir a missão mais tarde".
- **Site** (criar missão): a IA confere a descrição antes de gravar;
  vago → 422 `mission_request_unclear`, IA sem resposta → 503
  `ai_quota_exceeded`; a missão não é criada e o formulário mostra a
  mensagem (frontend inalterado). A transação é fechada antes da chamada
  de IA.
- Limitação conhecida (fora do GG): o César Core não diz ao GG se a
  falha foi cota ou queda — qualquer erro dele já significa que nenhum
  fallback respondeu, por isso a mensagem fala em cota e em "nenhum
  provedor conseguiu responder". Separar os dois exigiria mudar o
  repositório do César Core (TASK própria, se o usuário quiser).

### Etapa 4 — DEV vê "parcial" no detalhe da oferta

**Descartada** pelo usuário em 2026-09-26 ("não precisa, deixa só para o
sistema ver mesmo").

## Checkpoint forçado (2026-09-26, limite de cota da sessão) — histórico

> ✅ **RESOLVIDO na retomada de 2026-09-26** — a Fase C passou a
> despachar o vínculo parcial. Texto original:
>
> **Atenção ao retomar:** o working tree está no meio da etapa 1. A
> coleta ao vivo ainda não sabe tratar o vínculo parcial e quebra se
> rodar agora. O primeiro passo é corrigir isso na Fase C da
> orquestração, antes de rodar testes ou coleta. Tudo que estava verde
> (126, 127, cotas, flag) já está commitado local, nos commits
> `a1cbb9b` → `38fcc0a`.

## Deploy

Ver `docs/operations/prod-deployment-handoff.md`, seção "TASK-128 — o
que muda em PROD": duas migrations novas (head `20260926_0002`), a
varredura de páginas no worker e a criação de missão no site passando a
depender da IA do César Core.

## Fora de escopo

Agrupar/exibir cards ou gráficos por vínculo parcial ("gráficos e
vincular cards") — o dado existe no banco, o uso fica para uma TASK
futura.
