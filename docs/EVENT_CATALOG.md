# Catálogo de eventos da V1

O catálogo é a lista fechada de fatos de domínio que podem ser registrados na
V1. O nome do evento faz parte do contrato e termina com uma versão (`.v1`). Uma
mudança incompatível exige um novo nome versionado; eventos já gravados nunca são
reescritos.

| Evento | Agregado | Payload obrigatório |
| --- | --- | --- |
| `mission.status_changed.v1` | `mission` | missão, transição, estados anterior/novo e versão |
| `collection.completed.v1` | `collection_run` | coleta, loja, missão opcional e quantidade de observações |
| `collection.failed.v1` | `collection_run` | coleta, loja, missão opcional e código seguro da falha |
| `price.decreased.v1` | `offer` | oferta, observações anterior/atual, totais e moeda |
| `price.target_reached.v1` | `mission` | missão, oferta, observação, alvo, total atual e moeda |
| `offer.availability_changed.v1` | `offer` | oferta, observações e disponibilidades anterior/atual |
| `authentication.completed.v1` | `user` | usuário e ação fechada concluída |
| `authentication.session_expiring.v1` | `auth_session` | sessão, usuário e vencimento exato |
| `authentication.session_expired.v1` | `auth_session` | sessão, usuário e vencimento exato |
| `mission.prelist_ready.v1` | `mission` | missão, até 2 ofertas (a mais barata primeiro), totais e moeda |
| `mission.prelist_errata.v1` | `mission` | missão, oferta, observação, total atual/moeda, base anterior opcional |

## Regras

- Valores monetários usam `Decimal`, nunca `float`, e moeda ISO 4217 em letras
  ASCII maiúsculas.
- Queda exige total atual estritamente menor; alvo atingido aceita total atual
  menor ou igual ao alvo.
- Mudanças de estado e disponibilidade exigem valores anterior e atual distintos.
- `failure_code` é um código estável em `snake_case` e sanitizado; exceções, HTML, credenciais,
  tokens e dados pessoais não pertencem ao payload.
- Identificadores relacionam o fato às fontes persistentes; cópias integrais de
  entidades e evidências brutas não pertencem ao evento.
- Eventos de autenticação nunca carregam senha, hash, token, chat ID ou
  credencial. `expires_at` é timezone-aware e a ação usa `CredentialAction`.
- `mission.prelist_ready.v1` (TASK-068) aceita 1 ou 2 ofertas; quando há duas, a
  primeira é sempre a mais barata (`first_total <= second_total`) e as duas
  ofertas devem ser distintas. `mission.prelist_errata.v1` exige `current_total`
  estritamente menor que `previous_lowest_total` quando este não é `None`
  (`None` só no caso de a pré-lista original não ter tido nenhuma oferta para
  mostrar). Nenhum dos dois é gerado por IA — reaproveitam a classificação
  `MATCH` já calculada pela TASK-063.

O contrato executável está em `app.events`. A TASK-042 não cria a tabela
`events`, não detecta fatos, não publica, não consome e não notifica. Alertas de
preço pertencem à TASK-027; persistência/publicação e consumo pertencem às
TASKs 043 e 044. A TASK-036 entrega pelo Telegram os dois eventos de alerta de
preço quando publicados com uma missão destinatária. A TASK-068 entrega a
pré-lista informativa (sem IA) num consumidor Telegram próprio
(`telegram_prelist_v1`), separado dos alertas de queda/alvo.
