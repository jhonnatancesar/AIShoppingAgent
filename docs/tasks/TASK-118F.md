# TASK-118F — Integração AI do GG Oferta com César Core

Status: implementação DEV e validação real realizadas; aguardando revisão.
Sem commit, push, PROD ou integração Search 118G.

## Escopo e pré-requisitos

Integração solicitada explicitamente após aprovação da 118E (21/21 contracts).
O contrato Core foi estendido, com autorização, para `prompt` OU `messages`;
o GG Oferta preserva `system/user/assistant`, sem concatenar nem duplicar system.
Credencial DEV nova, aleatória, em arquivos distintos por projeto e ignorados;
nenhuma chave OmniRoute é usada como credencial de aplicação.

## Implementação

- `CesarCoreAIProvider` envia mensagens e requirements neutros, `FREE_ONLY`,
  cap e metadata via `POST /v1/ai/generate`. Modelo é decidido no Core.
- Factories existentes optam pelo manager Core somente com
  `AISHOPPING_CESAR_CORE_AI_ENABLED=true` (default false).
- Credencial exclusivamente `AISHOPPING_CESAR_CORE_API_KEY_FILE`; URL DEV
  HTTP loopback, sem redirecionamento ou proxy herdado. `*_SERVICE` deve ser
  `backend` ou o serviço real do processo, como `collection_worker`.
- Serviço/classe/cap/timeout configuráveis, sem usar papel como escolha de modelo.
- Grounding conserva o caminho atual e não passa pelo Core na 118F.
- Disaster fallback é opt-in por `AISHOPPING_CESAR_CORE_DISASTER_FALLBACK_ENABLED`.
  Somente `ConnectError` permite a cascata gratuita anterior. HTTP de qualquer
  erro (inclusive 400/401/403/429/502/503), timeout/leitura incerta, resposta
  malformada e cap violado não causam fallback local. Assim, tentativas já
  processadas pelo gateway não são duplicadas. Circuito aberto falha fechado.
- Evento agregado `cesar_core_disaster_fallback` distingue entrada na cascata;
  demais tentativas continuam usando a telemetria existente.
- Nenhum módulo de domínio alterado; nenhum banco, migration ou Compose alterado.

## Prova real

Manager ADMIN/DEV -> provider GG Oferta -> Core HTTP loopback -> OmniRoute
3.8.50 (mesmo digest aprovado) -> `oc/mimo-v2.5-free`.
Mensagens de teste system/user separadas chegaram intactas ao request upstream;
resposta `CAPPED`, modelo normalizado `mimo-v2.5-free`, correlação preservada.
Ao encerrar o Core, nova chamada recebeu erro tipado de conexão sem cascata
(flag disaster false). A busca em logs/traces/métricas capturados não encontrou
as credenciais nem o conteúdo das mensagens.

Core: 21 contracts anteriores + novo de roles, todos passando (22/22).
Unitários complementam erros, cap e disaster opt-in; não substituem a prova real.
Resultado final focado GG Oferta: **122 testes passaram**. O contract permanente
`tests/test_cesar_core_ai_contract.py` também passou contra Core/OmniRoute reais.
Habilitar explicitamente com `AISHOPPING_RUN_CESAR_CORE_CONTRACTS=1`, configurar
as variáveis DEV `AISHOPPING_CESAR_CORE_*` e manter o Core real iniciado; por
padrão, esse teste externo é pulado. Core não-contract: **204 passaram**.
Ruff e diff-check passaram; não há typechecker estático configurado no projeto.
Não há promessa de migração PROD: ativação e rollback são por flag do processo
DEV; os examples não propagam novas variáveis ao Compose.

## Operação local

GG Oferta lê sua própria cópia `.secrets/cesar-core-client-dev`; Core lê
`.secrets/ggoferta-core-client-dev` de seu próprio repositório via
`CESAR_CORE_SECURITY_GG_OFERTA_API_KEY_FILE`. No Core, passar configurações pelo
ambiente do processo: o `.env` compartilhado entre diferentes Settings ainda
rejeita chaves de outros namespaces (`extra_forbidden`); não foi alterado nesta TASK.
Não colocar valor secreto no `.env.example`, terminal, log ou Git.

Rollback: desligar `AISHOPPING_CESAR_CORE_AI_ENABLED` retorna às factories
anteriores, preservando suas credenciais locais e guardrails existentes.
