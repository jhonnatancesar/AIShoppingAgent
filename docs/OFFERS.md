# Ofertas

`Offer` representa o anúncio estável de um produto canônico em uma loja
nacional. A TASK-014 também introduz `Store`, entidade mínima necessária para
normalizar a origem e preservar a integridade referencial das ofertas.

## Loja

- `id`: UUID gerado pela aplicação;
- `code`: identificador estável e único em `snake_case`, com até 64 caracteres;
- `name`: nome obrigatório, com até 160 caracteres;
- `base_url`: URL base obrigatória;
- `is_active`: ativação lógica, com padrão `true`;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Uma loja não é um Store Provider. Este registro não contém seletores,
credenciais, regras de coleta nem suporte a marketplaces.

## Oferta

- `id`: UUID gerado pela aplicação;
- `product_id`: produto obrigatório, protegido por `RESTRICT`;
- `store_id`: loja obrigatória, protegida por `RESTRICT`;
- `external_id`: identificador opcional fornecido pela loja;
- `url`: URL canônica obrigatória;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Dentro de uma loja, o `external_id` é único quando informado e a URL é sempre
única. URLs iguais em lojas diferentes são permitidas. Índices próprios em
`product_id` e `store_id` suportam as relações previstas no modelo de dados.

## Limites

Preço e disponibilidade variam no tempo e nunca pertencem a `Offer`; serão
registrados como observações históricas na tarefa própria. A TASK-014 também não
cria APIs, coleta, normalização monetária, integração com lojas ou catálogo de
produtos.
