# TASK-137 — Banco de modelos de produto (lista aberta copiada) para entender o pedido do usuário

Status: **Em andamento (2026-10-03): importador de placa-mãe e RAM do BuildCores pronto (local); pedido->item (passo A) e dúvida com IA (passo C) prontos (local); falta preenchimento automático, família/categoria genérica, Wikidata/iPhone e rodapé.** Pedido do usuário em conversa, depois de a TASK-136
mostrar que missões de placa-mãe, RAM e celulares fora de iPhone/Galaxy S não ganham item de
monitoramento (não entram no funil) por falta de regra de texto.

## Progresso

**Etapas 1 e 2 (placa-mãe e RAM do BuildCores): implementadas (2026-10-03), commit local.**

- Em vez de tabelas novas, reaproveita o catálogo da TASK-132/133 (`product_identity_catalog_entries` e
  `_codes`): migration `20261004_0001` só amplia `source` (`buildcores`, `wikidata`) e guarda `source_ref`
  (id do registro na fonte, para atribuição e atualização). Resolve a pergunta 3 (não duplicar identidade).
- Importador `app/products/identity_catalog_import.py` + `backend/scripts/import_open_catalog.py`
  (`--dry-run`/`--apply`, idempotente, sem rede e sem IA). Cópia da base (git sparse clone das pastas
  `Motherboard` e `RAM`, 56 MB), nunca chamada de API.
- Resultado na carga de 2026-10-03: placas 3.701 lidas, 3.601 aproveitadas, 3.514 entradas; RAM 4.876 lidas,
  4.452 aproveitadas, 4.293 entradas; 13.525 códigos. Descartes explicados por motivo (sem fabricante, nome
  de anúncio comprido, RAM sem part number etc.).
- Qualidade: DDR1 a DDR5 sempre distinguidos (RAM: atributo `type`; placa: `memory_type` + DDR no nome);
  código ou nome usado por marcas diferentes sai dos dois lados; registros da mesma marca que dividem part
  number viram um só, com o nome do registro principal apenas; apelidos de nome da fonte NÃO são usados
  (a fonte mistura a placa com e sem "MAX"); "código" que é nome colado é rejeitado.
- **Casamento estrito por nome** para fontes abertas (`match_catalog`): corrida contígua e completa; depois
  do nome só pode vir fim do título, pontuação ou termo neutro (soquete, DDR, formato). "B650M PRO" nunca
  casa com "B650M PRO-A" ou "B650M PRO WIFI". Sem certeza, não casa e cai na IA.
- Medição com 23 títulos reais de loja: **0 casamentos errados**, 16 corretos, 7 sem casar (marca de
  revisão/região no nome da fonte, ex.: "V3 (REV. 1.5)", "WIFI7", ou placa ausente da fonte). Testes:
  `tests/test_identity_catalog_import.py` (14) e `tests/integration/test_identity_catalog_import.py` (5).

**Passo A (pedido do usuário -> item compartilhado pelo catálogo): implementado (2026-10-03), commit local.**

- Na criação/edição/retomada da missão, se as regras fixas de texto não reconhecem o pedido, o sistema
  procura UM produto no catálogo (`identity_catalog_request.monitoring_identity_from_catalog`, mesmo casamento
  estrito dos anúncios). Achou -> identidade `SPECIFIC` -> a missão usa a coleta compartilhada e o funil.
  Não achou, ambíguo ou nome mais longo -> nada muda (caminho antigo).
- Tenta cada texto do pedido sozinho (`search_query`, `model`, junção), porque a junção repete o nome.
- A busca nas lojas sai do NOME do catálogo (`collection` no `canonical_identity`): placa-mãe = marca +
  menor nome + filtro de título pelo nome; RAM = marca + família + capacidade/tipo/velocidade, sem filtro.
  Itens das regras de texto continuam como antes.
- Registro de categorias: `memory_type` adicionado à placa-mãe (DDR separado também na chave de
  monitoramento). Atributos do catálogo (`capacity-gb`) mapeados para os do registro (`capacity_gb`).
- A relevância continua como hoje (missão sem `requested_identity_key`: a IA decide nas escolhidas pelo
  funil); não exige que o anúncio esteja no catálogo, então não perde oferta por lacuna da fonte.
- Custo medido: carga do catálogo (7.808 entradas) ~360 ms + ~115 ms por pedido; só na criação/edição de
  missão. Sem cache por enquanto.
- Limites: missão de FAMÍLIA ("B650M" sem modelo) e categoria genérica ("cadeira gamer") ainda não entram;
  celular (capacidade obrigatória) fica fora deste passo; RAM por kit exato é rara e sem filtro de título.
