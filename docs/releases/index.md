# Releases

Índice técnico completo de versões, para uso interno. A versão pública e
curada para usuários finais vive no repositório separado
[`jhonnatancesar/AIShoppingAgent-site`](https://github.com/jhonnatancesar/AIShoppingAgent-site)
(`docs/releases.md` daquele repositório), não aqui — este documento pode
conter detalhes técnicos e operacionais que não vão para o site público.

## Versão atual

**v1.0.10b** — 2026-08-21

## Histórico

| Tag | Data | Resumo |
| --- | --- | --- |
| `v1.0.10b` | 2026-08-21 | Healthcheck do Tailscale Funnel recriado para Windows (correção operacional, não código de aplicação). |
| `v1.0.10` | 2026-08-20 | `DEC-070`: Terabyte desativada (bloqueio Cloudflare confirmado); navegação individual por parcelamento removida do `TerabyteProvider`. |
| `v1.0.9` | 2026-08-18 | Causa raiz real do link de oferta não clicável no Telegram: `AISHOPPING_AUTH_PUBLIC_BASE_URL` propagada para o `telegram_notifier`. |
| `v1.0.8` | 2026-08-17 | Correção de formato do link de oferta no Telegram (`parse_mode="HTML"`) — necessária, mas não suficiente (ver `v1.0.9`). |
| `v1.0.7` | 2026-08-17 | `DEC-069`/TASK-089: parcelamento 1:N (`offer_installment_options`) e apresentação `💰`/`💳` no Telegram. |
| `v1.0.6` | 2026-08-13 | Correção do autodeadlock do webhook do Telegram (extensão da TASK-079). |
| `v1.0.5` | 2026-08-11 | Filtragem canônica de produto e confiabilidade da coleta na Pichau. |
| `v1.0.4` | 2026-08-11 | TASK-074: correção de digitação/completude de marca-modelo em `search_query`. |
| `v1.0.3` | 2026-08-11 | TASK-073: bloqueia `/cadastro` para cadastro já completo sem sessão ativa. |
| `v1.0.2` | 2026-08-11 | Release corretiva sobre a `v1.0.1` — 7 itens registrados em `docs/internal/v1.0.2-scope.md`. |
| `v1.0.1` | 2026-08-09 | Release corretiva sobre a `v1.0.0` — inclui TASK-063 e TASK-064. |
| `v1.0.0` | 2026-08-09 | Primeira versão estável do MVP. |

O changelog técnico completo, com detalhes de investigação e validação por
versão, está em [Changelog](changelog.md). O checklist usado para declarar
uma release pronta está em [Release checklist](checklist.md).

## Como consultar uma tag

```powershell
git fetch --tags --prune
git tag -n5
git show <tag>
```

## Como atualizar produção para uma nova tag

Veja [Atualização entre releases](../installation/update.md).
