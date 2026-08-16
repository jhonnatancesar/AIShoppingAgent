# TASK-053 — Criar testes ponta a ponta

Status: **Concluída** em 2026-08-09 — E2E reproduzível e E2E externo aprovados (`PASS`), fechamento confirmado explicitamente pelo usuário (ver "E2E externo final refeito" e "Encerramento" abaixo).

Dependência obrigatória: TASK-062 concluída. O E2E exercita o fluxo real da
aplicação e não monta manualmente a sequência agenda → coleta → persistência
→ evento.

## Objetivo

Manter uma regressão E2E reproduzível e comprovar externamente a cadeia crítica
da V1:

`Telegram → autenticação → AI Provider Manager → confirmação da TASK-058 →`
`missão → agenda → collection_worker → Store Provider → observação →`
`avaliação → evento → telegram_notifier → Telegram`.

A confirmação da criação já existe na TASK-058. A confirmação de oferta
da TASK-040 não participa deste fluxo.

## Modos obrigatórios

### E2E reproduzível

- roda por comando explícito, fora do pipeline rápido;
- usa PostgreSQL 18.4 descartável, migrations reais, endpoint HTTP real e os
  loops reais do `collection_worker` e do `telegram_notifier`;
- controla somente as bordas externas de IA, marketplace e Telegram;
- nunca insere `PriceObservation` ou `Event` manualmente e nunca chama o
  avaliador ou o notifier diretamente;
- valida replay, ownership, fontes selecionadas, falha isolada, missão não
  elegível, `skipped`, restart e terminalidade do consumo;
- distingue uma nova coleta histórica legítima de uma duplicação da mesma
  execução pelos IDs de run, evento e tentativa de consumo.

### E2E externo

- usa Telegram, HTTPS/cloudflared, Gemini via Provider Manager, Chromium/Xvfb,
  Pichau, Terabyte, Amazon, Kabum, PostgreSQL e os dois workers reais;
- cria pelo canal público uma missão com alvo alto e deliberadamente
  permissivo, para provocar `price.target_reached.v1` sem fabricar preço;
- preço, moeda e disponibilidade precisam vir da página real; frete, quando
  a página revelar, também vem dela — nunca é fabricado nem tratado como
  zero/grátis quando desconhecido, mas na V1 (`DEC-045`) não é exigido
  para elegibilidade, porque o monitoramento de preço compara `amount`;
- não contorna CAPTCHA, bloqueio, autenticação ou proteção das lojas;
- restaura o webhook anterior e remove nominalmente o ambiente descartável;
- termina em exatamente uma classificação: `PASS`, `FAIL_INTERNO` ou
  `BLOCKED_EXTERNAL`.

Ausência de evidência elegível por comportamento de terceiro é
`BLOCKED_EXTERNAL`, nunca sucesso. Antes de atribuir `FAIL_INTERNO`, o
diagnóstico deve demonstrar que a falha pertence ao sistema.

## Critérios de aceite — status final (2026-08-09)

- [x] a missão nasce do webhook autenticado, passa pela interpretação e pela
  confirmação pública existente — confirmado no E2E externo real (missão
  "Logitech g pro 2" criada e confirmada pelo usuário via Telegram real) e
  no E2E reproduzível;
- [x] a agenda é reivindicada pelo worker, sem chamada manual da cadeia
  interna — `collection_worker` real processou a agenda sozinho (poll de
  15s), sem `once=True`/chamada direta no modo externo;
- [x] quatro fontes selecionadas geram runs terminais independentes — 3
  `succeeded` (Amazon, Terabyte, Kabum) + 1 `failed` (Pichau), todos
  terminais e independentes;
- [x] ao menos uma observação externa elegível produz alerta de alvo e
  entrega Telegram pela cadeia normal — 41 observações elegíveis, 11
  eventos `price.target_reached.v1`, 11 notificações Telegram reais
  `succeeded`;
- [x] replay da mesma update/execução não repete efeitos — validado no E2E
  reproduzível (restart sem duplicação de runs/observações/consumo);
