# TASK-081 — Processos Chromium/Playwright zumbis no `collection_worker`

Status: **CONCLUÍDA E VALIDADA EM RUNTIME (2026-08-13)** — `init: true`
aplicado só no `collection_worker` (`compose.yaml`), comprovado por
comparação real antes/depois (mesma missão real de 4 fontes: 13 zumbis
sem a correção → 0 zumbis com ela). Confirmado em runtime após
aprovação: `collection_worker` `healthy` (`RestartCount=0`), `Init=true`
só nele (`api`/`telegram_notifier`/`database` com `Init=<nil>`), `PID 1`
= `docker-init`, contagem de zumbis = `0`. Por instrução explícita do
usuário, o roteiro exaustivo original (exceção forçada, timeout
forçado, cancelamento, dois workers, `crashpad_handler`, regressão de
longa duração) **não foi executado** — ver "Registro de implementação"
no final deste documento para o que foi e não foi comprovado.

Dependência: nenhuma bloqueante. Relacionada à `TASK-079` (onde os
zumbis foram observados pela primeira vez, como hipótese inicial depois
descartada como causa do autodeadlock — ver "Relação com a TASK-079"
abaixo). Esta TASK trata os zumbis como o que realmente são: **um
problema de recursos separado**, não uma reabertura da TASK-079.

Versão alvo: a definir pelo usuário.

## Relação com a TASK-079

**Importante, para não repetir um engano já corrigido:** a TASK-079
inicialmente suspeitou que os zumbis do Chromium causassem o travamento
do `collection_worker`. Investigação com `py-spy`/`pg_locks` **refutou
essa hipótese** — a causa raiz comprovada do travamento foi um
autodeadlock do event loop (transação síncrona de banco atravessando
`await` de IA), sem nenhuma relação com os zumbis. Esta TASK **não
afirma nem reabre** essa questão — trata os zumbis como o problema de
recursos que sempre foram, paralelo e independente.

## Comprovação de que o problema ainda existe no ambiente atual

Reproduzido ao vivo nesta rodada, no `collection_worker` já em execução
na implantação nova (container criado `2026-08-13T21:34:05Z`):

- Único disparo de Chromium neste container até o momento: um teste
  controlado de validação (`python -c "with sync_playwright()..."`,
  parte da checklist de deploy, abre e fecha um browser headless contra
  `about:blank`).
- Contagem de processos via `/proc` **depois** desse único ciclo
  abrir+fechar: **15 zumbis** — `chrome-headless`×7, `chrome`×4,
  `chrome_crashpad`×4 — número quase idêntico ao episódio original da
  TASK-079 (15 zumbis: 14 Chromium + 1 Xvfb).
- `PPid` de todos os zumbis verificados = `1` (o processo Python do
  worker) — confirma que se tornaram órfãos e foram reparented para o
  PID 1 do container, exatamente a hipótese da TASK-079.
- `docker inspect` confirma `HostConfig.Init` = `null` — **`init: true`
  continua não aplicado**, consistente com o registro da TASK-079.

Ou seja: **um único ciclo limpo de abrir/fechar Chromium via Playwright
já produz zumbis nesta implantação nova** — não é preciso uma missão
real ou múltiplas coletas para reproduzir; a comprovação exigida por
esta etapa está satisfeita antes mesmo do plano de investigação abaixo
começar.

## Auditoria do lifecycle do Playwright no código do projeto

Feita nesta rodada, por leitura direta (não suposição):

- **Único ponto de lançamento do Chromium em todo o projeto**:
  `backend/app/collection/browser.py:47`
  (`self._playwright.chromium.launch(...)`), dentro de
  `BrowserSession.__aenter__`.
- **Único ponto de chamada de `BrowserSession`**:
  `backend/app/collection/providers/base.py:146`, sempre via
  `async with BrowserSession(...)  as session:` — gerenciador de
  contexto assíncrono, `__aexit__` sempre executa `close()`, mesmo em
  exceção.
- `BrowserSession.close()` (linhas 77-92) já usa `try/finally` aninhado
  corretamente: `context.close()` → (sempre) `browser.close()` →
  (sempre) `playwright.stop()` — nenhum dos três pode ser pulado por uma
  exceção nos anteriores.
- `__aenter__` também trata falha parcial: se `chromium.launch`/
  `new_context` falhar no meio, o `except BaseException: await
  self.close(); raise` garante que o que já foi aberto é fechado antes
  de propagar o erro.

