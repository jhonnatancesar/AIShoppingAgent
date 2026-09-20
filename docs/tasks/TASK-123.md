# TASK-123 — Placas-mãe nunca ganharam identidade global de produto

Status: **Registrada, não iniciada.** Achado real em PROD em 2026-09-20
(auditoria pós-deploy da `v1.3.15`): o usuário perguntou se o gap de
identidade das placas-mãe já tinha sido resolvido junto com a rodada de
identidade global de produto (checkpoints 3-11) -- verificado
diretamente no código e no banco de PROD: **não foi**.

## Preflight (investigação feita antes de registrar esta TASK)

- `backend/app/products/identity.py:135` (`_iphone`, `_galaxy_s`) e
  `:771` (`_EXTRACTORS = (*_EXTRACTORS, _cpu, _intel_cpu, _gpu)`) --
  o motor determinístico só reconhece 5 padrões. Nunca existiu um
  extractor de placa-mãe.
- Confirmado ao vivo no banco de PROD (container `api`): dezenas de
  placas-mãe reais (ASUS TUF/ROG, MSI MAG/PRO, Gigabyte AORUS, ASRock --
  B550/X870/X870E) com `Product.category`/`identity_key` **NULL**.
- **O que já foi corrigido, não confundir com esta TASK:** o bug de
  placa-mãe sendo classificada incorretamente como `category=cpu`
  (título mencionando "suporta processadores Ryzen/Intel" como
  compatibilidade). Guard `_mentions_cpu_only_as_compatibility`
  (`identity.py:590-594`), usa `_NON_CPU_PRODUCT_CATEGORY`
  (`identity.py:587`, regex `PLACA.?MAE|MOTHERBOARD|MAINBOARD`) -- já
  em produção, confirmado: os produtos com `category=cpu` hoje são
  todos CPUs reais, nenhuma placa-mãe entre eles. **Esta TASK não mexe
  nesse guard, que já funciona.**
- **Ontologia já registrada, sem extractor correspondente:**
  `_CATEGORY_REGISTRY` (`identity.py:485-492`) já declara
  `CategoryDefinition("motherboard", (AttributeDefinition("socket",
  blocking=True), AttributeDefinition("chipset"),
  AttributeDefinition("form_factor")))` -- inerte hoje (comentário do
  próprio arquivo, `:419-423`: categoria sem extractor nunca é
  devolvida por `_parse`). O schema de atributos (`socket`/`chipset`/
  `form_factor`) já é o desenho correto, só falta o extractor de texto.
- **Mecanismo que resolveria isso de forma genérica, já implementado
  mas nunca ligado:** aprendizado assistido por IA
  (`product_identity_candidates`, checkpoints 3-11, já publicado em
  `v1.3.15`) -- atrás da flag `product_identity_learning_enabled`,
  default `False`, confirmado ainda `False` em PROD. Tabela
  `product_identity_candidates` com 0 linhas -- nunca rodou pra nenhum
  produto. **Ativar essa flag é decisão de produto/custo (liga chamada
  de IA real no caminho de coleta), não faz parte desta TASK** -- fica
  registrada aqui só como mecanismo alternativo/complementar, a
  decisão cabe ao usuário separadamente.
- **Títulos reais já usados em teste no repositório** (referência para
  calibrar o extractor, `tests/integration/test_product_identity_learning.py:316-434`):
  `"Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"`,
  `"ASUS TUF GAMING B650-PLUS (WI-FI) DDR5 - Chipset AMD B650, Socket AM5"`.

## Objetivo

Adicionar um extractor determinístico `_motherboard` a
`backend/app/products/identity.py`, registrado em `_EXTRACTORS` (mesmo
padrão aditivo de `_cpu`/`_intel_cpu`/`_gpu`, `identity.py:768-771` --
nunca tocar a tupla já existente, só estender). Desenho proposto
(decisão de detalhe de implementação cabe a quem for codar):

- **Gatilho:** título menciona explicitamente `PLACA.?MAE|MOTHERBOARD|
  MAINBOARD` (reaproveitar/espelhar `_NON_CPU_PRODUCT_CATEGORY`) --
  fail-closed, nunca inferir placa-mãe só pela presença de um código de
  chipset isolado (um título de RAM/cooler/fonte pode citar chipset
  como compatibilidade).