- Testes: `tests/test_identity_catalog_request.py` (6) e 3 de integração com missão real
  (`tests/integration/test_identity_catalog_import.py`).

**Passo C (dúvida resolvida pela IA entre candidatos do banco): implementado (2026-10-03), commit local.**

- A IA NÃO roda na criação da missão (transação aberta). Na criação, se o catálogo não acha um produto
  único mas há candidatos parecidos (até 5: mesmo código de modelo, mesma marca quando citada, Jaccard
  >= 0,5), o pedido é registrado em `catalog_request_resolutions` (`pending`, migration `20261005_0001`) e
  a missão nasce no caminho antigo.
- No worker (`CollectionOrchestrator.run_batch`, após a varredura de títulos, até 3 por ciclo, mesma flag
  `product_identity_learning_enabled`), `resolve_pending_requests` manda à IA o pedido + o que ela entendeu
  (`search_query`/`model`) + os candidatos; resposta fechada `{"choice": "A".."E"|null}`; prompt manda
  tratar PRO-A/WIFI/MAX/PLUS/DDR diferente como produto diferente e responder null na dúvida.
- Conferência determinística da escolha (`choice_is_coherent`): código de modelo e marca do pedido têm de
  estar no candidato; incoerente vira `none`.
- `resolved` -> missões sem item com o mesmo pedido são religadas (passam à coleta compartilhada/funil);
  pedido decidido nunca gasta IA de novo (criação consulta a decisão e liga na hora). `none` não repete.
- Falhas reaproveitam a TASK-133: `classify_ai_failure`, espera crescente (`retry_delay`), disjuntor
  compartilhado (`breaker_trip`/`breaker_open_until`), `ai_gate`; falha de conteúdo 5x vira `none`.
- Marca do pedido fora dos candidatos nunca vira dúvida (lista fechada `KNOWN_BRANDS` + marcas do catálogo).
- Testes: `tests/test_identity_catalog_resolution.py` (8) e 4 de integração (fluxo completo com missão real,
  "nenhum", falha de IA com pausa, marca incoerente).

**Correção do passo C (2026-10-03): pedido de FAMÍLIA nunca vira dúvida.** "placa mãe B650" tem 131 placas no
banco (7 marcas): escolher uma à toa prenderia a missão numa placa que ninguém pediu. Agora: (1) pedido que é só
um chipset conhecido do catálogo (`attributes.chipset`) não gera candidato; (2) mais de 5 parecidos também não
(genérico demais). Esses pedidos seguem o caminho antigo até o **passo de família** (próximo): item
`FAMILY` "placa-mãe + chipset", coleta "placa mae b650" com filtro de título e funil escolhendo 1 ou 2.

## Preenchimento automático (a fazer, pedido do usuário 2026-10-03: "preencher a lista sozinha, de forma topíssima")

Quando o pedido ou o anúncio não existe no banco, o sistema completa a lista sozinho, sem entrar lixo:

1. Fontes em ordem: banco (já copiado) -> busca de identidade pelo Core (TASK-133 etapa 3) -> extração por
   IA com grounding (já existe).
2. Resultado novo nasce como candidato; só vira entrada `learned` quando passa em TODOS os portões:
   grounding no título; part number do fabricante OU o mesmo nome visto em 2+ lojas diferentes; sem
   conflito com nome/código de outra entrada; guarda de palavra de edição (`identity_edition`); nunca
   sobre entrada recusada; marca/família/modelo coerentes com o vocabulário da categoria.
3. Candidato que não passa fica em revisão (`scripts/review_identity_catalog.py`), nunca entra direto.
4. Medição: quantos preencheu sozinho, quantos foram para revisão, quantos viraram erro depois.

## Problema

Na criação da missão, o texto do usuário passa só por regras escritas à mão (`_EXTRACTORS` em
`backend/app/products/identity.py`: iPhone, Galaxy S, CPU AMD/Intel, GPU RTX). Para o resto
(`resolve_monitoring_identity` devolve `None`) a missão fica sem item compartilhado e cai no caminho
antigo, onde a IA analisa todos os candidatos de cada loja. A TASK-123 descartou um parser de regex por
categoria; esta TASK não cria regra por categoria: usa **dados**.

## Desenho (decisão do usuário, 2026-10-03)

1. **Copiar listas abertas para o nosso banco** (cópia da base, nunca chamada das APIs deles em
   produção; atualização periódica por novo download).
2. A IA continua montando o texto padrão do pedido (`IntentParameters`: `search_query`, `model`,
   `model_confidence`, `display_query`).
3. O GG **procura no banco** o que se parece com o pedido e vincula (item de monitoramento a partir da
   entrada encontrada, pelo mesmo `resolve_monitoring_identity_for_resolved_product`).
