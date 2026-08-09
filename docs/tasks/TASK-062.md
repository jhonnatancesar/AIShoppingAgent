# TASK-062 — Orquestrar coletas das missões ativas

Status: Concluída em 2026-08-09

## Classificação

Nova TASK do MVP. Não representa expansão opcional: fecha a lacuna funcional
que impede o fluxo principal da V1 de executar automaticamente. Deve ser
concluída antes da TASK-053.

## Objetivo

Conectar, sem redesenhar, as capacidades já existentes:

`missão ativa → fontes selecionadas → Store Providers → normalização →`
`observações → avaliação → event log → consumidor/notifier existente`.

A responsabilidade da TASK termina na publicação durável dos eventos. O
orquestrador nunca chama Telegram diretamente.

## Auditoria do código existente

- `find_due_schedules` já seleciona agendas habilitadas de missões ativas e não
  expiradas, em ordem determinística, com `FOR UPDATE SKIP LOCKED`;
- `advance_schedule` já elimina backlog retroativo e preserva a cadência;
- missões criadas por `create_mission_from_criteria` ainda não recebem
  `MissionSchedule`, portanto não entram automaticamente na agenda;
- não existe worker de coleta; o worker atual é específico do consumo e envio
  Telegram e não deve receber essa segunda responsabilidade;
- `CollectionAdapter`, os quatro providers, `PriceNormalizer`,
  `CollectionRun`, `PriceObservation`, `evaluate_price_alerts` e
  `publish_event` existem, mas não há serviço que conecte essas fronteiras;
- a persistência atual inicia/finaliza `CollectionRun`, porém ainda não resolve
  de forma transacional `Product`, `Seller`, `Offer` e `PriceObservation` a
  partir de um resultado normalizado;
- o schema não impede declarativamente duas observações da mesma oferta na
  mesma execução nem duas execuções `running` da mesma missão/fonte;
- os providers já possuem retry seguro e circuit breaker da TASK-049;
- o notifier já consome somente eventos de preço pelo event log append-only.

## Decisões implementadas

### 1. Agenda e elegibilidade

- criar `MissionSchedule` na mesma transação de toda nova missão criada pelo
  fluxo oficial;
- usar intervalo padrão configurável de 60 minutos e `next_run_at` igual ao
  instante de criação, tornando a primeira coleta imediatamente elegível;
- antes de reivindicar trabalho, criar de forma idempotente agendas ausentes
  para missões antigas que estejam ativas, não expiradas, com critérios e ao
  menos uma fonte; agenda existente desabilitada nunca é reativada;
- processar apenas missão ativa, não expirada, agenda habilitada, critério com
  `search_query` válido, fonte selecionada e `Store.is_active=true`;
- `draft`, `paused`, `completed`, `cancelled`, `expired`, agenda desabilitada,
  critério ausente/inválido ou ausência de fonte não geram coleta.

### 2. Claim curto e concorrência

- dentro de uma transação curta, bloquear agendas vencidas pelo mecanismo já
  existente, rejeitar missão com qualquer `CollectionRun.running`, avançar a
  agenda e criar um `CollectionRun.running` para cada fonte elegível;
- todos os runs do ciclo compartilham o mesmo `started_at`; a transação faz
  commit antes de Playwright, HTTP, espera ou backoff;
- adicionar, pela migration proposta `20260809_0001`, índice único parcial em
  `(mission_id, store_id)` para runs `running` e unicidade em
  `(collection_run_id, offer_id)` para observações;
- a constraint é a autoridade final; corridas de inserção serão tratadas por
  SAVEPOINT somente para a violação nomeada, sem capturar `IntegrityError`
  genericamente;
- runs `running` há mais de 10 minutos são encerrados como falha
  `stale_execution` antes de nova reivindicação. Um resultado externo atrasado
  só pode persistir se seu run ainda estiver `running`; caso contrário é
  descartado transacionalmente.

Isso evita manter row lock durante trabalho externo e não exige tabela nova de
lease ou sistema paralelo de scheduling.