- **Guard espelhado (na direção oposta do guard de CPU):** ao contrário
  de CPU (que nunca legitimamente cita "placa-mãe" no próprio título),
  uma placa-mãe real legitimamente cita frase de compatibilidade com
  CPU ("suporta processadores Ryzen 9000") -- então **não** basta
  reaproveitar `_mentions_cpu_only_as_compatibility` com a mesma regra
  OR. Precisa de uma regra AND própria: só rejeitar quando a frase de
  compatibilidade (`_CPU_COMPATIBILITY_PHRASE`) aparece **junto** com
  uma categoria de produto claramente diferente (memória/cooler/fonte/
  gabinete citando "para placas-mãe X" como especificação, não o
  produto em si) -- evita falso positivo em "Memória DDR5 para
  Placas-mãe AM5" sem rejeitar título legítimo de placa-mãe que cita
  compatibilidade de CPU.
- **Chipset:** lista fechada (fail-closed, mesmo espírito de
  `_GPU_BOARD_BRANDS`) cobrindo AM4 (A320/B350/B450/X370/X470/A520/
  B550/X570), AM5 (A620/B650/B650E/X670/X670E/B850/X870/X870E), Intel
  LGA1700 (H610/B660/B760/H670/Z690/Z790) e LGA1851 (H810/B860/Z890).
  Alternação ordenada do mais específico pro menos específico
  (`B650E` antes de `B650`) para não capturar o prefixo errado.
- **Socket:** sempre **derivado do chipset** (nunca do texto solto),
  mesmo princípio de `_ryzen_family` (tier de CPU derivado do código,
  nunca de texto ao redor) -- a correspondência chipset→socket é
  conhecimento público estável, nunca ambíguo.
- **Marca (fabricante):** lista fechada (ASUS, MSI, GIGABYTE, ASROCK,
  BIOSTAR), mesmo padrão de `_GPU_BOARD_BRANDS`.
- **`family`/`model`:** `family` = chipset (agrupa por geração/chipset,
  útil pra busca genérica). `model` = captura deliberadamente ampla do
  trecho após o chipset (não tenta separar "linha comercial" de
  "sufixo" com precisão cirúrgica) -- **melhor sub-agrupar de menos**
  (título de uma mesma placa vindo de duas lojas com formatação
  diferente cai na zona cinzenta + árbitro de IA já existentes,
  `identity_learning.py`) **do que fundir duas placas físicas
  diferentes por engano** (ex.: "B650-PLUS" vs "B650-E" tratadas como
  o mesmo produto). Mesmo princípio de design já usado pelo VRAM da GPU
  (`_gpu_vram`, `identity.py:720-732`): ausente/impreciso vira `ANY`
  ou cai pra zona cinzenta, nunca inventado nem fundido.

## Escopo

Extractor determinístico novo (`_motherboard`) + registro em
`_EXTRACTORS`. Nenhuma mudança em `_CATEGORY_REGISTRY` (schema já
correto, só o extractor falta). Nenhuma mudança nos guards de CPU já
existentes (`_mentions_cpu_only_as_compatibility`,
`_NON_CPU_PRODUCT_CATEGORY`) -- só reaproveitar/referenciar.

## Fora de escopo

Ativar `product_identity_learning_enabled` (decisão de produto/custo
separada, não desta TASK). Qualquer categoria além de placa-mãe (RAM,
fonte, gabinete, cooler -- mesmo gap provável, TASKs próprias se
priorizadas). Reparo retroativo de produtos já existentes com
`category`/`identity_key` NULL no banco (script de reparo próprio,
mesmo padrão de `scripts/repair_cpu_identity_misclassification.py`, se
decidido depois que o extractor estiver validado).

## Critério de validação futuro

Testes unitários cobrindo: os dois títulos reais citados acima
(mesma placa, duas lojas, formatação diferente) convergem para o mesmo
`family_key` (chipset B650 + AM5), mesmo que não convirjam
automaticamente no `identity_key` completo (aceitável -- zona cinzenta
resolve); título de RAM/cooler citando "placas-mãe AM5" como
compatibilidade NÃO é reconhecido como placa-mãe (guard AND); título de
CPU/GPU legítimo continua funcionando sem regressão (suíte de
`test_product_identity_engine.py` inteira, 0 falhas). Validação real:
reparo de uma amostra de placas-mãe reais já no banco de PROD
(re-rodar `resolve_product_variant` contra os títulos reais existentes)
confirmando que passam a ganhar `category=motherboard` com
`chipset`/`socket` corretos.