4. **Na dúvida** (vários parecidos ou confiança baixa) manda à IA os **candidatos do banco** junto com o
   que ela entendeu do pedido; a IA só escolhe entre eles, nunca inventa. Sem escolha segura, a missão
   segue como hoje.
5. Substitui a verificação por lojas via navegador do worker (`StoreProductIdentityResolver`, TASK-083)
   nesse papel.
6. Vale para celular, placa-mãe, RAM e o restante que as fontes cobrirem.

## Fontes (conferidas abertas em 2026-10-03)

| Fonte | Cobre | Licença/termos | Decisão |
|---|---|---|---|
| BuildCores OpenDB — https://github.com/buildcores/buildcores-open-db | CPU, GPU, Motherboard, RAM, Storage, PSU, PCCase, Monitor, Laptop, Keyboard, Mouse, Headphones etc.; JSON por componente com fabricante, série, variante, chipset, soquete, formato, tipo de memória e MPN | **ODC-By 1.0**: uso, cópia e adaptação liberados, inclusive comercial, **com atribuição** | Copiar |
| Lista de dispositivos do Google Play — https://storage.googleapis.com/play_public/supported_devices.csv | Android: marca, nome comercial, código, modelo (~54 mil linhas) | **Conferido em 2026-10-03: sem licença aberta.** A página de ajuda é para desenvolvedores checarem compatibilidade e remete aos Termos do Google, que proíbem copiar/distribuir partes dos serviços | **Não copiar** |
| Wikidata (classe "smartphone model", Q19723451) — https://query.wikidata.org/ | ~2.177 modelos; marcas: Samsung ~350, Xiaomi 179, Huawei 169, Sony 140, LG 95, Nokia 75, Apple 29; conferido: iPhone 16, Redmi Note 14, Redmi 13, Galaxy S25/A55 existem; **POCO X7 e Moto G85 não** | **CC0** (domínio público): uso livre, inclusive comercial, sem exigir crédito | Copiar (cobertura parcial; datas de lançamento quase sempre ausentes) |
| iPhone/Apple | Gerações e variantes | Fatos públicos de nomenclatura; montar à mão | Seed próprio |
| GSMArena | Maior acervo de celulares | Termos: só uso pessoal e não comercial; proibido copiar/reproduzir | **Não copiar**; só consulta manual |
| docyx/pc-part-dataset | Dados do PCPartPicker | Raspado de site de terceiros | Não usar (BuildCores cobre) |

## Escopo proposto (ordem)

1. Tabelas novas (modelo canônico, aliases/nomes comerciais, códigos de peça, fonte e data de carga) e
   migration. Atribuição da fonte guardada junto de cada registro.
2. Importadores por fonte (script idempotente, `--dry-run`, relatório de contagem, sem rede em produção
   quando o arquivo já estiver baixado).
3. Busca por parecido no banco (normalização de nomes, tokens, código de peça) com pontuação e limite de
   candidatos; regra de "claro" vs "dúvida".
4. Passo de dúvida com IA: entrada = texto padrão do pedido + candidatos; saída fechada (id do candidato
   ou "nenhum"); registrar falha de IA como na TASK-133 (`ai_failed`).
5. Ligação na criação/edição de missão (`reconcile_mission_monitoring_item`) e na identidade do anúncio
   (catálogo da TASK-133), sem mudar chaves já gravadas.
6. **Rodapé "Fontes de dados" (aprovado pelo usuário, 2026-10-03):** crédito ao BuildCores OpenDB com a licença ODC-By 1.0 e link, e menção ao Wikidata (CC0); entra junto com a primeira carga de dados. Se o sistema for aberto a terceiros, a mesma nota acompanha qualquer cópia do banco.
7. Medição: quantos pedidos resolvem sem IA, com IA e sem resolução, por categoria.

## Perguntas em aberto

1. ~~Termos do CSV do Google Play~~ — fechada em 2026-10-03: não copiar. Falta cobrir o que o Wikidata não tem (POCO, Moto G etc.): seed manual das linhas mais pedidas e aprendizado pelos anúncios reais (catálogo da TASK-133).
2. Cobertura de modelos vendidos só no Brasil (a base do BuildCores é americana).
3. Como unificar com `product_identity_catalog_entries` (TASK-132/133) sem duplicar identidade.
4. Frequência de atualização das cargas.
5. Texto e local da atribuição ODC-By (rodapé do site e documentação).

## Relação com outras TASKs

TASK-133 (catálogo e identidade), TASK-136 (funil antes da IA: depende de a missão ter item), TASK-123
(decisão de não criar parser por regex por categoria).
