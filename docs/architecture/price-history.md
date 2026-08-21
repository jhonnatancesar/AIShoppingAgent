# Histórico de preços

A TASK-017 disponibiliza consultas internas e somente leitura sobre as observações
imutáveis de preço. Ela não cria endpoints HTTP, relatórios, gráficos, alertas ou
comparações.

## Consultas

- `list_price_history` lista as observações de uma oferta, com filtros opcionais
  por intervalo inclusivo e disponibilidade.
- `get_latest_price_observation` retorna a observação mais recente ou `None`.
- A listagem usa `limit` de 1 a 100, `offset` não negativo e informa a contagem
  total do conjunto filtrado.
- A ordem é sempre `observed_at DESC, id ASC`, inclusive quando observações têm o
  mesmo horário.

Horários de filtro devem conter fuso. As consultas não modificam, deduplicam nem
descartam observações e preservam valores monetários como `Decimal` no modelo.

A TASK-038 usa o mesmo histórico imutável em um recorte próprio da missão:
somente coletas bem-sucedidas e fontes selecionadas, mantendo identificáveis a
observação corrente, a anterior comparável e o menor total comparável. Esse uso
é somente leitura e não altera as consultas públicas deste módulo.
