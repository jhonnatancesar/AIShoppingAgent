# Ideia futura — aba pública "Históricos de preços" na landing page

Registrada em 2026-09-26, a pedido do usuário, ao fim da TASK-125. **Não
é uma TASK**: é só o registro da ideia, sem escopo técnico definido e sem
implementação.

## O pedido, nas palavras do usuário

> "quero pôr na landing page uma aba históricos de preços e deixar a
> pessoa dar uma olhada, porque pretendo pôr uns itens para ficar full
> time pesquisando e aí as pessoas podem ver o gráfico do preço por dia,
> mês e ano sem precisar da conta do GG, mas somente ver esses dados
> mesmo"

## O que fica definido

- Aba pública na landing page, **sem login**.
- Só leitura: a pessoa vê o gráfico de preço por dia, mês e ano e nada
  mais.
- Os itens são escolhidos pelo dono do produto e ficam sendo pesquisados
  em tempo integral.

## Perguntas em aberto (responder antes de virar TASK)

- Como o dono escolhe os itens públicos: tela DEV, lista fixa, uma missão
  especial "pública"?
- O gráfico público reaproveita o de histórico (`PriceHistoryChart`), com
  o preço com cupom da TASK-125, ou uma versão simplificada?
- O que nunca pode aparecer em público: dados de usuários, missões, links
  de afiliado, preço de loja sem permissão.
- Limite de acesso, cache e custo: página pública sem login pode receber
  muito tráfego.
- Quais lojas e períodos aparecem, e com que frequência os itens públicos
  são coletados (cotas, fila justa da TASK-108).
