# TASK-129 — A IA preenche a identidade com o vocabulário padronizado do sistema

Status: **Concluída (2026-09-27), publicada na tag `v1.3.28`** (commit,
tag e push autorizados pelo usuário em 2026-09-27). Deploy em PROD
pendente.

## Problema real (achado no dry-run da PROD, 2026-09-27)

O dry-run do backfill da TASK-123 na PROD mostrou produtos iguais se
separando porque a IA escrevia os campos livremente:

- **categoria**: "motherboard" num lote, "placa-mae" em outro;
- **marca**: a linha no lugar do fabricante ("fury" x "kingston");
- **cor**: fazia parte da variante, então cada cor virava um produto.

O sistema já tinha as listas padronizadas: o registro de 23 categorias
(`_CATEGORY_REGISTRY`), a tabela de grafias `product_identity_aliases`
(TASK-112) e o conhecimento aprovado em `product_identity_candidates`.
Mas a extração por IA não consultava nenhuma delas, e a tabela de
grafias estava vazia. Além disso, lotes de 10 títulos estouravam o
limite do Groq gratuito (~8.000 tokens/min) e cortavam a resposta.

## Decisões do usuário (2026-09-27, em chat)

1. "primeiro vc resolve que a IA veja essas tabelas e passa explicação de
   cada item e o que ela deve preencher". Item genérico ganha categoria
   genérica padronizada ("cadeira gamer"), sem marca ou modelo, a não ser
   que o título traga.
2. "a questão da cor é somente se o usuário especificar no pedido da
   missão": a **cor nunca separa produto**. Quando a missão pede uma cor,
   quem filtra é a classificação de relevância, pelo título real de cada
   oferta.
3. Lotes menores, com pausa e uma nova tentativa.
4. Preencher a tabela de grafias: uma lista inicial, aprendizado
   automático **só com prova** e o resto como sugestão para revisão.

## O que foi feito

### 1. Vocabulário obrigatório no prompt (`app/products/identity_vocabulary.py`)

- `IdentityVocabulary` junta três fontes:
  - as categorias oficiais, com o significado em pt-BR e os atributos de
    cada uma;
  - as categorias genéricas já em uso e as marcas/famílias aprovadas (até
    150);
  - os aliases **ativos**.
- `load_identity_vocabulary` lê tudo em três consultas curtas, antes da
  chamada de IA (nenhuma transação fica aberta durante a IA).
- O prompt (`identity_ai._FIELD_INSTRUCTIONS`) explica cada campo:
  - `category`: sempre do vocabulário;
  - `brand`: o fabricante quando está no título; se o título só traz a
    linha, copia a linha;
  - `family`: a linha completa;
  - `model`: com todos os sufixos;
  - `variant`: nunca a cor;
  - `color`: só em `attributes.color`;
  - produto genérico: sem marca e modelo;
  - nada que não esteja no título.
- **Trava no código** (`canonicalize_fields`): depois do grounding, a
  categoria vira a oficial (sinônimo → oficial, ex.: `placa-mae` →
  `motherboard`) e marca/família passam pelos aliases ativos. Vale na
  identidade exata e no vínculo parcial.
- **Grounding via alias**: o fabricante é aceito quando o título traz
  uma grafia alternativa ativa que leva a ele. Exemplo: "Kingston" em
  "Memória Fury Beast", via `fury -> kingston`. Sem alias, reprova como
  sempre. Nunca inventa.
- **Cor fora da identidade** (`strip_color`): variante que é só cor vira
  vazia, e o atributo de cor sai da chave.

### 2. Lotes de 5, com pausa e nova tentativa

- `scripts/reprocess_unresolved_product_identity.py`: `_BATCH_SIZE = 5`
  (era 10) e `_BATCH_PAUSE_SECONDS = 30`.
- `reprocess_unresolved_products(batch_pause_seconds, sleep)` pausa entre
  lotes. Um lote que falhou inteiro tenta de novo **uma vez**, depois da
  pausa (log `product_identity_ai_batch_retry`).
- Uma rodada de 100 produtos leva cerca de 10 minutos só de pausas.

### 3. Tabela de grafias preenchida

- **Lista inicial** (migration `20260927_0001_seed_identity_aliases`): 47
  grafias linha → fabricante, por exemplo:
  - `rog`/`tuf` → `asus`, `aorus` → `gigabyte`, `wd` → `western-digital`;
  - `iphone` → `apple`, `galaxy` → `samsung`, `legion` → `lenovo`;
  - `fury` → `kingston` (em `ram`/`ssd`).

  Algumas valem para qualquer categoria (`*`). Outras só onde o nome não
  é ambíguo: "Nitro" é Acer em notebook/monitor, mas Sapphire em placa de
  vídeo. A mesma migration aceita o status novo `rejected`.
- **Aprendizado com prova** (`app/products/identity_alias_learning.py`):
  - Condição: mesmo part number do fabricante (ignorando pontuação) já
    aprovado com outra grafia de marca/família.
  - A grafia nova vira alias **ativo**, e o anúncio **herda a
    identidade existente** (`reviewer_note="same_part_number"`). Ele
    não vira um produto novo.
  - Uma sugestão pendente dessa grafia é promovida; uma recusa humana
    nunca é desfeita.
- **Sugestão**: a marca nova é o começo da família de outra marca da
  mesma categoria (ex.: "viper" x patriot/"viper-venom"), nos dois
  sentidos. Nasce `candidate` e não muda nada até ser aprovada.
- **Revisão**: `python -m scripts.review_identity_aliases` com `--list`
  (e `--all`), `--approve ID` e `--reject ID`. O ID aceita os 8
  primeiros caracteres. A recusa fica guardada, para a mesma sugestão
  nunca voltar.
- Aprender grafia nunca derruba a resolução: uma falha vira log
  `product_identity_alias_learning_failed`, e a resolução segue.

`build_alias_mapping` (TASK-112, `monitoring_key`) não tem chamador no
app. Por isso a lista inicial não altera nenhuma `monitoring_key` já
gravada.

## Validação

- Unitária: 2788 passando, 0 falhas, cobertura 90,34%;
  `identity_alias_learning.py` com 100%.
- Integração completa: 393/393, incluindo a migration nova com
  upgrade → downgrade → upgrade → `alembic check`.
- `ruff` limpo.
- Testes novos:
  - `tests/test_product_identity_vocabulary.py`;
  - `tests/test_product_identity_alias_learning.py`;
  - `tests/integration/test_identity_vocabulary.py`;
  - `tests/integration/test_identity_alias_learning.py`.

## Deploy

- Migration `20260927_0001`; head passa a ser **`20260927_0001`**.
- Nenhuma flag nova, nenhuma variável nova.
- Ver `docs/operations/prod-deployment-handoff.md`, seção "Atualização
  para a `v1.3.28`".
- O que já foi aprovado antes com grafia separada continua separado até
  ser reprocessado. O dry-run e o `--apply` da PROD ainda não rodaram com
  esta versão.

## Fora de escopo

- Janelas do Edge aparecendo na tela a cada busca (CDP abre abas em
  primeiro plano): fica como está, por decisão do usuário (2026-09-27).
- Tirar os perfis do Edge de `%TEMP%` antes de religar os workers de
  PROD.
- Tela de revisão de aliases no site: por enquanto, só o script.
