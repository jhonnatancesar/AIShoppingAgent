# TASK-067 — Categorias numeradas no `/cadastro`

Status: **Concluída em 2026-08-10**, lista consolidada aprovada
explicitamente pelo usuário, implementada e validada.

Dependência: nenhuma. Terceira TASK da `v1.0.2` (`docs/internal/v1.0.2-scope.md`, item
4); independente de TASK-065/066 (já concluídas).

## Contexto

`DEC-055` registrou que o passo `preferred_categories` do `/cadastro`
pede texto livre ("quais categorias você mais compra? ex.: games,
móveis"), parseado sem lista fechada — diferente do passo
`favorite_stores`, logo antes no mesmo fluxo, que já usa lista numerada
fixa (`1 Kabum`, `2 Pichau`, `3 Terabyte`, `4 Amazon`, `5 Todas`) com
resposta por números separados por vírgula. O item 4 da `v1.0.2` pede
levantar as categorias reais das 4 lojas selecionáveis da V1 e trocar o
prompt para o mesmo padrão numerado.

## Objetivo

Substituir o texto livre de `preferred_categories` por uma lista numerada
fixa, construída a partir da taxonomia real de categorias das 4 lojas
(Kabum, Pichau, Terabyte, Amazon), no mesmo padrão de `favorite_stores`.

## Escopo

1. Pesquisar a taxonomia real (menu/rodapé de categorias) de Kabum,
   Pichau, Terabyte e Amazon.com.br.
2. Consolidar uma lista numerada de categorias, sem inventar nomes que
   não existam em pelo menos uma das lojas reais.
3. Trocar o prompt e o parser de `preferred_categories`
   (`backend/app/users/registration.py`) para o padrão numerado, com
   múltipla escolha por vírgula (mesmo padrão de `_parse_stores`).
4. Não alterar `favorite_stores` nem qualquer outro passo do cadastro.
5. Não alterar o schema do banco (`preferred_categories` já é
   `ARRAY(String(64))`, suficiente para os slugs).
6. Não tocar em produção.
7. Não criar tag `v1.0.2`.
8. Não iniciar a TASK-068 automaticamente.

## Pesquisa de taxonomia real (2026-08-10)

Visitei ao vivo (Browser) a home e o rodapé/menu de categorias de cada
loja e extraí a navegação real exibida (sem inventar nomes):

- **Kabum** (`kabum.com.br`) — menu principal + rodapé: Hardware,
  Periféricos, Computadores, Games, Celular & Smartphone, Notebook, TV,
  Áudio, Projetores, Espaço Gamer, Escritório, Casa Inteligente, Tablets/
  iPads/E-readers, Câmeras e Drones, Energia, Conectividade,
  Eletrodomésticos, Eletroportáteis, Ar e Ventilação, Segurança,
  Automação, Telefonia Fixa, Ferramentas, Geek, Monitores, Placa de
  Vídeo, Teclado e Mouse, Processadores, Controles e Games.
- **Pichau** (`pichau.com.br`) — bloco "NA PICHAU TEM!": Hardware,
  Periféricos, Computadores, Kit Upgrade, Monitores, Cadeiras e Mesas
  Gamer e Escritório, Eletronicos, Notebooks e Portáteis, Mochilas,
  Vestuario, Video Games, Redes e Wireless, Casa Inteligente, Casa e
  Lazer, Energético, Realidade Virtual, Openbox, Pets.
- **Terabyte** (`terabyteshop.com.br`) — rodapé (sitemap completo):
  PC Gamer, Kit Upgrade, Hardware (Processadores, Placa de Vídeo, Placa
  Mãe, Memórias, HD/SSD, Placa de Som), Monitores, Gabinetes, Simulador,
  Fontes, Periféricos, Conectividade, Armazenamento, Cabos e Adaptadores,
  Notebooks, Cadeiras, Refrigeração, Impressão 3D, Mesa Gamer, Rede,
  LifeStyle (Mochilas, Powerbank, Smartwatch), Video Games, Home e Eletro,
  Geek, Segurança, Diversos.
- **Amazon.com.br** (`amazon.com.br`) — barra de navegação principal
  (real, via `read_page`): Games, Eletrônicos, Casa, Brinquedos e Jogos,
  Computadores, Beleza, Amazon Moda, Alimentos e Bebidas, Ferramentas e
  Construção, Bebês, Cuidados Pessoais, Automotivos, Pet Shop, Livros,
  eBooks Kindle.

### Critério de consolidação

Kabum, Pichau e Terabyte são lojas especializadas em hardware/PC
gamer/periféricos; Amazon é um marketplace generalista que também vende
essas categorias mas cobre muito mais (livros, moda, beleza, alimentos,
bebês, automotivo, pet shop). Incluir cegamente todo o catálogo da Amazon
geraria uma lista de "categorias preferidas" onde metade das opções nunca
teria resultado nas outras 3 lojas selecionáveis da V1 — o objetivo do
campo é ajudar a interpretar o perfil de compra do usuário dentro do que
essas 4 fontes realmente vendem em comum, não replicar o catálogo inteiro
da Amazon.

Critério aplicado: uma categoria entra na lista consolidada se aparece,
com o mesmo sentido, em pelo menos **2 das 4 lojas** (ou é uma categoria
claramente vendida pela Amazon dentro de "Eletrônicos"/"Computadores"
mesmo sem aparecer como item isolado na barra principal, quando também
aparece explicitamente em pelo menos uma das outras três). Isso exclui
automaticamente as categorias exclusivas de mercado geral da Amazon
(Livros, Moda, Beleza, Alimentos, Bebês, Automotivos, Pet Shop) e as
exclusivas hiper-nichadas de uma única loja (ex.: "Realidade Virtual" só
na Pichau, "Impressão 3D"/"Simulador" só na Terabyte, "Telefonia Fixa" só
na Kabum) — critério objetivo, não escolha arbitrária.

## Lista consolidada proposta

| # | Categoria | Slug | Presente em |
| --- | --- | --- | --- |
| 1 | Hardware / Componentes de PC (placas de vídeo, processadores, placas-mãe, memórias, fontes, armazenamento) | `hardware` | Kabum, Pichau, Terabyte |
| 2 | Periféricos (teclado, mouse, headset, webcam) | `perifericos` | Kabum, Pichau, Terabyte |
| 3 | Computadores / PC Gamer montado | `computadores` | Kabum, Pichau, Terabyte, Amazon |
| 4 | Notebooks | `notebooks` | Kabum, Pichau, Terabyte |
| 5 | Monitores | `monitores` | Kabum, Pichau, Terabyte |
| 6 | Celulares e Smartphones | `celulares` | Kabum, Amazon |
| 7 | TV, Áudio e Vídeo | `tv_audio` | Kabum, Amazon (Eletrônicos) |
| 8 | Video Games e Consoles | `video_games` | Kabum, Pichau, Terabyte, Amazon |
| 9 | Cadeiras e Móveis Gamer/Escritório | `cadeiras_moveis` | Kabum, Pichau, Terabyte |
| 10 | Casa Inteligente e Automação | `casa_inteligente` | Kabum, Pichau |
| 11 | Eletrodomésticos e Eletroportáteis | `eletrodomesticos` | Kabum, Terabyte, Amazon (Casa) |
| 12 | Câmeras e Drones | `cameras_drones` | Kabum, Amazon (Eletrônicos) |
| 13 | Redes e Conectividade | `redes_conectividade` | Kabum, Pichau, Terabyte |
| 14 | Segurança (câmeras, alarmes) | `seguranca` | Kabum, Terabyte |
| 15 | Geek e Colecionáveis | `geek_colecionaveis` | Kabum, Terabyte |
| 16 | Todas as categorias | `todas` (equivalente a nenhum filtro) | — (opção agregadora, mesmo padrão do "5 - Todas" de `favorite_stores`) |

Resposta por números separados por vírgula (ex.: `1,4,8`), `16` para
"todas", `"pular"` continua válido para deixar em branco — mesmo padrão
de `favorite_stores`.

## Decisão aprovada (2026-08-10)

O usuário aprovou a lista consolidada de 15 categorias + "Todas" sem
alterações.

## Implementação (2026-08-10)

- **`backend/app/users/registration.py`**: `_NUMBERED_CATEGORIES` (mapa
  `"1"`-`"15"` → slug) e `_ALL_CATEGORIES` (`"16"`/`todo`/`todos`/`toda`/
  `todas`) adicionados, no mesmo padrão de `_NUMBERED_STORES`/
  `_ALL_STORES`. O prompt de `preferred_categories` virou lista numerada
  fixa (mesmo texto/formato do prompt de `favorite_stores`).
  `_parse_categories` reescrita para tokenizar por vírgula/espaço,
  resolver números para slugs e rejeitar entradas sem nenhuma
  correspondência conhecida — removidos os limites antigos de "até 20
  itens livres de até 64 caracteres" (`_MAX_CATEGORIES`/
  `_MAX_CATEGORY_LENGTH`), que não fazem mais sentido com vocabulário
  fechado. `favorite_stores` e os demais passos do cadastro não foram
  tocados.
