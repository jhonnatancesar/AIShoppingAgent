# AI Provider Manager

Todo acesso a IA passa por `AIProviderManager.generate`. Módulos de domínio não
podem importar SDKs nem chamar Gemini, OpenAI, Claude ou qualquer outro provedor
diretamente. `AIProvider` é uma porta interna usada apenas pelo manager.

## Contrato

- `AIRequest` possui UUID de correlação, perfil existente, propósito estável em
  `snake_case`, uma ou mais mensagens tipadas e horário UTC.
- Mensagens aceitam os papéis `system`, `user` e `assistant`.
- `AIResponse` mantém a correlação e informa provedor, modelo, conteúdo e horário.
- Requisições e respostas são imutáveis; conteúdo vazio, horário sem fuso e
  resposta de outra requisição são rejeitados.
- Erros de provedor expõem somente código sanitizado, indicação de nova tentativa
  e, para quota, o horário UTC de reset quando o provedor o informar. Respostas
  brutas, prompts, tokens e credenciais não pertencem ao erro.

Perfis previstos:

- `USER`: cadeia exclusivamente gratuita: `gemini-3.6-flash`, Groq
  `openai/gpt-oss-120b` e OpenRouter `openrouter/free`. Nenhuma rota paga é
  elegível para esse manager.
- `ADMIN`: papel histórico do domínio que compartilha a mesma política
  gratuita do DEV — `gemini-3.6-flash` (o mesmo
  modelo Gemini Flash do perfil `USER`, via `Settings.gemini_model`, sobre a
  chave dedicada `AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`) e, se configurado, o
  Groq (`GroqProvider`, TASK-059, opcional) e OpenRouter `openrouter/free`
  como fallbacks de disponibilidade. **Nenhum nível
  Gemini Pro/preview participa da cascata (TASK-064/DEC-050)** — a
  perfil recebe provider pago.
- `DEV`: em chamadas normais usa a mesma cadeia exclusivamente gratuita do
  USER. Quando `require_search_grounding=True`, a aplicação pesquisa primeiro
  pela Firecrawl Search API v2 direta, exige ao menos uma fonte web válida e
  então envia o contexto não confiável à mesma cascata gratuita. Falha ou
  resultado vazio da pesquisa fecha o fluxo antes de qualquer LLM.
- `PLUS`: futuro; não existe no contrato nem na V1.

O perfil USER traduz mensagens para o contrato de cada provider e
após cada chamada e converte quota, indisponibilidade, autenticação e rejeição em
erros sanitizados. Ao atingir o limite, `AIProviderQuotaExceeded` permite ao canal
informar que o usuário tente novamente mais tarde. USER e DEV nunca recebem
fallback pago.

`GroqProvider` (TASK-059) chama a API compatível com OpenAI do Groq via
`httpx`, traduzindo os papéis `system`/`user`/`assistant` diretamente (sem a
fusão de mensagens de sistema exigida pelo Gemini) e convertendo erros para
os mesmos tipos sanitizados do `GeminiProvider`, incluindo `quota_reset_at`
a partir do cabeçalho `retry-after` quando informado.

Cada tentativa gera `ai_provider_attempt` com UUID de correlação, perfil,
finalidade, provedor, modelo, resultado, indicador de fallback e reset de quota
quando conhecido. O evento não contém mensagens nem conteúdo da resposta. O
objeto `AIQuotaNotice` permite aos futuros canais informar o horário de retomada
ou declarar explicitamente que ele é desconhecido, sem inventar um prazo.

Em 2026-08-02, a implementação foi validada com o SDK 2.16.0 e uma chamada
autenticada real pelo `AIProviderManager`, usando `gemini-3.6-flash`, com resposta
não vazia e o conteúdo esperado. O caminho real de falha também foi validado com
uma chave descartável inválida, confirmando erro sanitizado sem exposição do valor
ou da resposta bruta. A telemetria foi validada novamente no fluxo ADMIN/DEV real:
o premium retornou `429` com reset e o Flash gratuito concluiu o fallback. Nenhuma
credencial é versionada.

