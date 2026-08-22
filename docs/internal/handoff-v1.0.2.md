# Handoff — estado real em 2026-08-10 (pós-`v1.0.1` em produção)

Este documento existe para abrir uma nova sessão sem depender do histórico
de chat anterior. Reflete o estado **depois** do commit de consolidação
desta sessão, já publicado em `origin`.

## Estado do Git

| Ref | Valor |
| --- | --- |
| Branch atual | `task-064-ai-provider-cascade` |
| `main` (local) | `a6dbb30` |
| `origin/main` | `a6dbb30` — publicado por autorização explícita do usuário |
| `origin/task-064-ai-provider-cascade` | `a6dbb30` — publicada junto |
| Tag `v1.0.0` | `85b56c6` — **intocada**, marco histórico, não usar para deploy |
| Tag `v1.0.1` | `578dc29` — release corrente, já implantada em produção |

O commit de consolidação `a6dbb30` foi publicado em `origin/main` e em
`origin/task-064-ai-provider-cascade` por autorização explícita do usuário
em sessão posterior à redação original deste documento; nenhuma tag nova
foi criada.

## `v1.0.1` — já está em produção real

Implantada e validada nesta sessão, num Ubuntu Server real (não é mais
"em preparação"):

- Build das imagens, PostgreSQL subido, 26 migrations aplicadas até
  `20260809_0004` (head).
- 7 serviços do `compose.yaml` saudáveis.
- Webhook do Telegram registrado sobre URL HTTPS pública real (túnel
  próprio do operador — a V1 não define domínio/reverse proxy/CI-CD
  permanentes, por escopo).
- Cadastro, criação de senha, login e criação de missão por texto livre
  validados ao vivo. Gemini Flash e fallback Groq respondendo
  corretamente, sem carga alta.
- Proprietário promovido a `DEV` pelo procedimento manual de
  `docs/installation/linux-legacy-setup.md` (seção 9).
- **Achado real corrigido**: containers da aplicação rodam como usuário
  não-root (UID 999 na imagem); `.secrets/` precisou de `chown` para esse
  UID no servidor (bind mount em Linux real aplica permissão POSIX que o
  Docker Desktop no Windows não aplicava). Corrigido só com permissão de
  arquivo — nenhum código, `compose.yaml` ou `Dockerfile` alterado. Ainda
  não documentado como nota permanente em `docs/installation/linux-legacy-setup.md` —
  pendência leve para uma próxima sessão.
- Endereço, credenciais e detalhes específicos do servidor **não estão
  neste repositório** (por design — `docs/installation/secrets.md`) e não estão neste
  handoff.

## `v1.0.0` × `v1.0.1` — não confundir

`v1.0.0` (`85b56c6`) foi criada pela TASK-054 e **nunca foi movida**. Ficou
desatualizada assim que TASK-063 e TASK-064 fecharam depois dela — não
contém nenhuma das duas. Uma auditoria posterior (`DEC-051`) confirmou
isso e decidiu manter `v1.0.0` intocada como marco histórico, publicando
`v1.0.1` como a release corretiva corrente. **Qualquer deploy novo deve
usar `v1.0.1`, nunca `v1.0.0`.**

## `v1.0.2` × V1.2 — documentos separados, não confundir

São **versões diferentes**, cada uma com seu próprio documento (`DEC-059`
corrigiu uma mistura estrutural onde os dois viviam no mesmo arquivo):

- **`v1.0.2`** — release *patch* dentro da V1 (semver `x.y.z`), corretiva,
  registrada em **`docs/internal/v1.0.2-scope.md`**.
- **V1.2** — fase funcional maior, entre a V1 (MVP completo) e a V2,
  registrada em **`docs/internal/v1.2-scope.md`**.

Ordem: `v1.0.1` (atual, em produção) → `v1.0.2` (`docs/internal/v1.0.2-scope.md`) → V1.2
(`docs/internal/v1.2-scope.md`) → V2. Nenhum item de nenhum dos dois virou TASK; nenhum
código foi alterado por nenhum registro.

### `docs/internal/v1.0.2-scope.md` — 5 itens

1. Remover `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` — confirmadas
   sem efeito real via `compose.yaml` (`DEC-052`).
2. Auditar `restart: unless-stopped` serviço por serviço — hoje só
   `collection_worker`/`telegram_notifier` voltam sozinhos após
   reboot/crash (`DEC-052`).
3. Editar missão existente (lojas e/ou preço-alvo) sem recriar (`DEC-057`).
   **Fora do escopo original da `v1.0.2`** (funcionalidade nova, não
   infra) — registrado por pedido explícito.
