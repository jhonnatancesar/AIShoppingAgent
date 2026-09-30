# TASK-134 — GG entende os erros de IA do Core e pausa até o dia virar

Status: **Registrada (2026-09-30), planejada, NÃO iniciada.** Depende da TASK-134 do
César Core (`docs/task-134-ai-errors-and-daily-pause.md` no repositório
`C:\cesar-core`). Só começa depois que a TASK-133 (GG) estiver fechada e o Core tiver os
novos códigos prontos e validados.

## Objetivo

Hoje o GG transforma qualquer falha do Core num único erro genérico e trata tudo com a
mesma pausa curta (disjuntor da TASK-133). Esta TASK faz o GG entender cada código do
Core e agir de forma diferente para cada um.

## Escopo proposto

1. **Ler os códigos do Core** (`ai_daily_quota_exhausted`, `ai_rate_limited`,
   `ai_upstream_unavailable`, `ai_upstream_timeout`, `ai_request_denied`,
   `ai_invalid_response`) em vez do erro genérico, guardando `reset_at` e
   `retry_after_seconds`.
2. **Pausar até o reset:** cota do dia esgotada pausa a IA de identidade (e as demais
   chamadas de IA do GG) até `reset_at`; limite por minuto espera o tempo sugerido;
   indisponível e tempo esgotado seguem a pausa curta crescente. O estado fica no
   disjuntor compartilhado (tabela) já criado na TASK-133.
3. **Durante a pausa, o GG segue analisando o que tem no banco:** coleta de preço,
   histórico e referências externas já salvas. **Antes de codar, conferir quais partes
   da coleta hoje dependem de IA** (relevância da oferta, nome de exibição, referência
   de mercado, cupom, identidade) e o que acontece com cada uma quando a IA está fora.
4. **As três correções do disjuntor** já aprovadas: gravar o aviso numa transação
   própria e curta, ler dentro de um savepoint (erro de leitura nunca trava a coleta) e
   no máximo 2 chamadas de identidade ao mesmo tempo por processo.
5. **Relatório** por tipo de erro e por etapa que resolveu (título, catálogo, busca,
   página, IA).

## Fora de escopo

O contador diário por provedor (opção A do Core) e qualquer mudança no Core.

## Perguntas em aberto

1. O que da coleta depende de IA e o que fica parado na pausa (conferência pendente).
2. Se a pausa por cota do dia vale para todas as chamadas de IA do GG ou só para
   identidade.