- **`docs/architecture/users.md`**: descrição de `preferred_categories` corrigida de
  "lista livre" para a lista fechada de 15 slugs; parágrafo do `/cadastro`
  atualizado com o novo padrão numerado.
- **Testes** (`tests/test_user_registration.py`,
  `tests/test_telegram_router.py`): os três testes que validavam texto
  livre (`"games, moveis, livros"` etc.) foram substituídos/reescritos
  para o formato numerado — inclusive um teste novo cobrindo o prompt
  numerado completo, um parametrizado (números individuais, múltiplos,
  `16`, `"todas"`) e um de rejeição para entrada sem correspondência
  conhecida, mesmo padrão dos testes equivalentes de `favorite_stores`.
  `tests/test_privacy.py` e `backend/scripts/validate_privacy.py` já
  usavam `["hardware"]` como fixture — compatível sem alteração.
  `tests/test_telegram_preferences.py` seta o atributo diretamente (sem
  passar pelo parser), não afetado.

## Validação (2026-08-10)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **756 testes (90,65% cobertura; `registration.py` a
  100%)**, migration head `20260809_0004`, **14 testes de integração
  PostgreSQL reais** — todos aprovados (`Pipeline local aprovado.`).
- **Fluxo completo real do `/cadastro`**
  (`test_registration_full_flow_completes_and_clears_step`,
  `tests/test_telegram_router.py`): dirige o handler de webhook real
  (`receive_telegram_webhook`) pelos 4 passos em sequência
  (`username`→`email`→`favorite_stores`→`preferred_categories`) até a
  conclusão, só com o envio HTTP ao Telegram mockado — cobre o código de
  produção real do roteador e do parser, não apenas a função isolada.
