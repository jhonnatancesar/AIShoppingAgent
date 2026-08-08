# TASK-037 — Criar preferências de notificações

Status: Concluída em 2026-08-08

## Objetivo

Permitir que cada usuário consulte, ative e desative separadamente as
notificações Telegram de queda de preço e de preço-alvo atingido, sem alterar
seu cadastro ou o destino privado definido na TASK-036.

## Escopo

- Criar o comando textual `/preferencias`, processado sem IA e sem botões.
- Consultar o estado atual e aceitar somente:
  `/preferencias quedas ativar|desativar` e
  `/preferencias alvo ativar|desativar`.
- Persistir `notify_price_decreases` e `notify_target_reached` em `users`,
  ambos obrigatórios e ativados por padrão, inclusive para usuários existentes.
- Manter a entrega exclusivamente no chat privado da própria pessoa.
- Quando uma preferência impedir o envio, registrar `skipped`, sem
  `failure_code`. `succeeded` e `skipped` são terminais para o par
  evento/consumidor; `failed` continua elegível para retry.
- Um evento `skipped` nunca fica pendente nem volta após reativação; somente
  eventos novos podem ser enviados a partir desse momento.
- Não alterar `favorite_stores`, `preferred_categories`, e-mail, autenticação,
  cadastro da TASK-060 nem apresentar teclado ou botões.

## Critério de aceite

- Consulta e alterações independentes funcionam pelo comando real do bot.
- Preferências existentes e novas iniciam ativadas.
- Eventos bloqueados ficam terminalmente `skipped`; reativação não recupera
  eventos antigos.
- PostgreSQL real, Telegram real, suíte completa e Docker/Linux aprovados.

## Implementação e validação

- Revisão Alembic `20260808_0006`, modelo `User` e enum
  `ConsumptionOutcome.SKIPPED`.
- `app.telegram.preferences` concentra parsing, apresentação e decisão por
  tipo de evento; o cadastro da TASK-060 não foi modificado.
- O índice parcial terminal cobre `succeeded` e `skipped`; tentativas continuam
  append-only.
- Ciclo PostgreSQL 18 isolado `upgrade → downgrade → upgrade` aprovado, com
  defaults `true` confirmados para usuário preexistente e head único.
- Menu real do bot atualizado. Validação real: dois comandos processados, dois
  eventos `skipped`, um evento novo entregue após reativação e zero pendências;
  dados de ensaio revertidos.
- Imagem Linux reconstruída e smoke test do contrato executado com sucesso no
  Docker, sem processar eventos reais pendentes.
- Pipeline completo: 429 testes aprovados e 94,97% de cobertura; Alembic
  confirmou que a metadata não possui operações novas não migradas.

## Próxima tarefa no fluxo

TASK-038 — Definir fluxo de recomendação.