**Conclusão da auditoria de código**: não foi encontrado bug de cleanup
no código do próprio projeto — a estrutura já é exception-safe e tem um
único caminho de uso, sempre via gerenciador de contexto. Isso **reforça
a hipótese de reaping do PID 1** (o `browser.close()` do Playwright
encerra o processo principal do Chromium que ele lançou, mas processos
internos do próprio Chromium — `chrome-headless` como
renderer/GPU/utility, `chrome_crashpad` como handler de crash — podem já
ter sido reparented para o PID 1 do container antes do encerramento, e
um PID 1 sem semântica de init nunca dá `wait()` neles) — mas **não
prova essa causa por si só**; a comprovação exige a comparação
sem/com `init: true` (ver plano abaixo), porque também é preciso
descartar comportamento interno do próprio Chromium (ex.: watchdog do
`crashpad_handler` mantendo processos vivos por design) como fator
concorrente.

## Objetivo

Fazer o ciclo de vida correto (do projeto e/ou do container) encerrar os
processos do Chromium — não mascarar o sintoma com um job periódico que
mata processos.

**Não é aceitável como solução final:**

- Um cron/timer que mata processos zumbis periodicamente sem corrigir a
  causa.
- Assumir a causa por analogia sem o teste comparativo sem/com `init: true`.
- Aplicar `init: true` em qualquer serviço além do `collection_worker`
  sem necessidade demonstrada.

Uma defesa adicional para resíduos genuinamente inevitáveis (ex.: um
crash real do Chromium fora do controle do `try/finally` do projeto)
pode ser aceitável **como complemento**, nunca como substituto da
correção do ciclo de vida.

## Plano de investigação (nenhum item executado ainda)

1. **Contagem de processos** (via `/proc`, já demonstrado nesta TASK que
   funciona sem precisar de `ps`) antes, durante e depois de várias
   coletas controladas — não só uma.
2. **PID/PPID** de cada processo Chromium e seus filhos, rastreando
   reparenting ao longo do ciclo de vida de uma coleta.
3. **Processos órfãos**: confirmar em que momento exato (antes ou depois
   de `browser.close()`) cada processo filho perde o pai original.
4. **Comportamento do PID 1** do container (hoje o processo Python do
   worker, sem init) frente aos `SIGCHLD` desses processos.
5. **Cleanup do Playwright**: instrumentar temporariamente
   `BrowserSession.close()` (timestamps antes/depois de cada `close()`/
   `stop()`) para confirmar que o Playwright já considera o Chromium
   encerrado do seu próprio ponto de vista no momento em que os zumbis
   aparecem — não vira código definitivo automaticamente.
6. **Comportamento em exceção**: forçar uma falha dentro do `async with
   BrowserSession` (ex.: exceção de navegação) e comparar contagem de
   zumbis contra o caminho feliz.
7. **Comportamento em timeout**: forçar `navigation_timeout_ms`/
   `action_timeout_ms` estourarem e comparar.
8. **Cancelamento**: cancelar a tarefa `asyncio` no meio de uma coleta
   (simulando o `deadline_seconds` da TASK-079) e comparar.
9. **Reinício do worker**: confirmar se um `docker compose restart
   collection_worker` limpa os zumbis acumulados (esperado, já que eles
   morrem com o container) e se o comportamento de reaping do container
   novo é idêntico.
10. **Comparação objetiva sem/com `init: true`**: mesmo roteiro de
    coletas controladas, uma rodada com `HostConfig.Init=false` (baseline
    atual) e outra com `init: true` só no `collection_worker`
    (`compose.yaml`), mesmas condições, contando zumbis antes/depois em
    ambas.
11. **`crashpad_handler`**: investigar se o Chromium headless do
    Playwright pode ser lançado com o handler de crash desabilitado
    (reduz uma classe inteira de processos filhos sem alterar
    comportamento funcional) como mitigação independente do `init:
    true`, avaliada separadamente na comparação.
12. **Regressão de longa duração**: sequência prolongada de coletas
    reais (êxito e falha controlada) medindo se a contagem de zumbis
    cresce de forma limitada (esperado com `init: true`, se comprovado)
    ou ilimitada (comportamento atual).

## Critério de aceite obrigatório

Depois de várias coletas bem-sucedidas **e** falhas controladas
(navegação, timeout, cancelamento), o número de processos Chromium
residuais **não pode crescer indefinidamente** — cada ciclo de coleta
deve terminar com contagem de processos filhos igual (ou devolvida a)
zero no `collection_worker`, ou a correção não está completa.

## Testes de regressão a planejar

- Teste de integração (ou script de validação controlada, se não for
  praticável como teste automatizado) que executa N coletas reais
  seguidas (sucesso e falha misturados) e falha se a contagem de
  processos Chromium no container crescer entre o início e o fim da
  sequência.
