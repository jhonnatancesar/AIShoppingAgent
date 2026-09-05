# TASK-119 — Status desatualizado no GET de missão logo após pausar/retomar/cancelar

Status: **NÃO REPRODUZIDO após investigação (2026-09-05) — rebaixada,
mantida só como registro.** Investigação extensa (ver "Investigação
2026-09-05" abaixo) não conseguiu reproduzir o problema por nenhum
caminho testado: nem HTTP direto contra o backend real, nem através do
proxy dev do Vite, nem em 3 tentativas reais no navegador replicando
exatamente a sequência original (incluindo a troca rápida entre duas
missões imediatamente antes de pausar). A explicação mais provável é um
artefato da ferramenta de inspeção de rede do navegador usada na sessão
original (ver detalhe abaixo), não um bug real da aplicação. Nenhuma
relação com a limpeza de lint (Subtask 16 / TASK de redesign do Admin) —
o caminho de reload envolvido não foi alterado nessa rodada.

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

## Investigação 2026-09-05 — não reproduzido

Auditoria de código (`missions_router.py`, `app/missions/service.py`,
`app/missions/query.py`, `app/database/dependency.py`) não encontrou
nenhum caminho estrutural que explicasse o sintoma original: `pause`/
`resume`/`cancel` (`transition_mission_async`) atualiza `mission.status`,
`mission.state_version` e insere a `MissionTransition` na MESMA
transação/commit; `get_mission_detail_for_user` faz um `select(Mission)`
genuinamente novo (sessão nova por requisição, `get_web_async_session`,
sem cache/identity-map compartilhado entre requisições). Não havia razão
estrutural para `status` vir stale enquanto `transitions[]` vem fresco na
MESMA resposta.

Três metodologias de reprodução, todas sem sucesso:

1. **HTTP direto contra o backend real** (`127.0.0.1:8000`, sem proxy):
   10 iterações de `criar missão → pause → GET imediato`, sequenciais,
   mesma sessão de usuário — **10/10 corretas**.
2. **HTTP através do proxy dev do Vite** (`localhost:5173`, mesmo
   caminho que o navegador usa): 15 iterações do mesmo cenário — **15/15
   corretas**.
3. **Navegador real** (Playwright/CDP via ferramenta de automação),
   replicando a sequência exata da observação original, incluindo o caso
   mais agressivo (trocar de missão A para uma missão B recém-criada com
   só 0,1s de intervalo e clicar em "Pausar" imediatamente, sem esperar o
   carregamento assentar — o cenário que deveria maximizar a chance de um
   fetch de montagem duplicado pelo React StrictMode ainda estar em voo
   quando o reload pós-ação chega): **3/3 tentativas corretas**, UI
   sempre mostrou "Pausada"/"Retomar" imediatamente após a ação.

### Explicação mais provável

A observação original veio de uma única captura, via
`read_network_requests({requestId})` da ferramenta de automação de
navegador, de UMA resposta HTTP que continha `status="active"` e
`transitions[]` já com a transição nova — um estado internamente
inconsistente que só faria sentido se fossem DUAS respostas diferentes
lidas como uma só. Essa observação aconteceu logo depois de uma sequência
própria de testes com navegação muito rápida e sobreposta (troca de duas
`offerId` sintéticas e duas missões reais em sucessão, tudo em poucos
segundos, para testar especificamente proteção contra corrida) — ou seja,
a ferramenta de inspeção de rede estava sob uma carga de requisições
sobrepostas bem maior que qualquer uso real. A hipótese mais provável é
um artefato de atribuição de `requestId`/corpo de resposta da própria
ferramenta de inspeção sob essa carga, não um bug do backend nem do
frontend.

### Encerramento

Nenhuma alteração de código feita — não há causa raiz real identificada
para corrigir. Se o sintoma for observado de novo, a evidência mínima
necessária para reabrir esta TASK com confiança é uma captura no nível de
rede bruta (HAR do navegador real, ou log do backend mostrando o valor
efetivamente lido/serializado por aquela requisição específica) — não
apenas leitura pelo painel de rede de uma ferramenta de automação.