- [x] o mesmo evento possui no máximo um consumo terminal por consumidor —
  0 consumos duplicados no E2E externo (11 eventos, 11 consumos
  `succeeded`) e validado explicitamente no E2E reproduzível;
- [x] restart não reprocessa trabalho concluído, mas nova coleta legítima
  pode acrescentar nova observação histórica — validado no E2E
  reproduzível;
- [x] preferência desativada produz `skipped` terminal e não reenvia ao
  reativar — validado no E2E reproduzível (missão "e2e skipped");
- [x] ownership, privacidade da telemetria e diagnósticos sanitizados
  permanecem — validado no E2E reproduzível (log final sem título de
  missão nem Telegram ID) e mantido no E2E externo;
- [x] pipeline completo, revisão, documentação e workflow Git aprovados —
  `scripts\check.ps1`: 723 testes rápidos, 90,43% de cobertura, 13
  integrações PostgreSQL reais, migration head `20260809_0003`.

Todos os critérios de aceite estão atendidos. A falha isolada da Pichau no
E2E externo (instabilidade externa confirmada, não `403/429`) não invalida
nenhum critério: gerou run terminal independente, não foi mascarada como
sucesso, não derrubou as outras três fontes e não acionou o backoff
persistente do DEC-047 indevidamente — o sistema degradou exatamente como
projetado.

## Fora do escopo

- arquitetura de V2, novos marketplaces ou contorno de proteção externa;
- fabricar observações/eventos ou inferir frete/disponibilidade;
- exigir queda de preço real durante a janela do teste;
- colocar dependências públicas no pipeline rápido cotidiano.

## Correções internas encontradas durante a execução

- o onboarding passou a listar lojas por números e aceitar `5` para todas;
- concluir `/cadastro` passou a emitir o link inicial de senha e orientar
  `/entrar` depois da criação;
- o mínimo de senha passou a oito caracteres, sem regra de composição naquela
  validação (a política atual passou posteriormente a exigir maiúscula,
  minúscula, número e símbolo), com as proteções existentes preservadas;
- rejeição `ok=false` ao registrar/restaurar webhook passou a encerrar a
  ferramenta com falha real;
- frete dependente de login/endereço e promoção condicional não pode ser
  normalizado como zero numa sessão anônima.
- conclusões de senha/login passaram a publicar confirmações duráveis no chat;
  alteração e recuperação usam o mesmo contrato, e a sessão recebe avisos
  únicos antes e depois de expirar (`DEC-043`, migration `20260809_0002`);
- a conversa de orçamento ausente e sugestão de referência de mercado foi
  reservada à V1.2 (`DEC-044`), sem ampliação da V1;
- o classificador externo deixou de consultar uma coluna inexistente da chave
  composta de `mission_sources`.

## Validações realizadas nesta execução (histórico — superseded)

> Classificação `BLOCKED_EXTERNAL` abaixo é anterior à disponibilidade por
> card, DEC-046 e DEC-047. Ver "E2E externo final refeito" para o resultado
> `PASS` atual.

- E2E reproduzível: 2 cenários aprovados em PostgreSQL 18.4, incluindo
  upgrade, downgrade e novo upgrade até `20260809_0002`;
- E2E externo: quatro fontes executadas e 40 observações reais; 2 runs
  concluídas e 2 falhas externas isoladas; nenhuma observação possuía frete
  conhecido/elegível sem login no marketplace;
- autenticação no Telegram real: exatamente 2 eventos/consumos `succeeded`
  para senha criada e login, 1 aviso pré-expiração e 1 aviso de expiração;
  um novo ciclo do worker manteve exatamente 1 evento e 1 terminal para a
  expiração, comprovando ausência de duplicação;
- classificação externa atual: `BLOCKED_EXTERNAL` com motivo
  `no_eligible_external_evidence`, nunca convertida em sucesso ou falha interna.

## Amazon — instabilidade sob concorrência (retomada 2026-08-09)

A falha intermitente anteriormente observada na Amazon não foi reproduzida na
validação representativa atual em Docker/Linux. O provider permaneceu sem
alteração. Caso a falha reapareça, a investigação deve ser retomada com
evidência capturada no momento da ocorrência.

