# Telegram

Telegram é o canal conversacional previsto para o MVP. O adaptador deve traduzir mensagens em comandos ou intenções sem conter lógica de domínio.

A fronteira de entrada que traduz uma mensagem bruta do Telegram em uma
intenção estruturada está definida em `docs/TELEGRAM_ADAPTER.md`.
Autenticação real, sessões e notificações proativas continuam reservadas a
tarefas posteriores (TASK-036, TASK-061).

## Comandos dedicados (TASK-060)

Dois comandos são reconhecidos diretamente pelo webhook, antes de qualquer
interpretação por IA — não passam pelo vocabulário fechado do
`IntentInterpreter`:

- `/cadastro`: inicia (ou reinicia) o cadastro inicial não sensível, com
  passos sequenciais (nome de usuário, e-mail, lojas favoritas,
  preferências de categoria). Detalhes em `docs/USERS.md`.
- `/upgrade`: existe e aparece no menu do bot, mas responde apenas que a
  função está "em breve" — nenhuma lógica real de mudança de plano ou
  perfil está implementada.

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
