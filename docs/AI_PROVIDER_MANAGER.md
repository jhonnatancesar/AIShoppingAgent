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

- `USER`: implementado com o SDK oficial `google-genai` e o modelo configurável
  `gemini-3.6-flash`; exige `AISHOPPING_GEMINI_API_KEY`. Sem fallback.
- `ADMIN/DEV`: política única que tenta `gemini-3.1-pro-preview`, depois o
  Groq (`GroqProvider`, TASK-059, opcional — só entra se
  `AISHOPPING_GROQ_API_KEY` estiver configurada) e, por último, o
  `gemini-3.6-flash` gratuito. Sem a chave do Groq, o comportamento é o
  mesmo de dois níveis já validado nas TASKs 029–031.
- `PLUS`: futuro; não existe no contrato nem na V1.

O perfil USER traduz mensagens para o contrato Gemini, fecha o cliente assíncrono
após cada chamada e converte quota, indisponibilidade, autenticação e rejeição em
erros sanitizados. Ao atingir o limite, `AIProviderQuotaExceeded` permite ao canal
informar que o usuário tente novamente mais tarde. USER nunca tenta o modelo
premium nem o Groq. OpenAI, Claude, usuário pago e comparação multi-IA ficam
para a V2 — o Groq só existe como fallback interno de infraestrutura do
ADMIN/DEV, nunca como escolha exposta ao usuário final.

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
autenticada contra a API do Groq (`llama-3.3-70b-versatile`), e a cascata de
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
