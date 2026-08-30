# TASK-110 — Atualizar `docs/architecture/providers.md`

Status: **CONCLUÍDA e publicada em `origin/main` (`36351bd`).** *(Nota
2026-08-30: este status estava registrado como "planejada, não
iniciada" até uma sincronização de documentação corrigir para o estado
real, confirmado por `git log`/`git merge-base --is-ancestor`.)*

## Objetivo

Corrigir `docs/architecture/providers.md`, que ficou desatualizado em duas
frentes independentes, nenhuma delas causada pela TASK-109:

1. A tabela "Fontes selecionáveis na V1" lista só `pichau`/`terabyte`/
   `amazon`/`kabum` e trata Mercado Livre como "Futuro" — mas Mercado Livre
   e Magalu já foram implementados (TASK-104A/TASK-104B) e fazem parte da
   V1 real (ver `CLAUDE.md`, `docs/tasks/TASK-104A.md`,
   `docs/tasks/TASK-104B.md`, `docs/tasks/TASK-105.md`).
2. As linhas finais do documento descrevem Pichau/Terabyte como
   dependentes de Chromium headed + Xvfb dentro de um container Docker —
   isso deixou de ser verdade com o fechamento da TASK-109 (2026-08-23): o
   `collection_worker` roda nativo no Windows e navega via Microsoft Edge
   real por CDP (`Playwright.chromium.connect_over_cdp()`), nunca mais
   lança Chromium gerenciado em nenhum caminho real de coleta. Ver
   `docs/architecture/windows-collection-worker.md` e
   `docs/architecture/playwright.md` (ambos atualizados/criados na
   TASK-109) para a arquitetura de transporte correta atual.

## Contexto / motivação

Achado durante a auditoria de `BrowserSession`/Chromium da TASK-109
(fechamento da migração de browser): ao revisar toda a documentação viva
que menciona Chromium/Xvfb/Docker no contexto de coleta,
`docs/architecture/providers.md` se mostrou desatualizado em relação à
lista real de fontes já implementadas (problema anterior e independente
da TASK-109) além do transporte de navegador (causado pela TASK-109).
Reescrever o documento inteiro sem confirmar a lista exata de fontes e
sem repetir o mesmo processo de verificação já usado nas outras TASKs
estava fora do escopo daquela rodada — por isso ficou como TASK própria.

## Escopo

- Corrigir a tabela de fontes selecionáveis na V1 para refletir a lista
  real (confirmar em `backend/app/collection/providers/stores.py` e no
  roadmap — hoje: `pichau`, `terabyte`, `amazon`, `kabum`, `magalu`,
  `mercadolivre`);
- corrigir a descrição de transporte de navegador (Edge/CDP, não
  Chromium/Xvfb/Docker), sem duplicar o conteúdo já detalhado em
  `docs/architecture/windows-collection-worker.md`/`playwright.md` — só
  referenciar;
- manter o restante do conteúdo que continua correto (classificação de
  vendedor/entrega, `Seller`/`Offer`, seção "Futuro": Shopee/AliExpress).

## Fora de escopo

Qualquer mudança de código, comportamento de provider ou arquitetura de
transporte — esta TASK é só documentação.
