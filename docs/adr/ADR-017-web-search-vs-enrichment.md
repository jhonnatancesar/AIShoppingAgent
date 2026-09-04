# ADR 017 — Search e enriquecimento são capacidades distintas

Aceito por decisão explícita do usuário na TASK-118G.

WebSearchManager escolhe Core no rollout; Firecrawl Search é fallback somente
de indisponibilidade, nunca de vazio ou evidência fraca. Extração Firecrawl de
URLs conhecidas fica fora de WebSearchProvider e preserva o teto de 3 URLs.
Snippet fraco permite scrape, não segunda busca. Nenhuma regra de identidade,
quórum ou preço muda. Uso de Search e scrape é distinguível.

SearXNG certificado no caminho real; `max_results` limita exposição, não
aquisição externa universal. Configuração/evidências: `docs/tasks/TASK-118G.md`.
