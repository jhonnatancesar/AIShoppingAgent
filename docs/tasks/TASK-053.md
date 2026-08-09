# TASK-053 — Criar testes ponta a ponta

Status: Bloqueada externamente (`BLOCKED_EXTERNAL`)

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

## Critérios de aceite

- a missão nasce do webhook autenticado, passa pela interpretação e pela
  confirmação pública existente;
- a agenda é reivindicada pelo worker, sem chamada manual da cadeia interna;
- quatro fontes selecionadas geram runs terminais independentes;
- ao menos uma observação externa elegível (preço real disponível na moeda
  do critério, frete opcional) produz alerta de alvo e entrega Telegram pela
  cadeia normal;
- replay da mesma update/execução não repete efeitos;
- o mesmo evento possui no máximo um consumo terminal por consumidor;
- restart não reprocessa trabalho concluído, mas nova coleta legítima pode
  acrescentar nova observação histórica;
- preferência desativada produz `skipped` terminal e não reenvia ao reativar;
- ownership, privacidade da telemetria e diagnósticos sanitizados permanecem;
- pipeline completo, revisão, documentação e workflow Git aprovados.

## Fora do escopo

- arquitetura de V2, novos marketplaces ou contorno de proteção externa;
- fabricar observações/eventos ou inferir frete/disponibilidade;
- exigir queda de preço real durante a janela do teste;
- colocar dependências públicas no pipeline rápido cotidiano.

## Correções internas encontradas durante a execução

- o onboarding passou a listar lojas por números e aceitar `5` para todas;
- concluir `/cadastro` passou a emitir o link inicial de senha e orientar
  `/entrar` depois da criação;
- o mínimo de senha passou a oito caracteres, sem regra de composição, com as
  proteções existentes preservadas;
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

## Validações realizadas nesta execução

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

