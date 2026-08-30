# TASK-118 — Integrar o OmniRoute como camada central de roteamento de IA e Web Search

Status: **Registrada, sem pré-flight.** Nenhum código, configuração,
secret ou dependência alterados. Pré-flight será solicitado
explicitamente em rodada separada — este documento fixa só o objetivo,
a arquitetura pretendida e os pontos que aquele pré-flight precisa
fechar antes de qualquer implementação.

## Objetivo

Reduzir o acoplamento direto do GG Oferta com múltiplos providers de IA
e mecanismos de Web Search — chaves, quotas, fallback e escolha de
modelo hoje resolvidos internamente — centralizando essa responsabilidade
no OmniRoute. O `AI Provider Manager` (`backend/app/ai_provider/`)
continua sendo a única porta de acesso interna à IA (guardrail já
vigente em `CLAUDE.md`: "Não conectar módulos diretamente a Gemini,
OpenAI ou Claude"), mas passa a poder ser implementado sobre o OmniRoute
em vez de sobre cada provider individualmente — sem o GG Oferta ficar
arquiteturalmente dependente do OmniRoute.

## Arquitetura pretendida

```
IA:
  GG Oferta → AI Provider Manager → OmniRoute → providers/modelos

Web Search:
  GG Oferta → abstração interna de Web Search → OmniRoute → mecanismo de busca

Scraping avançado (quando Web Search não basta):
  GG Oferta → Firecrawl (mantido, uso reduzido e específico)
```

- `AI Provider Manager` continua existindo como abstração interna
  (pode ser simplificado, nunca removido) — o domínio nunca chama um
  provider ou o OmniRoute diretamente.
- Nova abstração interna de Web Search (equivalente ao papel do `AI
  Provider Manager` para IA) — o domínio nunca chama a API do OmniRoute
  diretamente para busca.
- OmniRoute concentra: múltiplas API keys já usadas pelo projeto,
  providers gratuitos/no-auth, providers com free tier, APIs pagas,
  fallback, quota/rate limit, disponibilidade/health, escolha de
  provider/modelo.
- Política de custo: (1) gratuito/no-auth adequado; (2) free tier
  configurado; (3) pago só como último recurso — nunca `auto`
  irrestrito quando a chamada tiver requisito mínimo de
  qualidade/capacidade que o GG Oferta precise declarar.
- Firecrawl permanece só para scraping/extração avançada de página
  específica — nunca mais para Web Search genérica, reduzindo consumo
  de créditos. Remoção do que for exclusivo de Web Search só depois de
  migração e validação real.

## Componentes afetados (previstos, a confirmar no pré-flight)

- `backend/app/ai_provider/` (`manager.py`, providers individuais,
  contratos) — possível simplificação, nunca reescrita completa.
- `backend/app/search/firecrawl.py` e qualquer chamador que hoje use
  Firecrawl só para busca (ex.: `market_research`, TASK-113).
- Nova abstração de Web Search (módulo a definir no pré-flight).
- `backend/app/core/config.py`/secrets — chaves hoje individuais por
  provider podem ser substituídas por configuração de acesso ao
  OmniRoute, mantendo o padrão `*_FILE` já vigente.
- Observabilidade/telemetria de chamadas de IA e busca já existente
  (`app/observability/`) — precisa continuar distinguindo provider,
  modelo, motivo de fallback, rota gratuita vs. paga, e chamadas que
  ainda usaram Firecrawl.

## Critérios de aceite

- `AI Provider Manager` continua sendo o único ponto de acesso interno
  à IA; nenhum módulo de domínio passa a chamar o OmniRoute ou um
  provider diretamente.
- Existe uma abstração interna própria para Web Search, com a mesma
  disciplina de não vazar a API do OmniRoute para o domínio.
- Política de custo (gratuito → free tier → pago) é respeitada e
  observável — dá para saber, por chamada, qual rota foi usada e por
  quê.
- Firecrawl deixa de ser usado para Web Search genérica; uso
  remanescente é só scraping avançado, auditável e reduzido em volume
  real (não só em teoria).
- Fallback seguro quando o OmniRoute está indisponível (mecanismo exato
  a decidir no pré-flight) — nenhuma funcionalidade essencial do GG
  Oferta trava por indisponibilidade externa sem alternativa.
- PROD e DEV continuam com credenciais e dados completamente separados;
  nenhuma chave em código/repositório/log/Telegram.
- Migração incremental: OmniRoute integrado atrás das abstrações
  existentes primeiro, validado com uso real, só depois remoção de
  infraestrutura antiga que se tornar redundante.

## Riscos e pontos a decidir no pré-flight

1. O que exatamente é o OmniRoute (API real, autenticação, contrato de
   requisição/resposta, SDK oficial ou HTTP direto) — nada disso pode
   ser assumido de memória; precisa de documentação oficial atual,
   mesma disciplina já usada para o Cloudflare Access na TASK-117.
2. Mecanismo de fallback quando o OmniRoute está indisponível: cascata
   local de emergência (ex.: a cascata gratuita já vigente hoje,
   `CLAUDE.md`) vs. falha explícita — decisão arquitetural, não
   implementação prematura.
3. Como declarar "requisito mínimo de qualidade/capacidade" por chamada
   sem reintroduzir a complexidade que o OmniRoute deveria absorver.
4. Granularidade da nova abstração de Web Search: módulo novo dedicado
   vs. extensão do `AI Provider Manager` — nenhuma decisão tomada aqui.
5. Como a política gratuita atual do projeto (`CLAUDE.md`: cascata
   Gemini → Groq → OpenRouter para USER/DEV, ADMIN compartilhando a
   mesma política) se reconcilia com o OmniRoute decidindo a rota —
   quem manda em quê precisa ficar explícito antes de codificar.
6. Onde e como as chaves centralizadas no OmniRoute ficam configuradas
   no GG Oferta (só credencial de acesso ao OmniRoute) vs. o que
   eventualmente eventualmente precisa continuar local.
7. Critério exato para permitir/proibir uso de fuzzy/IA em decisões que
   já têm regra determinística no domínio — reafirmar, nunca
   flexibilizar, guardrails já vigentes (ex.: Product Identity Engine).

## Estratégia de migração (alto nível, sem interrupção)

Integrar o OmniRoute atrás das abstrações já existentes primeiro
(`AI Provider Manager` e a nova abstração de Web Search), sem remover
nenhum provider/mecanismo atual. Validar comportamento real (provider
usado, fallback, custo, disponibilidade) antes de qualquer remoção.
Reduzir uso de Firecrawl para Web Search só depois da nova rota provada
em produção. Remoção de infraestrutura antiga (providers individuais,
uso de Firecrawl para busca) é etapa final, condicionada a validação —
nunca simultânea à integração inicial.

## Testes (quando a TASK for implementada)

Focados, não suíte completa/Docker indiscriminado: integração GG Oferta
→ OmniRoute; seleção/fallback; indisponibilidade do OmniRoute/provider;
Web Search via OmniRoute; separação Web Search × Firecrawl; preservação
do contrato das abstrações existentes (`AI Provider Manager` e a nova
abstração de Web Search) para os chamadores atuais.

## Fora de escopo (nesta e na implementação futura, salvo decisão em contrário)

Pré-flight (feito em rodada separada, sob pedido explícito).
Implementação de código, configuração ou secret. Remoção do Firecrawl
como um todo — só o uso dele para Web Search genérica é candidato a
redução. Reescrita completa do `AI Provider Manager`. Qualquer chamada
paga não estritamente necessária. Deploy ou mudança em PROD.
