# TASK-066 — Auditar restart policy dos serviços Docker

Status: **Concluída em 2026-08-10**, decisão aprovada explicitamente pelo
usuário (aplicar aos 5 serviços restantes), implementada e validada.

Dependência: nenhuma. Segunda TASK da `v1.0.2` (`docs/V1_0_2.md`, item 2);
independente da TASK-065 (já concluída).

## Contexto

`DEC-052` registrou que, dos 7 serviços de `compose.yaml`, só
`collection_worker` e `telegram_notifier` têm `restart: unless-stopped`;
`database`, `api`, `otel-collector`, `prometheus` e `jaeger` não têm
política de restart definida e exigem `docker compose up -d` manual após
reboot do host, queda de energia, restart do Docker Engine ou crash de
container. `docs/PRODUCTION_SETUP.md` (seção 15) já documenta esse
comportamento como o estado real da `v1.0.1` em produção. O item 2 da
`v1.0.2` pede uma auditoria serviço por serviço, aplicando
`restart: unless-stopped` **só onde for tecnicamente apropriado** — sem
aplicar cegamente aos sete.

## Objetivo

Decidir, com justificativa técnica por serviço, quais dos 5 serviços sem
política de restart definida devem receber `restart: unless-stopped`, e
aplicar a decisão em `compose.yaml`.

## Escopo

1. Auditar os 7 serviços (`database`, `api`, `collection_worker`,
   `telegram_notifier`, `prometheus`, `jaeger`, `otel-collector`),
   considerando função, criticidade, dependências (`depends_on`) e volume
   de dados.
2. Propor e aplicar `restart: unless-stopped` só onde justificado.
3. Atualizar a documentação operacional afetada
   (`docs/PRODUCTION_SETUP.md` seção 15, e qualquer outra referência ao
   comportamento atual).
4. Não alterar nenhum outro campo de `compose.yaml` além da política de
   restart.
5. Não tocar em produção (servidor real da `v1.0.1`) — a mudança fica só
   no repositório; aplicar no servidor é uma atualização operacional
   separada, fora desta TASK.
6. Não criar tag `v1.0.2`.
7. Não iniciar a TASK-067 automaticamente.

## Auditoria (2026-08-10)

### Estado atual confirmado em `compose.yaml`

| Serviço | `restart` hoje | `depends_on` | Healthcheck |
| --- | --- | --- | --- |
| `database` | *(nenhum)* | — | `pg_isready`, 5s |
| `api` | *(nenhum)* | `database` (healthy) | `GET /ready`, 5s |
| `collection_worker` | `unless-stopped` | `database` (healthy) | `GET :9464/metrics`, 10s |
| `telegram_notifier` | `unless-stopped` | `database` (healthy) | `GET :9464/metrics`, 10s |
| `otel-collector` | *(nenhum)* | `jaeger` (started) | sem healthcheck no Compose |
| `prometheus` | *(nenhum)* | `api` (healthy) | `GET /-/healthy`, 10s |
| `jaeger` | *(nenhum)* | — | `GET /api/services`, 10s |

### Achado principal: a configuração atual já é inconsistente

`collection_worker` e `telegram_notifier` têm `restart: unless-stopped` e
**ambos dependem de `database` estar saudável** — mas `database` não tem
política de restart. `depends_on`/`condition: service_healthy` só é
respeitado pelo Compose CLI durante `docker compose up`; a política de
restart do Docker Engine (usada após reboot do host ou restart do
serviço `docker`) reinicia cada container isoladamente, sem essa
ordenação. Ou seja: hoje, depois de um reboot real, `collection_worker` e
`telegram_notifier` já tentam voltar sozinhos, mas encontram o
`database` parado — o auto-restart configurado neles é parcialmente
inútil até alguém subir o banco manualmente. Isso por si só já justifica
tecnicamente incluir `database` nesta correção, e não é uma escolha
arbitrária "aplicar a mais um serviço".

### Recomendação por serviço