### 3. Execução por fonte

- registrar exatamente Pichau, Terabyte, Amazon e Kabum no
  `CollectionAdapter` existente;
- executar as fontes reivindicadas concorrentemente, no máximo as quatro fontes
  fechadas da V1, mantendo um resultado independente por `CollectionRun`;
- usar o `RetryPolicy` e o circuit breaker já embutidos nos providers; o worker
  não adiciona outro retry em torno da navegação;
- falha de uma fonte finaliza apenas o run correspondente e não cancela nem
  reverte as demais.

### 4. Identidade e persistência

- normalizar cada `CollectionResult` integralmente pelo `PriceNormalizer`;
  normalização inválida torna aquele run falho, sem persistência parcial;
- resolver `Offer` primeiro por `(store, seller, external_id)` quando houver
  identificador, e por `(store, seller, url)` como identidade alternativa,
  respeitando os índices atuais;
- criar `Product` somente quando uma oferta realmente nova precisar dele,
  usando o título coletado e sem mesclar automaticamente nomes entre lojas;
- nunca deduplicar produtos por título, marca presumida ou modelo presumido;
- criar/resolver `Seller` somente quando existir identificador externo estável;
  nome de vendedor sem ID permanece evidência opcional, sem inferir identidade;
- usar SAVEPOINT ao criar identidades protegidas por índice único; numa corrida,
  reler a oferta vencedora e não deixar produto/vendedor órfão;
- não alterar automaticamente produto ou vendedor já persistido a partir de
  texto novo da página;
- inserir uma observação append-only por oferta/run, preservando valores
  normalizados, fulfillment, horário e evidência pública limitada;
- buscar a observação anterior pela ordem determinística já usada no histórico.

### 5. Avaliação e eventos

- na transação de sucesso da fonte: persistir observações, chamar
  `evaluate_price_alerts` para cada observação e publicar cada candidato com
  `publish_event`;
- publicar também `collection.completed.v1`, com a contagem realmente
  persistida, na mesma transação que finaliza o run como `succeeded`;
- em falha conhecida, finalizar o run e publicar `collection.failed.v1` com
  código fechado e sanitizado;
- não criar detector paralelo de preço, novo catálogo, broker ou chamada direta
  ao notifier;
- ausência legítima de ofertas termina como `succeeded`, contagem zero e evento
  de conclusão; não é inventado preço nem alerta;
- disponibilidade `unknown` continua `unknown` e não gera alerta. A TASK não
  pode inferir disponibilidade positiva apenas porque uma página respondeu.

### 6. Matriz de falhas

| Situação | Resultado do run | Código/efeito |
| --- | --- | --- |
| nenhuma oferta válida | `succeeded` | `collection.completed.v1`, contagem 0 |
| timeout/navegação indisponível após retry seguro | `failed` | `provider_unavailable` |
| circuit breaker aberto | `failed` | `circuit_open` |
| bloqueio permanente/CAPTCHA/403 | `failed` | `provider_blocked` |
| parsing ou normalização inválida | `failed` | `normalization_failed` |
| falha transacional antes do commit | permanece sem efeito parcial | não repetir chamada externa cegamente; tentar apenas registrar falha e, se o banco continuar indisponível, recuperar o run como stale |
| run perdeu validade durante chamada externa | `failed` pelo recuperador | resultado tardio descartado |

Os códigos não contêm URL, consulta, produto, UUID, mensagem de exceção ou dado
do usuário.

### 7. Processo operacional

- criar `app.collection.worker` como segundo comando da mesma imagem monolítica,
  seguindo o ciclo operacional do notifier sem misturar responsabilidades;
- adicionar serviço Compose `collection_worker`, com Xvfb já fornecido pela
  imagem e acesso somente ao secret do PostgreSQL;
- não conceder ao worker token Telegram, segredo de webhook ou chaves de IA;
- usar poll curto configurável, lote limitado e backoff de falha do worker já
  compatível com a política da TASK-049;
