# Telegram

Telegram é o canal conversacional previsto para o MVP. O adaptador deve traduzir mensagens em comandos ou intenções sem conter lógica de domínio.

A fronteira de entrada que traduz uma mensagem bruta do Telegram em uma
intenção estruturada está definida em `docs/TELEGRAM_ADAPTER.md`.
Notificações proativas de alertas foram implementadas na TASK-036. A TASK-046
autentica minimamente a identidade do canal: somente mensagens privadas da
própria pessoa e contas internas ativas podem operar. A TASK-061 acrescenta
senha e sessão persistente por formulário HTTPS; MFA permanece futuro.

## Fronteira de autorização (TASK-047)

Depois da TASK-046, o webhook exige uma permissão do papel persistido antes de
IA, leitura ou mutação funcional. `USER`, `ADMIN` e `DEV` formam a hierarquia
estrita `USER ⊂ ADMIN ⊂ DEV`, mas todos continuam limitados aos próprios
recursos. Papel ausente/desconhecido e recurso alheio falham fechados.

Uma recusa retorna `204`, não envia resposta e não atualiza o destino privado
nem o estado funcional. Somente `authorization.denied` é acrescentado à
auditoria com campos controlados. Papel nunca é aceito da mensagem, payload,
cadastro ou comando; novos contatos continuam sendo provisionados como USER.
Detalhes estão em `docs/AUTHORIZATION.md`.

## Fronteira de autenticação do canal (TASK-046)

O webhook primeiro autentica o transporte pelo segredo compartilhado. Somente
depois confia em `message.from.id`, e apenas quando a conversa é `private` e
`chat.id == message.from.id`. Grupo, supergrupo, canal ou identidade divergente
não provisionam usuário. `is_active=false` bloqueia IA, missões, cadastro,
preferências, resposta e atualização do destino.

Essas recusas devolvem `204`, portanto são terminais para a entrega, e registram
somente `non_private_chat`, `identity_mismatch` ou `inactive_user`, sem IDs,
texto ou payload. O segredo do webhook autentica o Telegram, não o usuário
final; a sessão por senha da TASK-061 é uma camada posterior e independente.

## Notificações proativas (TASK-036)

O webhook memoriza como destino somente conversas que a Bot API classifica
como `private` e cujo `chat.id` coincide com a identidade da pessoa. Grupos,
supergrupos e canais nunca são persistidos como destino automático.

O processo `app.telegram.worker` consome `price.decreased.v1` e
`price.target_reached.v1` pelo contrato at-least-once da TASK-044. Cada envio
gera uma tentativa append-only. Depois da TASK-049, somente falhas transitórias
inequívocas podem ser tentadas novamente; timeout ambíguo não provoca reenvio
cego. Erros permanentes e tentativas esgotadas geram `dead_lettered`. Detalhes
operacionais estão em
`docs/EVENT_CONSUMPTION.md`.

## Replay e rate limit (TASK-049)

Depois de autenticar transporte e identidade, o webhook persiste um recibo
terminal por `update_id`. Recibo aceito e efeito funcional fazem commit juntos;
rollback remove ambos. Replay retorna `204` sem repetir efeito, IA, cota ou
resposta. Cada usuário aceita 20 updates por minuto; excesso persiste
`rate_limited`, retorna `204` e não executa domínio/IA. Recusas de política
autenticadas usam `discarded`.

## Comandos dedicados

Dez comandos são registrados no menu. Os comandos de autenticação, perfil e
privacidade
são reconhecidos diretamente pelo webhook, antes de qualquer
interpretação por IA — não passam pelo vocabulário fechado do
`IntentInterpreter`:

- `/cadastro`: inicia (ou reinicia) o cadastro inicial não sensível, com
  passos sequenciais (nome de usuário, e-mail, lojas favoritas,
  preferências de categoria). Detalhes em `docs/USERS.md`.
- `/upgrade`: existe e aparece no menu do bot, mas responde apenas que a
  função está "em breve" — nenhuma lógica real de mudança de plano ou
  perfil está implementada.
- `/preferencias` (TASK-037): consulta as notificações e aceita
  `quedas ativar|desativar` ou `alvo ativar|desativar`. Não altera cadastro,
  e-mail, autenticação ou fontes e não usa botões.
- `/privacidade` (TASK-050): resposta fixa e curta sobre uso de dados,
  terceiros, retenção e desidentificação; funciona sem IA e sem sessão por
  senha e não envia dados pessoais a provider externo. Detalhes em
  `docs/PRIVACY.md`.
- `/senha`, `/entrar`, `/sair` e `/recuperar` (TASK-061): emitem link HTTPS,
  estabelecem/revogam sessão ou recuperam a senha sem receber segredo no chat.
  Detalhes em `docs/AUTHENTICATION.md`.
- `/start` e `/ajuda`: orientação de onboarding e autenticação.

Sem sessão por senha, permanecem disponíveis `/start`, `/ajuda`, `/cadastro`,
`/senha`, `/entrar`, `/recuperar` e `/privacidade`, sempre depois das TASKs
046/047. Missões,
preferências, IA, recomendações, comparações, confirmações, trilha,
`/upgrade` e demais efeitos funcionais exigem sessão válida.

As preferências começam ativadas. Um alerta bloqueado é consumido como
`skipped`, sem retry nem reenvio retroativo. A entrega continua restrita ao
chat privado da própria pessoa.

O perfil de IA usado por uma interação real (`USER` vs `ADMIN`/`DEV`) é
decidido pelo `User.role` já resolvido, nunca por escolha do próprio
usuário — ver `docs/AI_PROVIDER_MANAGER.md`.

## Seleção de fontes de busca

Quando o bot oferecer a escolha de fontes para uma missão, deverá apresentar:

- Pichau;
- Terabyte;
- Amazon;
- Kabum.

Logo abaixo, em seção visualmente separada:

***Futuro***

- Mercado Livre (ML);
- Shopee;
- AliExpress.

As quatro primeiras opções podem ser selecionadas individualmente ou em
conjunto. As fontes futuras são apenas informativas, não podem ser selecionadas
e não disparam coleta na V1. A apresentação será implementada com os comandos do
bot na TASK-035; os providers pertencem à TASK-055.