4. Categorias do `/cadastro` por lista numerada, mesmo padrão já usado
   pelas lojas favoritas, em vez de texto livre (`DEC-055`). Também fora
   do escopo original.
5. Pré-lista de preços encontrados, **sem IA** — mostra só 1 preço por
   loja selecionada, puramente informativa, sem julgamento de "vale a
   pena" (`DEC-058`). Também fora do escopo original. **Fase 1** de uma
   ideia maior cuja fase 2 (com IA) é o item 11 da V1.2.

### `docs/internal/v1.2-scope.md` — 12 itens (itens 1–8 já existiam de sessões anteriores)

> Snapshot histórico de 2026-08-10. A `DEC-080` (2026-08-22) substituiu esta
> composição: lives foram movidas para a V2; Magalu, Mercado Livre e Shopee
> integram o item de novas lojas da V1.2; AliExpress permanece futuro.

Adicionados nesta sessão:

9. Reduzir `PriceObservation` redundante — gravar só quando o estado
   observado da oferta mudar de verdade (preço, moeda, frete,
   disponibilidade, modalidade de envio); nunca apagar/compactar
   histórico já gravado; precisa de um marcador tipo `last_seen_at` fora
   da tabela imutável pra saber que a oferta foi vista de novo sem gerar
   linha redundante (`DEC-053`).
10. Magalu como quinta loja pesquisável, mesma arquitetura de Store
    Provider já aprovada (`DEC-054`).
11. Comparação de menor preço histórico — externo (pesquisa fora do que o
    app já coleta, com fonte/URL/data verificáveis, independente do ano)
    e interno (`price_observations`, já existe) — com comparação
    percentual entre atual/interno/externo, estilo Steam Inventory
    Helper. Esta é a **fase 2** (com IA) da pré-lista — a fase 1, sem IA,
    é o item 5 de `docs/internal/v1.0.2-scope.md`. **Regra sem exceção**: a IA nunca
    inventa preço/data/loja/fonte/URL — só interpreta fatos já
    encontrados por pesquisa externa real ou pelo banco; mesmo princípio
    já em produção desde a TASK-063 e mesmo padrão de template fixo no
    código. Exige capacidade nova de busca externa, API/motor não
    escolhido (`DEC-056`).
12. Pesquisa de ofertas em lives — por enquanto só YouTube e Shopee Live
    (`DEC-056`).

Cupons (item 3, de sessão anterior) foi confirmado como já registrado no
lugar certo — usuário decidiu deixar na V1.2, não mover para V2.

## Decisões desta sessão (`docs/internal/decision-log.md`)

`DEC-051` (manter `v1.0.0` imutável, publicar `v1.0.1`) → `DEC-052`
(registrar `v1.0.2`) → `DEC-053` (`PriceObservation` redundante) →
`DEC-054` (Magalu) → `DEC-055` (categorias numeradas) → `DEC-056` (preço
histórico externo/interno + lives) → `DEC-057` (editar missão) → `DEC-058`
(pré-lista sem IA) → `DEC-059` (separar `v1.0.2` de V1.2 em documentos
distintos).

## O que NÃO foi feito nesta sessão (não assumir)

- Nenhuma TASK nova foi criada ou iniciada para nenhum item de `v1.0.2`
  ou V1.2.
- Nenhum código, `compose.yaml`, `Dockerfile` ou variável de ambiente do
  repositório foi alterado por nenhum dos registros de planejamento.
- O achado de UID/permissão de secrets no servidor **não foi documentado**
  como nota permanente em `docs/installation/linux-legacy-setup.md` ainda — só está
  registrado em `docs/releases/changelog.md`/`docs/internal/project-context.md`.
- Nenhuma tag nova foi criada; o servidor de produção não foi tocado por
  este commit. (O commit de consolidação foi publicado em `origin/main`
  em sessão posterior, por autorização explícita do usuário — ver seção
  "Estado do Git" acima.)

## Prompt sugerido para abrir a próxima sessão

```
Estou retomando o AIShoppingAgent. Leia primeiro AGENTS.md e
docs/internal/handoff-v1.0.2.md para reconstruir o estado exatamente de onde
paramos — não assuma nada não confirmado nesses dois arquivos. Branch
esperada: task-064-ai-provider-cascade. v1.0.1 (578dc29) já está em
produção real; v1.0.0 (85b56c6) é só histórico, nunca usar pra deploy.
v1.0.2 (docs/internal/v1.0.2-scope.md, 5 itens) e V1.2 (docs/internal/v1.2-scope.md, 12 itens) são
versões diferentes, cada uma no seu documento — não confundir. Nenhuma
TASK aberta ainda para nenhum item.
```
