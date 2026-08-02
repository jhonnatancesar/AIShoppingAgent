# Ofertas

`Offer` representa o anúncio estável de um produto canônico em um varejista ou
marketplace. A TASK-014 introduziu `Store`; a revisão corretiva `20260802_0009`
adicionou tipo de fonte e vendedores.

## Loja

- `id`: UUID gerado pela aplicação;
- `code`: identificador estável e único em `snake_case`, com até 64 caracteres;
- `name`: nome obrigatório, com até 160 caracteres;
- `base_url`: URL base obrigatória;
- `source_type`: `retailer` ou `marketplace`;
- `is_active`: ativação lógica, com padrão `true`;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Uma fonte não é um Store Provider. Este registro não contém seletores,
credenciais nem regras de coleta.

## Vendedor

`Seller` identifica um vendedor dentro de um marketplace por nome e ID externo
opcional. O banco rejeita vendedores em varejistas e garante que uma oferta não
possa referenciar um vendedor de outro marketplace.

## Oferta

- `id`: UUID gerado pela aplicação;
- `product_id`: produto obrigatório, protegido por `RESTRICT`;
- `store_id`: loja obrigatória, protegida por `RESTRICT`;
- `seller_id`: vendedor opcional e restrito à mesma fonte;
- `external_id`: identificador opcional fornecido pela loja;
- `url`: URL canônica obrigatória;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

No varejo, identidade externa e URL são únicas por fonte. Em marketplace, são
únicas por fonte e vendedor, permitindo o mesmo anúncio para vendedores
diferentes. Índices em produto, fonte e vendedor suportam as relações previstas.

## Limites

Preço e disponibilidade variam no tempo e nunca pertencem a `Offer`; serão
registrados como observações históricas na tarefa própria. A TASK-014 também não
cria APIs, coleta, normalização monetária, integração com lojas ou catálogo de
produtos.
