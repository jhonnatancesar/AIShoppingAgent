# ADR 016 — Rollout DEV de AI via César Core

Status: implementado na TASK-118F, aguardando revisão do usuário.

## Decisão

Preservar `AIProviderManager` como única porta de IA e selecionar o gateway
via factories existentes, com flag default false. `CesarCoreAIProvider` mantém
messages e roles, envia classe neutra e `FREE_ONLY`, nunca escolhe modelo.
O Core decide policy/target e aplica quota/auth/limite. Credencial DEV própria
por `*_FILE`, distinta da credencial upstream.

Disaster fallback opt-in só para falha de conexão. Respostas de erro do Core
e timeout incerto não repetem os providers antigos, evitando duplicação de
consumo e mascaramento de quota/auth/policy. Grounding permanece no fluxo
anterior; Search não migra nesta TASK. Endpoint DEV é loopback; Compose e PROD
não mudam. Rollback por flag preserva os providers gratuitos anteriores.

## Evidência e consequências

Core aceita prompt legado OU messages e normaliza ambos para o mesmo domínio.
Contract real confirmou system/user separados, resultado CAPPED e correlação.
Os 21 contracts anteriores do Core não regrediram. Operação e testes completos
em `docs/tasks/TASK-118F.md`. Nenhum consumidor legado precisa migrar agora.