## E2E reproduzível refeito após disponibilidade por card, DEC-046 e DEC-047

A validação registrada em "Validações realizadas nesta execução" é anterior à
disponibilidade por card/fallback seletivo, ao DEC-046 (intervalo/stagger) e
ao DEC-047 (backoff persistente por fonte). Reexecutado com o estado atual
(`3f02fd8`):

- primeira execução (`python scripts/run_e2e_tests.py`) **falhou**:
  `test_critical_chain_replay_restart_skipped_and_ownership` esperava 4
  `CollectionRun` logo após criar a missão e rodar o `collection_worker` uma
  vez, mas recebeu 0. Causa raiz confirmada: o `staggered_next_run_at`
  (DEC-046) passou a deslocar `next_run_at` em até
  `collection_schedule_stagger_seconds` (padrão 300s) já na criação da
  missão pelo webhook; o teste não fixava esse valor, então o `next_run_at`
  gerado ficava aleatoriamente no futuro e a agenda não estava due na hora do
  `once=True`. Não é bug de produto — é o comportamento pretendido pelo
  DEC-046 (evitar sincronização entre missões) não refletido no cenário E2E,
  que foi escrito antes da mudança.
- corrigido em `tests/e2e/test_critical_flow.py`: mesma técnica já usada em
  `tests/test_mission_schedules.py` para testar `staggered_next_run_at`
  isoladamente — `monkeypatch.setattr("app.missions.schedule.random.uniform",
  lambda a, b: 0.0)`, tornando o E2E determinístico sem alterar o produto
  nem o comportamento real de stagger (que já tem cobertura unitária
  dedicada em `test_mission_schedules.py`, `test_mission_creation.py` e
  `test_collection_orchestration.py`).
- segunda execução: **2/2 cenários aprovados**, incluindo a cadeia completa
  (criação → confirmação → agenda → `collection_worker` real → 4
  `CollectionRun`/3 observações/3 eventos de alvo → `telegram_notifier` real
  → restart sem duplicação → missão `skipped` sem reenvio → missão pausada
  sem run → ownership) e o cenário de onboarding/autenticação (senha, login,
  avisos de expiração sem duplicação).
- E2E reproduzível considerado válido para o código atual, incluindo
  disponibilidade por card, DEC-046 e DEC-047.

## E2E externo final refeito (2026-08-09, `3f02fd8`/`bb6bb3f`)

Execução única e representativa, sem investigação preparatória contra a
Pichau (nenhuma busca de aquecimento, nenhuma página extra, nenhuma repetição
de URL, nenhum fallback forçado) — só a coleta normal que já fazia parte do
fluxo.

**Ambiente:** stack Compose local reconstruído com o código atual (imagem
rebuildada, migration `20260809_0003` aplicada ao banco existente), túnel
`cloudflared` temporário, webhook do bot real substituído temporariamente e
restaurado (removido, `--action delete`) ao final — a URL anterior já estava
morta antes desta execução. `AISHOPPING_AUTH_PUBLIC_BASE_URL` revertido em
`.env` depois do teste.

**Missão real:** criada e confirmada pelo próprio usuário via Telegram real
("Logitech g pro 2", alvo `999999.00 BRL`, quatro fontes selecionadas).
Agenda, `staggered_next_run_at` (~4 min) e o `collection_worker` real
(poll de 15s, sem chamada manual da cadeia interna) processaram a missão
sozinhos.

**Resultado por fonte:**

| Fonte | Coleta | Observações | Com `amount` válido | AVAILABLE | UNKNOWN | Bloqueio externo | Fallback |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Amazon | `succeeded` | 18 | 18 | 18 | 0 | não | não precisou (sem UNKNOWN) |
| Terabyte | `succeeded` | 20 | 20 | 20 | 0 | não | não precisou (sem UNKNOWN) |
| Kabum | `succeeded` | 20 | 20 | 3 | 17 | não | sim — top K=3 (`availability_fallback_max_candidates`), as 3 tentativas resolveram para AVAILABLE; as 17 restantes permaneceram UNKNOWN por estarem fora do K, não por falha |
| Pichau | `failed` | 0 | 0 | 0 | 0 | instabilidade externa observada (ver abaixo) | não aplicável (run falhou antes de qualquer card) |

