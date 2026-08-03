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
  `gemini-3.6-flash`; exige `AISHOPPING_GEMINI_API_KEY`.
- `ADMIN/DEV`: política única que tenta `gemini-3.1-pro-preview` e retorna ao
  `gemini-3.6-flash` em quota ou indisponibilidade do nível premium.
- `PLUS`: futuro; não existe no contrato nem na V1.

O perfil USER traduz mensagens para o contrato Gemini, fecha o cliente assíncrono
após cada chamada e converte quota, indisponibilidade, autenticação e rejeição em
erros sanitizados. Ao atingir o limite, `AIProviderQuotaExceeded` permite ao canal
informar que o usuário tente novamente mais tarde. USER nunca tenta o modelo
premium. OpenAI, Claude, usuário pago e comparação multi-IA ficam para a V2.

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
