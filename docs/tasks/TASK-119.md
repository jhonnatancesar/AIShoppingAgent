# TASK-119 — Status desatualizado no GET de missão logo após pausar/retomar/cancelar

Status: **PLANNED/BACKLOG** — achado real durante o smoke funcional pós-limpeza
de lint do frontend (2026-09-04), registrado aqui sem nenhuma investigação de
causa raiz nem alteração de código ainda. Nenhuma relação com a limpeza de
lint (Subtask 16 / TASK de redesign do Admin) — o caminho de reload envolvido
não foi alterado nessa rodada.

## Problema observado

Reprodução real, com usuário autenticado (testado com uma conta ADMIN
descartável, mas o caminho de código não depende de papel):

1. Criar uma missão (`request_kind=generic_category` já é suficiente).
2. Chamar `POST /api/v1/missions/{id}/pause`.
3. **Imediatamente em seguida** (mesma sequência que o frontend faz via
   `onReload()` em `MissionDetailPage.tsx`), chamar
   `GET /api/v1/missions/{id}`.

A resposta desse GET imediato trouxe `status` e `state_version`
**desatualizados** (o valor de ANTES do pause, ex. `"active"`/`1`), mesmo que
`transitions[]` na MESMA resposta já contivesse a transição nova
(`{"from_status":"active","to_status":"paused",...}`). Confirmado por
consulta direta ao banco no momento exato: a linha em `missions` já estava
`status='paused'`, `state_version=2` — ou seja, o dado correto já existia no
Postgres quando o GET respondeu com o valor antigo.

Um **GET subsequente e independente** (ex.: recarregar a página do zero,
minutos depois) já retornou o valor correto (`"paused"`/`2`). O problema é
especificamente na janela imediatamente após a escrita, não um estado
permanentemente incorreto.

## Impacto no frontend

`MissionDetailPage.tsx` chama `onReload()` (a função `load`) logo após
`runAction` (pause/resume/cancel) ter sucesso — o usuário pode ver o status
antigo e o botão de ação errado (ex. ainda "Pausar" depois de já ter pausado)
por alguns segundos, até uma navegação/reload independente corrigir a tela.
Não há bug de frontend aqui — o `onReload` está fazendo exatamente um novo
GET, como esperado; o dado que ele recebe do backend é que está inconsistente
momentaneamente.

## Hipótese de causa raiz (não confirmada, só hipótese para orientar a investigação)

`transitions[]` parece vir de uma tabela/consulta diferente daquela que
popula `status`/`state_version` no mesmo payload, e as duas não estão sendo
lidas com a mesma visão consistente do banco logo após o commit do comando de
pause. Possibilidades a investigar, sem presumir qual é a certa:

- sessão/conexão assíncrona reaproveitada com snapshot antigo (nível de
  isolamento de transação);
- objeto ORM da missão não recarregado (`session.refresh`/`expire`) antes de
  serializar a resposta do GET, enquanto as transições são buscadas via uma
  query separada que já reflete a escrita;
- alguma camada de cache (se existir) no caminho de leitura de missão, não
  invalidada no mesmo commit do comando de pause.

## Escopo

Investigar `backend/app/webapp` (router de missions) e o serviço/comando de
pause/resume/cancel de missão, a camada de sessão async
(`backend/app/database/session.py`) e qualquer commit/refresh envolvido no
comando de pause. Reproduzir com um teste automatizado que isole exatamente
essa sequência (ação de mudança de status seguida de GET, na mesma janela),
confirmar a causa raiz, corrigir, e adicionar teste de regressão cobrindo o
cenário.

## Fora de escopo

Mudar o contrato da API (o frontend já espera receber o estado atualizado no
GET pós-reload — não é para mudar isso). Mudar o frontend — o problema é
inteiramente do lado do backend.

## Critério de validação futuro

Repetir exatamente a sequência do problema observado (pause → GET imediato)
e confirmar que o GET já retorna `status`/`state_version` consistentes com
`transitions[]` e com o banco, sem depender de um segundo GET independente.
Cobrir também resume e cancel, já que o mesmo caminho de reload é usado pelos
três comandos.