**Pichau — o que aconteceu, sem investigação adicional:** o worker processou
outra missão pré-existente com Pichau selecionada pouco antes (mesmo
processo, mesmo circuit breaker por provider da TASK-049); essa tentativa
falhou com `failure_code=provider_unavailable`
(`ProviderNavigationError`/timeout de navegação — não um `403`/`429`
confirmado). O circuit breaker do provider Pichau abriu em seguida, e a
tentativa desta missão herdou esse circuito já aberto
(`failure_code=circuit_open`), falhando sem nova requisição de rede. Não é
bug: é exatamente o comportamento pretendido da resiliência local (TASK-049)
protegendo contra falhas repetidas. Como não foi um `ProviderBlockedError`
com status `403`/`429` confirmado, o backoff persistente por fonte do
DEC-047 **corretamente não foi acionado**
(`mission_sources.consecutive_blocks=0`, `next_eligible_at=NULL` para
Pichau) — critério do DEC-047 é intencionalmente restrito a bloqueio
confirmado, não a timeout/instabilidade de navegação. Registrado como
bloqueio/instabilidade externa observada; nenhuma nova tentativa foi feita,
nenhuma página extra foi aberta, nenhum fallback foi forçado.

**Eventos e notificações:**

- `collection.completed.v1`: 3 (uma por fonte bem-sucedida);
- `collection.failed.v1`: 1 (Pichau);
- `offer.availability_changed.v1`: 1 (Kabum, oferta que virou AVAILABLE via
  fallback);
- `price.target_reached.v1`: 11 (6 Amazon, 1 Kabum, 4 Terabyte) — todas as
  ofertas elegíveis (`amount` real, disponibilidade AVAILABLE, moeda BRL
  compatível com o critério) geraram alerta, sem preço fabricado;
- consumo pelo `telegram_notifier` real: 11 tentativas, **11 `succeeded`**,
  **0 duplicadas** — todas entregues pela Bot API real ao chat privado do
  usuário.

**Classificação oficial** (`python -m scripts.validate_external_e2e
--mission-title "Logitech g pro 2"`):

```json
{"evidence": {"consumption_attempts": 11, "duplicate_terminal_consumption": false,
"eligible_observations": 41, "failed_runs": 1, "observations": 58,
"running_runs": 0, "runs": 4, "selected_sources": 4, "succeeded_runs": 3,
"successful_notifications": 11, "target_events": 11}, "status": "PASS"}
```

**`PASS`.** Uma fonte (Pichau) falhou por instabilidade externa real, e o
sistema reagiu corretamente — sem transformar isso em `FAIL_INTERNO` nem
insistir contra o bloqueio: as outras três fontes produziram evidência
elegível real, geraram eventos reais e o Telegram real confirmou entrega sem
duplicação. Nenhuma observação/evento foi inserido manualmente; nenhum
provider foi contornado; nenhum CAPTCHA/bloqueio foi burlado.

## Encerramento

Aprovado explicitamente pelo usuário em 2026-08-09, com o resultado acima
aceito nestes termos:

- Amazon, Terabyte e Kabum: `succeeded`;
- Pichau: falha externa isolada (`provider_unavailable`/`circuit_open`),
  tratada corretamente sem contaminar as demais fontes nem o backoff
  persistente do DEC-047 — **condição externa observada, não um bug interno
  pendente**; nenhuma ação de código é devida por isso;
- 11 eventos `price.target_reached.v1`, 11 consumos Telegram `succeeded`, 0
  duplicados;
- cadeia real executada do webhook ao Telegram sem nenhuma chamada manual
  interna;
- pipeline oficial completo aprovado (723 testes rápidos, 90,43% de
  cobertura, 13 integrações PostgreSQL reais);
- E2E reproduzível aprovado (2/2);
- E2E externo aprovado (`PASS`).

Todos os critérios de aceite atendidos (seção acima). TASK-053 **concluída**.
TASK-054 não foi iniciada.

