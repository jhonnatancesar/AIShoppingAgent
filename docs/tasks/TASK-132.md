# TASK-132 — Anúncio que muda de produto (troca automática de cadastro) e catálogo de nomenclaturas

Status: **Registrada (2026-09-28), planejada, NÃO iniciada.** Pedido do
usuário em conversa. Duas partes independentes, que podem virar duas TASKs
na hora de executar. Nenhuma das duas bloqueia o deploy da `v1.3.30`.

## Parte A — Detectar que o anúncio passou a descrever outro produto

**Origem:** Offer `196b2fa2` (Amazon `B0D8WH9NG3`, GIGABYTE B550 AORUS Elite
AX V3). Na única coleta que existe (28/08) o título era "B550 AORUS Elite AX
V3"; hoje o mesmo link abre a "B550I AORUS PRO AX" (ITX). Anúncio da Amazon
com versões: a versão padrão mudou.

**Requisito do usuário (2026-09-28):** "o GG tem que detectar que a placa
mudou e trocar o nome do item no banco, preservando o que já tem".

**Hoje:** a identidade é resolvida uma vez, quando a Offer é ligada ao
Product. Uma observação nova com título de outro produto é só gravada no
histórico do Product antigo, sem nenhum aviso.

**Proposta (a validar no desenho):**
1. Detectar: a cada coleta, comparar o título novo com o último; só se mudou
   (hash do título normalizado) resolver a identidade pelo caminho normal
   (extrator determinístico, cache, reuso, IA só se preciso). Cor não conta
   (a identidade já a ignora). Divergência da `identity_key` em 2 coletas
   seguidas = anúncio mudou de produto (histerese, porque anúncio com
   versões costuma alternar a versão padrão).
2. Agir: criar uma Offer nova (mesmo link) já no Product certo e marcar a
   antiga como substituída (`superseded_by_id`/`superseded_at`, mecanismo que
   já existe em `offers.models`, hoje usado só para troca de vendedor em
   `_supersede_old_unattributed_offer`). O histórico antigo fica com o
   produto antigo; o novo começa limpo no produto novo.
3. Avisar: registrar um evento para o DEV ver a troca.

**Por que não só renomear o item:** o histórico de preço de dois produtos
diferentes ficaria numa linha só (salto falso no gráfico, alerta de "melhor
preço" errado). Separar preserva tudo o que já existe.

**A definir:** se dá para existir uma segunda Offer com a mesma loja e o mesmo
`external_id` (conferir as restrições de unicidade); o que fazer com
`MissionProductAlertState` e `mission_offer_relevance` da Offer antiga;
custo de IA (só quando o título muda); anúncio que alterna entre A e B.

**Enquanto isso:** o único caso conhecido (`196b2fa2`) se confere à mão depois
do primeiro ciclo de coleta em PROD.

## Parte B — Catálogo ("mini banco") de nomenclaturas

**Pedido do usuário (2026-09-28):** um mini banco para ir guardando as
nomenclaturas de placas, CPUs, celulares etc., nome e part number, seguindo a
tabela de família/produto. **A mensagem veio cortada ("…e pr")**: a
finalidade completa fica para o usuário completar antes de desenhar.

**O que já existe:** `product_identity_aliases` (grafias de marca/família),
`product_identity_candidates` (identidades aprendidas, com part number),
casamento por part number ignorando pontuação
(`values_match_ignoring_punctuation`), extratores determinísticos (iPhone,
Galaxy S, Ryzen, Intel Core, RTX) e o vocabulário da TASK-129.

**O que falta (hipótese):** uma camada curada e aprovada pelo usuário,
consultada ANTES da IA, cuja chave seja o part number exato ou o nome
normalizado e cujo valor seja (categoria, marca, família, modelo, variante),
com fonte e aprovação. Alimentada pelos Products já aprovados e por revisão
(precedente: `scripts/review_identity_aliases.py`).

**Ganhos esperados:** evita "Pro × Pro Max", "MAX × sem MAX" e RAM lida como
CPU; economiza cota de IA; dá um lugar para regras que hoje são casos soltos,
como códigos de peça que embutem cor (Corsair `Z40` × `C40`, Kingston
`BWE2` × `BBE2`).

**Cuidado conhecido (TASK-131, causa C):** o extrator grava o atributo como
`storage_gb` e o construtor como `storage-gb`, então a `identity_key` sai
diferente. O catálogo precisa gerar a identidade pelo mesmo caminho do
extrator, sem mudar as chaves já gravadas.

**Perguntas em aberto:** finalidade completa; tabela nova ou reaproveitar
`product_identity_candidates`; quem aprova; como popular a primeira vez;
quais categorias entram primeiro.
