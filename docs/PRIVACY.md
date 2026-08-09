# Privacidade técnica da V1

Este documento descreve o comportamento implementado. Ele não constitui
certificação jurídica, não afirma conformidade integral com a LGPD e não
substitui avaliação jurídica da instância que operar o sistema.

## Inventário e finalidade

| Categoria | Dados | Finalidade e armazenamento |
| --- | --- | --- |
| Conta | UUID interno, nome, papel, Telegram user/chat ID, username, e-mail opcional | Identificar a conta, autorizar o acesso e entregar respostas; PostgreSQL. |
| Preferências | Lojas/categorias informadas e opções de notificação | Personalizar cadastro e alertas; PostgreSQL. |
| Autenticação | Hash Argon2id, hashes de tokens, sessões e datas de segurança | Autenticar sem persistir senha ou token em claro; PostgreSQL. |
| Missões | Título, busca, fontes, alvo, agenda, transições e intenção pendente | Executar e rastrear a missão; PostgreSQL. Textos livres podem revelar interesses e não devem conter PII desnecessária. |
| Preço e compra | Produtos, ofertas, observações, evidências, recomendação e confirmação de consentimento | Comparar preços e preservar proveniência; PostgreSQL. Não há compra, pagamento ou checkout. |
| Eventos | IDs técnicos, valores, disponibilidade, estado e códigos fechados | Publicação, consumo e notificação rastreáveis; PostgreSQL append-only. |
| Telemetria | Request/trace/span ID e códigos operacionais limitados | Diagnóstico; logs Docker, Prometheus e Jaeger. |

`telegram_update_receipts` preserva `update_id`, UUID interno e disposição para
impedir replay e aplicar limite. A aplicação não persiste o corpo completo do
Update do Telegram nem o texto das notificações enviadas.

## Compartilhamento necessário

- **Telegram:** recebe respostas e alertas no chat privado já vinculado.
- **Gemini e, para ADMIN/DEV, Groq quando habilitado:** recebem somente o texto
  necessário para interpretar intenção ou confirmação. UUID interno, IDs do
  Telegram, e-mail, papel e material de autenticação não são acrescentados ao
  prompt. O usuário não deve inserir PII desnecessária no texto livre.
- **Pichau, Terabyte, Amazon e Kabum:** recebem o termo de busca necessário nas
  consultas às fontes selecionadas. Mercado Livre, Shopee e AliExpress não são
  consultados na V1.

Políticas e prazos próprios desses terceiros precisam ser avaliados pelo
responsável pela instância antes da produção; o projeto não promete controlá-los.

## Logs, métricas e traces

Logs omitem Telegram/user ID, nome, username, e-mail, URL completa, texto do
usuário, query, payload, token, senha/hash e valores SQL. Exceções expõem apenas
a classe segura, nunca mensagem ou traceback bruto. `request_id`, `trace_id` e
`span_id` são correlação observacional, não identidade ou autorização.

Defaults operacionais da V1, não prazos jurídicos definitivos:

- logs Docker: `json-file`, no máximo 5 arquivos de 10 MB por container;
- Prometheus: no máximo 15 dias ou 2 GB, o limite atingido primeiro;
- Jaeger: memória volátil, no máximo 10.000 traces e container limitado a
  512 MB; traces não sobrevivem ao restart.

A aplicação não mantém tabela própria de logs. Logs anteriores à implantação
desses limites deixam de existir conforme a rotação do Docker; a TASK-050 não
apaga silenciosamente arquivos operacionais do host.

Backups PostgreSQL também contêm dados potencialmente pessoais. O procedimento
manual da TASK-051 exige diretório privado, arquivo `0600` e restauração de
prova em banco limpo; retenção, criptografia e cópia externa continuam sob
responsabilidade do operador. Consulte `docs/OPERATIONS.md`.

## Retenção de autenticação

- action tokens expirados, consumidos ou invalidados ficam elegíveis para
  limpeza 24 horas depois;
- sessões expiradas ou revogadas ficam elegíveis após 30 dias;
- a limpeza é manual, sem scheduler:

```powershell
cd backend
python -m app.privacy.cli cleanup-auth --execute
```

O procedimento apaga somente tokens e sessões. Auditoria sanitizada de ações de
segurança permanece append-only e não contém senha, hash ou token.

## Desidentificação da conta

A V1 possui uma operação local e controlada, não exposta por Telegram ou API:

```powershell
cd backend
python -m app.privacy.cli deidentify-account --user-id <UUID> --execute
```

O Telegram ID esperado é solicitado com entrada oculta e a execução exige a
confirmação textual `DESIDENTIFICAR`. Na mesma transação, a operação:

- apaga credencial, tokens e sessões;
- remove Telegram user/chat ID, username, e-mail e nome;
- limpa preferências pessoais, cadastro em andamento e `pending_intent`;
- desativa conta, notificações e agendas;
- neutraliza títulos e buscas mutáveis das missões;
- reduz o papel para `USER`;
- acrescenta `privacy.account_deidentified` com metadata fixa e sanitizada.

Antes da primeira alteração, snapshots, JSONB e textos históricos relacionados
são verificados contra os identificadores diretos conhecidos. Qualquer PII em
estrutura append-only ou razão livre de transição interrompe toda a transação.
Triggers e decisões de imutabilidade nunca são enfraquecidos automaticamente.

## Dados históricos preservados

Continuam preservados por integridade e proveniência:

- UUID interno do usuário;
- IDs relacionais e recibos de replay;
- transições, auditoria, eventos e tentativas de consumo append-only;
- observações e evidências históricas de preço;
- confirmações e trilha de consentimento de compra;
- relações técnicas necessárias às FKs `RESTRICT`.

Os snapshots de compra contêm produto, loja, vendedor opcional, oferta e
observação, não nome, e-mail ou Telegram ID do proprietário. Payloads de evento
usam catálogo fechado e IDs técnicos. `audit_entries.metadata` aceita somente
contexto controlado e sanitizado.

Esses registros permanecem correlacionáveis pelo UUID interno. O mecanismo é,
portanto, **desidentificação da conta/pseudonimização operacional**, não garantia
de anonimização irreversível. Uma avaliação futura específica poderá decidir se
uma base pode ser efetivamente anonimizada ou eliminada sem destruir a
integridade exigida.

## Comando `/privacidade`

O comando é uma resposta fixa em português, funciona antes da sessão por senha,
não usa IA e não envia dados pessoais a provider. Ele resume coleta,
compartilhamento e desidentificação e aponta para este documento. Não promete
exclusão integral, anonimização irreversível, prazo não implementado ou
conformidade jurídica certificada.

## Referências informativas

- [Princípios da LGPD](https://www.gov.br/saude/pt-br/acesso-a-informacao/lgpd/principios)
- [Lei nº 13.709/2018](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709compilado.htm)