- adicionar target interno do Prometheus para suas métricas, sem porta pública.

### 8. Observabilidade e segurança

- usar worker allowlisted `collection_orchestrator` e somente outcomes fechados
  `succeeded`/`failed`; ausência de ofertas conta como sucesso;
- spans podem conter somente operação e `source_code` das quatro fontes
  allowlisted; logs contêm códigos e contagens;
- nunca usar em labels/attributes/logs: `mission_id`, `user_id`, título,
  consulta, produto, URL, seller, UUID ou mensagem bruta de exceção;
- o worker lê missões e fontes diretamente do banco, sem aceitar identidade,
  papel, missão ou parâmetro de segurança de payload Telegram/HTTP;
- toda consulta e persistência mantém `mission_id`, fonte e entidades
  relacionadas coerentes, sem bypass cross-user.

## Arquivos/módulos implementados

- `app.collection.orchestration`: claim, execução e persistência por fonte;
- `app.collection.worker`: loop operacional;
- `app.collection.persistence`: extensão dos serviços existentes, sem segundo
  modelo de armazenamento;
- `app.missions.service`: criação atômica da agenda padrão;
- `app.core.config`, Compose, Prometheus e métricas: somente configurações e
  observabilidade necessárias ao worker;
- migration `20260809_0001`: somente garantias de unicidade justificadas acima;
- testes unitários e integração PostgreSQL permanente da TASK-052.

## Validação executada

- missões ativa, pausada, cancelada, concluída, expirada e sem agenda;
- criação automática/idempotente da agenda e respeito à desativação explícita;
- múltiplas fontes e falha isolada de uma delas;
- normalização e persistência append-only sem duplicação por run/oferta;
- produto/oferta concorrentes sem órfãos nem duplicação;
- avaliação real e publicação dos eventos de preço;
- eventos reais de coleta concluída/falha;
- duas instâncias concorrentes com sessões/conexões PostgreSQL distintas;
- crash/run stale e descarte de resultado tardio;
- pipeline permanente da TASK-052 atualizado;
- validação real no Docker Linux/Xvfb contra as quatro fontes, sem stealth ou
  contorno de proteção;
- worker automático detectando uma missão elegível sem montagem manual da
  cadeia e deixando o evento publicado para o notifier existente;
- pipeline completo, revisão técnica, documentação e workflow Git.

## Fora do escopo

- novos providers/lojas, mudança de seletores por conveniência ou inferência
  otimista de disponibilidade;
- novo avaliador, event log, consumidor ou notifier;
- Telegram direto no orquestrador;
- IA, compra, reserva, checkout ou ação financeira;
- Celery, Redis, RabbitMQ, Kafka, cron externo ou arquitetura distribuída;
- APIs públicas, payloads de execução e funcionalidades de V2.

## Ordem

## Resultado

- migration `20260809_0001` aprovada em PostgreSQL 18.4 real com
  `upgrade → downgrade → upgrade`;
- testes permanentes provaram claim concorrente, backfill idempotente, missão
  pausada ignorada, falha isolada, observações e eventos reais;
- pipeline Docker Linux/Xvfb acessou Pichau, Terabyte, Amazon e Kabum reais;
- o `collection_worker` detectou sozinho uma missão sintética com quatro
  fontes, terminalizou quatro runs, persistiu 20 observações e quatro eventos;
- o container montou somente `/run/secrets/postgres_password`; Telegram,
  webhook e IA permaneceram ausentes;
- frete desconhecido continua persistido como evidência desconhecida e não
  produz alerta dependente de custo total;
- o ambiente descartável de validação foi removido depois da inspeção.
- pipeline oficial aprovado com Python 3.14.6, 638 testes rápidos, 90,04% de
  cobertura e 11 integrações PostgreSQL reais.

## Próxima tarefa no fluxo

TASK-053 — executar os testes E2E externos sobre o fluxo automático real. Ela
não foi iniciada automaticamente.
