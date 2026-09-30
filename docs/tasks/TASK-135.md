# TASK-135 — Ponte do GG com o Telegram para aprovar troca de modelo de IA

Status: **Registrada (2026-09-30), planejada, NÃO iniciada.** Depende da TASK-135 do
César Core (`docs/task-135-model-updates-telegram-approval.md` em `C:\cesar-core`).
Só começa depois da TASK-133 (GG) e do Core ter as rotas administrativas prontas.

## Objetivo

Quando o Core detecta e testa um modelo novo de IA, o GG avisa o dono no Telegram, com
botões **Aprovar** / **Recusar**, devolve a decisão ao Core e avisa quando houver troca
ou reversão automática. Decisões do usuário: o GG faz a ponte (o Core não tem bot); sem
trava de modelo estável (`DEC-142`); reversão automática obrigatória no Core.

## Escopo proposto

1. **Credencial administrativa nova e mínima** do GG para o Core (arquivo fora do Git,
   só o usuário coloca o valor; nunca aparece no chat).
2. **Consulta periódica** ao Core (proposta: a cada 5 minutos) das trocas pendentes,
   aplicadas e revertidas.
3. **Mensagem no Telegram do dono** (só o dono, identidade já vinculada): passo,
   modelo atual → novo, estável ou preview, resultado do teste, custo de cota e botões
   Aprovar / Recusar; aprovação com prazo de validade.
4. **Devolver a decisão ao Core** (`approve`/`reject`) e comando para **desfazer** uma
   troca já aplicada.
5. **Avisar** quando o Core trocar e quando reverter sozinho, com o motivo e os números.
6. Testes com o Core simulado; o formato real das rotas só se fecha quando a TASK-135
   do Core definir o contrato.

## Perguntas em aberto

1. Nome da credencial administrativa e onde fica configurada.
2. Como o GG identifica "o dono" para receber os avisos (conta ADMIN ligada ao Telegram).