| Serviço | Aplicar `unless-stopped`? | Justificativa técnica |
| --- | --- | --- |
| `database` | **Sim** | Resolve a inconsistência acima; é o serviço do qual todos os outros seis dependem direta ou indiretamente (`api`→`database`; `prometheus`→`api`); sem ele voltar sozinho, nenhum auto-restart dos demais tem efeito prático completo. PostgreSQL é seguro para restart automático (WAL/crash recovery nativo do motor). |
| `api` | **Sim** | Caminho crítico voltado ao usuário — serve o webhook do Telegram e os endpoints HTTP; hoje é o único serviço de negócio sem recuperação automática. Sem `api` de volta, cadastro/login/missão ficam indisponíveis mesmo com `collection_worker`/`telegram_notifier` funcionando. |
| `collection_worker` | já tem | Mantido sem alteração. |
| `telegram_notifier` | já tem | Mantido sem alteração. |
| `otel-collector` | **Sim** | Container stateless (só encaminha OTLP→Jaeger); nenhuma razão técnica para exigir intervenção manual; reinício automático não tem efeito colateral. |
| `prometheus` | **Sim** | Métricas ficam em volume nomeado (`prometheus_data`, sobrevive a restart do container); perder coleta durante o downtime é aceitável (`docs/PRODUCTION_SETUP.md` seção 14 — "não são dado de negócio"), mas manter o serviço no ar automaticamente evita uma lacuna maior de observabilidade sem motivo para excluí-lo. |
| `jaeger` | **Sim** | Armazenamento em memória (traces não sobrevivem a restart, por design já documentado — `docs/OPERATIONS.md`), mas isso não é razão para excluir o *serviço* do auto-restart — só significa que o histórico de traces anterior ao restart se perde, o que já é esperado e aceito hoje mesmo em restart manual. |

**Conclusão da auditoria**: não encontrei nenhum dos 7 serviços com uma
razão técnica genuína para permanecer sem `restart: unless-stopped` — são
todos processos de longa duração (nenhum job pontual/migration), nenhum
tem efeito colateral destrutivo em reinício automático, e o principal
achado (dependência de `database` já configurada para auto-restart nos
outros dois) reforça que a política deveria ser uniforme. A ressalva do
`DEC-052`/item 2 ("sem aplicar cegamente") foi tratada auditando cada um
individualmente — a conclusão de que todos qualificam é resultado da
auditoria, não uma aplicação automática sem análise.

## Decisão aprovada (2026-08-10)

O usuário aprovou explicitamente a opção recomendada: aplicar
`restart: unless-stopped` aos 5 serviços que não tinham a política
(`database`, `api`, `otel-collector`, `prometheus`, `jaeger`), deixando
os 7 serviços consistentes.

## Implementação (2026-08-10)

- **`compose.yaml`**: adicionada a linha `restart: unless-stopped` aos 5
  serviços (`database`, `api`, `otel-collector`, `prometheus`, `jaeger`).
  Nenhum outro campo alterado — diff confirmado como só essas 5 linhas
  novas.
- **`docs/PRODUCTION_SETUP.md`**: seção "Serviços" (achado da auditoria)
  atualizada de "Correção planejada" para "Correção aplicada"; seção 15
  ("Inicialização após reboot") reescrita para descrever os 7 serviços
  com `restart: unless-stopped`, mantendo a recomendação de confirmar com
  `docker compose up -d` mesmo assim.
- **`docs/V1_0_2.md`**: item 2 marcado como concluído.

## Validação (2026-08-10)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **752 testes (90,63% cobertura)**, validação de sintaxe do
  Compose (`docker compose config --quiet`), migration head
  `20260809_0004`, **14 testes de integração PostgreSQL reais** — todos
  aprovados (`Pipeline local aprovado.`).
- **Validação funcional real, isolada e local (não produção)**: subi
  `database` e `jaeger` via `docker compose up -d` (sem build de imagem
  própria — só imagens oficiais), confirmei por `docker inspect` que os
  containers reais têm `HostConfig.RestartPolicy.Name=unless-stopped`.
  Simulei um **crash real** (`docker exec ... kill -9 1`, matando o
  processo interno do container — diferente de `docker stop`/`docker
  kill` no nível do Engine, que o Docker trata como parada intencional e
  **não** aciona `unless-stopped`) e confirmei que os dois containers
  voltaram sozinhos e ficaram `healthy` em poucos segundos, sem nenhuma
  intervenção manual — validando o cenário real de "crash de container"
  que motivou o item 2. Ambiente de teste (containers, volume, rede)
  removido depois (`docker compose down -v`), sem afetar nada além dele
  mesmo.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1` — a mudança só existe no repositório; aplicar no servidor fica
  para uma atualização operacional futura, fora desta TASK.
- **Nenhuma tag `v1.0.2` criada.**

## Encerramento

Concluída em 2026-08-10. Os 7 serviços de `compose.yaml` têm
`restart: unless-stopped`, decisão fundamentada por serviço e aprovada
explicitamente. Validação real confirmou a recuperação automática após
crash simulado. Produção da `v1.0.1` intocada; TASK-067 não iniciada.

## Fora do escopo desta TASK

- Instalar unidade systemd para subir `docker compose up -d` automaticamente
  no boot (`docs/OPERATIONS.md` já registra que a V1 não faz isso) — item
  não pedido, ampliaria escopo.
- Qualquer alteração em produção.
- TASK-067 a TASK-069.