- Teste específico de cancelamento (deadline da TASK-079 estourando no
  meio de uma coleta) confirmando zero processo residual depois do
  cleanup.
- Se a correção for `init: true`: teste/validação confirmando
  `HostConfig.Init=true` só no `collection_worker`, nenhum outro
  serviço alterado.

## Fora de escopo

- Qualquer alteração no mecanismo de autodeadlock já corrigido pela
  TASK-079 — esta TASK não toca fronteiras transacionais, `AsyncSession`
  nem o desenho de Fase A/B/C.
- Comportamento funcional dos providers (extração, seletores, retry).
- Qualquer serviço além do `collection_worker` (único que usa
  `BrowserSession`/Playwright).

## Impacto em banco/migration

Nenhum esperado — problema de orquestração de processos/container, não
de schema.

## Registro de implementação (2026-08-13)

**Baseline** (container novo, pós-recriação da validação da TASK-080):
0 processos Chromium, 0 zumbis.

**Reprodução (sem correção)**: missão real de 4 fontes (`RTX 4060`,
via `backend/scripts/validate_collection_worker.py seed`, script já
existente) → todos os 4 `collection_runs` `succeeded` → **13 zumbis**
depois (`chrome-headless`×5, `chrome_crashpad`×4, `chrome`×4, todos
`PPID=1`, todos estado `Z`). `Xvfb` (persistente, não é zumbi) intacto.
Confirma o achado já registrado na auditoria da Etapa 2 em escala real
(4 lojas, não só um ciclo manual).

**Causa**: `HostConfig.Init=null` — PID 1 do container era o processo
Python do worker direto, sem semântica de init/reaper. Auditoria do
código (`BrowserSession.close()`, `try/finally` aninhado,
`async with` em todo o único ponto de uso) não encontrou bug de
cleanup no projeto — consistente com reparenting de processos internos
do Chromium para um PID 1 sem reaper.

**Correção aplicada**: `init: true` no serviço `collection_worker`
(`compose.yaml`), nenhum outro serviço tocado (só ele lança
Chromium/Playwright). Container recriado (`docker compose up -d
--no-deps collection_worker`, sem rebuild de imagem — mudança é só de
configuração do Compose). `PID 1` passou a ser `docker-init`; o worker
Python virou `PID 7`, filho de `PID 1`.

**Confirmação pós-correção**: segunda missão real de 4 fontes
(`mouse gamer`, mesmo usuário de teste, 4 `collection_runs`
`succeeded`) → **0 zumbis**, 5 processos totais (`docker-init`, `sh`
do exec, `python`, `Xvfb`, mais um transitório).

**Desvio deliberado do plano de investigação original, por instrução
explícita do usuário** ("não quero teste, vai direto pra resolução"):
os itens 2-9, 11-12 do "Plano de investigação" (rastreamento fino de
PID/PPID por etapa, instrumentação de timestamps, exceção forçada,
timeout forçado, cancelamento, comparação com `crashpad_handler`
desabilitado, dois processos de worker, regressão de longa duração)
**não foram executados**. O que foi comprovado é mais estreito que o
"Critério de aceite obrigatório" original: uma comparação real
antes/depois com coletas bem-sucedidas (13→0 zumbis), não uma bateria
cobrindo sucesso+falha+timeout+cancelamento. Registrado aqui para não
passar a falsa impressão de que o roteiro completo foi cumprido.

**Riscos residuais não cobertos por esta validação**:
- Comportamento em falha/timeout/cancelamento de coleta não testado
  explicitamente com `init: true` — plausível que `tini` resolva
  igualmente (reaping independe do motivo do encerramento), mas não
  comprovado.
- Memória do host durante a validação: servidor de 8 GB já operando
  com pouca memória livre (~0,5 GB) por fatores externos a esta TASK
  (VM do Docker Desktop/WSL2 + processos do próprio ambiente) — as
  coletas desta validação não pioraram isso de forma perceptível, mas
  não há folga para testes mais pesados (dois workers simultâneos,
  sequência longa) sem risco real ao host.

**Nota operacional**: a missão de teste desta TASK reaproveitou o
usuário sintético `"TASK-062 Docker validation"` (já criado durante a
TASK-081 original) — segunda missão real (`mouse gamer`) também ficou
persistida na base de produção deste servidor.

## Critérios de aceite — status real

O "Critério de aceite obrigatório" original (zero crescimento após
sucesso **e** falha controlada) **não foi integralmente verificado** —
só o caminho de sucesso foi comparado antes/depois. Falha/timeout/
cancelamento ficam como validação pendente, não bloqueante para esta
correção por decisão explícita do usuário de priorizar a resolução
sobre o roteiro completo de teste.
