# Produtos

`Product` representa a identidade canônica de um item, sem vínculo com loja,
anúncio, preço ou disponibilidade. A entidade persistente foi introduzida pela
TASK-013.

## Contrato

- `id`: UUID gerado pela aplicação;
- `name`: nome obrigatório, com até 300 caracteres e não vazio após remoção de
  espaços nas extremidades;
- `brand`: marca opcional, com até 160 caracteres e não vazia quando informada;
- `model`: modelo opcional, com até 160 caracteres e não vazio quando informado;
- `created_at` e `updated_at`: timestamps obrigatórios, conscientes de fuso e em
  UTC.

## Identidade e deduplicação

O nome não é uma chave de identidade: produtos diferentes podem compartilhar o
mesmo nome, e variações de escrita não provam que dois registros sejam o mesmo
produto. Por isso, esta etapa não cria restrição de unicidade por `name`,
`brand`, `model` nem pela combinação desses campos.

A deduplicação deve ser conservadora e ocorrer em uma camada de serviço futura,
com evidência suficiente de marca, modelo e atributos específicos da categoria.
Até que esse fluxo seja definido, registros não são mesclados automaticamente.
Identificadores de lojas pertencem às ofertas e não podem determinar sozinhos a
identidade canônica.

## Limites

A TASK-013 não cria catálogo HTTP, busca, categorias, atributos variáveis,
ofertas, lojas, preços ou integrações externas. Essas responsabilidades continuam
nas tarefas próprias do roadmap.
