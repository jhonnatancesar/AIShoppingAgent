# TASK-059 — Groq como fallback do ADMIN/DEV e perfil configurável de validação

Status: Concluída

## Objetivo

Implementar um `GroqProvider` dentro de `app.ai_provider`, seguindo o
contrato agnóstico já usado pelo Gemini, e adicioná-lo como terceiro nível
de fallback do perfil `ADMIN/DEV` (depois do Gemini premium, antes do
Gemini gratuito). Adicionar um parâmetro opcional de perfil ao
`IntentInterpreter.interpret()` (default `USER`, inalterado para produção)
para que ferramentas de validação manual possam rodar via `ADMIN/DEV` e
preservar a cota gratuita compartilhada do `USER` para usuários reais do
Telegram.

## Contexto

Registrada em `DEC-016` depois que a chave `AISHOPPING_GROQ_API_KEY`,
presente em `backend/.env`, foi testada isoladamente (fora do
`AIProviderManager`) e respondeu com sucesso. Durante a definição do
escopo, ficou claro que só adicionar o Groq não resolveria o problema
prático que motivou o pedido — a validação manual da TASK-057 esgotou a
cota do perfil `USER`, e `IntentInterpreter.interpret()` fixa
`profile=UserRole.USER` incondicionalmente, então nenhuma troca de manager
no script de validação teria efeito sem essa mudança.

## Escopo

- `GroqProvider` (`backend/app/ai_provider/groq.py`), via `httpx` contra a
  API compatível com OpenAI do Groq (`/openai/v1/chat/completions`),
  mapeando erros para os mesmos tipos sanitizados do `GeminiProvider`
  (`AIProviderQuotaExceeded` com `quota_reset_at` quando o cabeçalho
  `retry-after` vier informado, `AIProviderUnavailable`,
  `AIProviderError` para autenticação/rejeição/resposta vazia), sem vazar
  corpo bruto nem credenciais.
- `AISHOPPING_GROQ_API_KEY` e `AISHOPPING_GROQ_MODEL` (default
  `openai/gpt-oss-120b`) em `backend/app/core/config.py`,
  `backend/.env.example` e `docs/development/dependencies.md`. `httpx` declarado
  explicitamente em `backend/requirements.txt` (já presente de forma
  transitiva).
- `AdminDevAIProviderManager` (`backend/app/ai_provider/manager.py`) passa a
  tentar até três níveis, nesta ordem: Gemini premium → Groq → Gemini
  gratuito. Groq é opcional: se `AISHOPPING_GROQ_API_KEY` não estiver
  configurada, `build_admin_dev_ai_provider_manager` mantém o
  comportamento de dois níveis já existente, sem quebrar ambientes sem a
  chave. `USER` continua sem nenhum fallback, inalterado.
- `IntentInterpreter.interpret()` ganha um parâmetro nomeado opcional
  `profile: UserRole = UserRole.USER`, propagado para o `AIRequest`
  construído internamente. O webhook do Telegram (TASKs 033–035) não passa
  esse parâmetro e continua sempre em `USER` — nenhuma mudança de
  comportamento em produção.
- `backend/scripts/validate_intent_interpreter.py` ganha uma opção de
  perfil (`admin` como padrão, poupando a cota do `USER`; `user` disponível
  para a confirmação final antes de fechar a TASK-057), construindo o
  manager correspondente.
- Testes unitários novos/ajustados para o `GroqProvider`, para a cascata de
  3 níveis do `AdminDevAIProviderManager` (incluindo Groq ausente/opcional)
  e para o parâmetro de perfil do `IntentInterpreter`.
- Validação real: chamada direta ao Groq via `AIProviderManager`, cascata
  de fallback real (premium → Groq) e retomada da validação bloqueada da
  TASK-057 (mensagens de `query_mission`, `mission_command` e `unknown`)
  via perfil `ADMIN/DEV`.

## Fora de escopo

- Não decide nem prepara OpenAI, Claude ou qualquer outro provedor além do
  Groq — nenhum código ou stub para eles.
- Não expõe escolha de provedor de IA ao usuário final; a política
  continua interna ao `AIProviderManager`.
- Não altera `app.telegram` (adaptador, webhook ou despacho de missão) nem
  o comportamento do `IntentInterpreter` para o caminho de produção
  (perfil `USER` continua o único usado pelo webhook).
- Não fecha a TASK-057 automaticamente: uma confirmação final contra o
  Gemini real do perfil `USER` continua sendo o critério de aceite dela.

## Critério de aceite

`GroqProvider` validado contra a API real do Groq através do
`AIProviderManager`. Cascata de 3 níveis do `AdminDevAIProviderManager`
validada de ponta a ponta (incluindo o caminho sem Groq configurado).
`IntentInterpreter` interpreta corretamente com o perfil `ADMIN`/`DEV`
passado explicitamente, sem alterar o padrão `USER`. Suíte automatizada
completa aprovada (`scripts\check.cmd`).

## Resultado da validação real (2026-08-08)

- `GroqProvider` chamado de verdade via `AIProviderManager`
  (`openai/gpt-oss-120b`), resposta coerente recebida.
- Cascata de 3 níveis validada de ponta a ponta: um premium real forçado a
  falhar por cota (`AIProviderQuotaExceeded`) resultou em resposta real do
  Groq (`response.provider == "groq"`), confirmando que o fallback alcança
  o provedor real, não só os fakes dos testes unitários.
- `backend/scripts/validate_intent_interpreter.py --profile admin` rodou as
  19 mensagens diversas da TASK-057 (informal, gírias, erros de digitação,
  ordem livre), cobrindo os quatro `IntentKind`, todas classificadas
  corretamente — incluindo os seis comandos de missão — sem tocar na cota
  compartilhada do `USER`.
- `scripts\check.cmd` completo aprovado: 316 testes, 94,96% de cobertura.
- Efeito colateral positivo: a confirmação final da TASK-057 contra o
  `USER`/Gemini real avançou de 3/19 mensagens para cobrir 3 dos 4
  `IntentKind` (`create_mission`, `query_mission`, `mission_command`);
  `unknown` segue pendente por nova exaustão de cota do `USER`
  (`docs/tasks/TASK-057.md`).