- **Sem round-trip ao vivo contra a API real do Telegram**: diferente de
  TASK-053/063/064 (mudanças de comportamento maiores), esta TASK altera
  só o texto do prompt e o parser de um passo do cadastro — a mecânica do
  webhook/roteador está intocada e já validada em produção real
  (`v1.0.1`). Repetir um teste ao vivo específico só para uma mudança de
  vocabulário fechado seria validação desproporcional ao risco; a
  cobertura unitária (100% em `registration.py`) mais o teste de fluxo
  completo acima são consideradas suficientes.
- **`preferred_categories` continua sem consumidor além de metadado**:
  confirmado por auditoria (só aparece em
  `backend/app/privacy/service.py` para desidentificação) — nenhum
  filtro/pontuação de coleta ou recomendação foi alterado.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Encerramento

Concluída em 2026-08-10. `/cadastro` pede categorias por lista numerada
fixa (15 categorias reais + "Todas"), mesmo padrão de `favorite_stores`.
`preferred_categories` continua metadado informativo, sem novo
consumidor. Produção da `v1.0.1` intocada; TASK-068 não iniciada.

## Fora do escopo desta TASK

- Usar `preferred_categories` para filtrar/pontuar resultados de coleta
  ou recomendação — hoje é só metadado informativo, sem consumidor
  downstream (confirmado por auditoria: só aparece em
  `backend/app/privacy/service.py` para desidentificação); esta TASK não
  muda isso.
- Qualquer alteração em `favorite_stores` ou outros passos do cadastro.
- TASK-068/TASK-069.