Em 2026-08-08 (TASK-059), o `GroqProvider` foi validado com uma chamada real
autenticada contra a API do Groq (modelo posteriormente substituído por
`openai/gpt-oss-120b`), e a cascata de
3 níveis do `AdminDevAIProviderManager` foi validada de ponta a ponta com um
premium real forçado a falhar por cota, confirmando que o Groq real é
alcançado e responde. As 19 mensagens diversas de validação do
`IntentInterpreter` (TASK-057) foram classificadas corretamente via essa
cascata, sem tocar na cota compartilhada do perfil `USER`.

Em 2026-08-08 (TASK-060), o webhook do Telegram passou a escolher qual
adaptador/perfil usar a partir do `User.role` já resolvido — `USER` sempre
fala com o Gemini gratuito, `ADMIN`/`DEV` sempre com a cascata do
`AdminDevAIProviderManager`. Validado de ponta a ponta contra o Telegram
real: uma mensagem de um usuário `ADMIN` real acionou o Gemini premium, que
retornou `429`, caindo para o Groq real com sucesso — a mesma cascata da
TASK-059, agora acionada por uma interação real, não só por ferramentas de
validação manual.

Ainda em 2026-08-08 (TASK-058), a chave Gemini deixou de ser compartilhada
entre perfis: `AISHOPPING_GEMINI_API_KEY_USER` (perfil `USER`) e
`AISHOPPING_GEMINI_API_KEY_ADMIN_DEV` (premium e gratuito da cascata
`ADMIN`/`DEV`) são credenciais distintas, para que a cota gratuita de
usuários reais nunca seja consumida por validação manual ou uso
administrativo — mesmo objetivo do `--profile admin` da TASK-059, agora
garantido também no nível da credencial, não só do perfil lógico.

Em 2026-08-09 (TASK-063), o `collection_worker` passou a ser um novo
chamador do `AIProviderManager` — perfil `ADMIN` fixo (mesma cascata do
`AdminDevAIProviderManager`, nunca a chave/cota do perfil `USER`), para
normalizar título de exibição e classificar a correspondência
missão↔oferta antes de um alerta (`app.collection.relevance`,
`docs/architecture/price-alerts.md`). É o primeiro caso de uso da IA fora do caminho do
webhook/Telegram: o secret `gemini_api_key_admin_dev` (e opcionalmente
`groq_api_key`) passou a ser montado também no serviço `collection_worker`
(`docs/installation/secrets.md`) — continua sem token do bot, segredo do webhook nem
chave Gemini `USER`. Falha de IA (indisponibilidade, cota, resposta fora do
contrato) nunca derruba a coleta: o chamador trata como "sem resultado
válido ainda" e tenta de novo na próxima coleta. Validado numa missão real
via Telegram: sob carga real (~20 classificações numa única coleta), a
maioria das chamadas de IA falhou (`unavailable` nas três camadas da
cascata), mas as que tiveram sucesso classificaram corretamente — inclusive
distinguindo corretamente dois modelos textualmente parecidos ("Logitech G
PRO 2" como `match` do critério pedido; "Logitech G Pro X Superlight 2"
como `no_match`, apesar de nome muito similar).

Em 2026-08-09/2026-08-10 (TASK-064/DEC-050), a camada `gemini-3.1-pro-preview`
foi removida — a auditoria confirmou que o modelo é oficialmente `preview` e
que a chave não tem cota real de nível "Pro" (um candidato GA "Pro",
`gemini-pro-latest`, também falhou com `quota_exceeded` imediato). A cascata
`ADMIN`/`DEV` colapsou de 3 para 2 camadas: `gemini-3.6-flash` (o mesmo
modelo do perfil `USER`) e, se configurado, o Groq. `Settings.gemini_premium_model`/
`AISHOPPING_GEMINI_PREMIUM_MODEL` deixaram de existir. Validado com chamadas
reais: fallback Flash→Groq confirmado (Groq real respondendo quando o Flash
falha por `quota_exceeded`/`unavailable`, inclusive com o circuit breaker da
TASK-049 abrindo corretamente para o par `(gemini, gemini-3.6-flash)` depois
de falhas reais consecutivas) e uma coleta pequena representativa (missão
descartável, uma única fonte) confirmando melhora real na taxa de
classificação: 75% de sucesso (`classify_offer_relevance` e
`normalize_offer_title`, 15/20 cada), contra a maioria de falhas documentada
acima sob a cascata de 3 camadas. Nenhuma mudança na semântica de relevância
da TASK-063 nem no comportamento fail-closed. Detalhes completos em
`docs/tasks/TASK-064.md`.
