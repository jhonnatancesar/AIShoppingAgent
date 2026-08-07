# Telegram

Telegram é o canal conversacional previsto para o MVP. O adaptador deve traduzir mensagens em comandos ou intenções sem conter lógica de domínio.

A fronteira de entrada que traduz uma mensagem bruta do Telegram em uma
intenção estruturada está definida em `docs/TELEGRAM_ADAPTER.md`.
Autenticação, webhooks, comandos e notificações serão especificados e implementados em tarefas posteriores.

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
