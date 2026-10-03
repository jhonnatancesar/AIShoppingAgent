# TASK-137 — Banco de modelos de produto (lista aberta copiada) para entender o pedido do usuário

Status: **Registrada (2026-10-03), NÃO iniciada.** Pedido do usuário em conversa, depois de a TASK-136
mostrar que missões de placa-mãe, RAM e celulares fora de iPhone/Galaxy S não ganham item de
monitoramento (não entram no funil) por falta de regra de texto.

## Problema

Na criação da missão, o texto do usuário passa só por regras escritas à mão (`_EXTRACTORS` em
`backend/app/products/identity.py`: iPhone, Galaxy S, CPU AMD/Intel, GPU RTX). Para o resto
(`resolve_monitoring_identity` devolve `None`) a missão fica sem item compartilhado e cai no caminho
antigo, onde a IA analisa todos os candidatos de cada loja. A TASK-123 descartou um parser de regex por
categoria; esta TASK não cria regra por categoria: usa **dados**.

## Desenho (decisão do usuário, 2026-10-03)

1. **Copiar listas abertas para o nosso banco** (cópia da base, nunca chamada das APIs deles em
   produção; atualização periódica por novo download).
2. A IA continua montando o texto padrão do pedido (`IntentParameters`: `search_query`, `model`,
   `model_confidence`, `display_query`).
3. O GG **procura no banco** o que se parece com o pedido e vincula (item de monitoramento a partir da
   entrada encontrada, pelo mesmo `resolve_monitoring_identity_for_resolved_product`).
4. **Na dúvida** (vários parecidos ou confiança baixa) manda à IA os **candidatos do banco** junto com o
   que ela entendeu do pedido; a IA só escolhe entre eles, nunca inventa. Sem escolha segura, a missão
   segue como hoje.
5. Substitui a verificação por lojas via navegador do worker (`StoreProductIdentityResolver`, TASK-083)
   nesse papel.
6. Vale para celular, placa-mãe, RAM e o restante que as fontes cobrirem.

## Fontes (conferidas abertas em 2026-10-03)

| Fonte | Cobre | Licença/termos | Decisão |
|---|---|---|---|
| BuildCores OpenDB — https://github.com/buildcores/buildcores-open-db | CPU, GPU, Motherboard, RAM, Storage, PSU, PCCase, Monitor, Laptop, Keyboard, Mouse, Headphones etc.; JSON por componente com fabricante, série, variante, chipset, soquete, formato, tipo de memória e MPN | **ODC-By 1.0**: uso, cópia e adaptação liberados, inclusive comercial, **com atribuição** | Copiar |
| Lista de dispositivos do Google Play — https://storage.googleapis.com/play_public/supported_devices.csv | Android: marca, nome comercial, código, modelo (~54 mil linhas) | **Conferido em 2026-10-03: sem licença aberta.** A página de ajuda é para desenvolvedores checarem compatibilidade e remete aos Termos do Google, que proíbem copiar/distribuir partes dos serviços | **Não copiar** |
| Wikidata (classe "smartphone model", Q19723451) — https://query.wikidata.org/ | ~2.177 modelos; marcas: Samsung ~350, Xiaomi 179, Huawei 169, Sony 140, LG 95, Nokia 75, Apple 29; conferido: iPhone 16, Redmi Note 14, Redmi 13, Galaxy S25/A55 existem; **POCO X7 e Moto G85 não** | **CC0** (domínio público): uso livre, inclusive comercial, sem exigir crédito | Copiar (cobertura parcial; datas de lançamento quase sempre ausentes) |
| iPhone/Apple | Gerações e variantes | Fatos públicos de nomenclatura; montar à mão | Seed próprio |
| GSMArena | Maior acervo de celulares | Termos: só uso pessoal e não comercial; proibido copiar/reproduzir | **Não copiar**; só consulta manual |
| docyx/pc-part-dataset | Dados do PCPartPicker | Raspado de site de terceiros | Não usar (BuildCores cobre) |

## Escopo proposto (ordem)

1. Tabelas novas (modelo canônico, aliases/nomes comerciais, códigos de peça, fonte e data de carga) e
   migration. Atribuição da fonte guardada junto de cada registro.
2. Importadores por fonte (script idempotente, `--dry-run`, relatório de contagem, sem rede em produção
   quando o arquivo já estiver baixado).
3. Busca por parecido no banco (normalização de nomes, tokens, código de peça) com pontuação e limite de
   candidatos; regra de "claro" vs "dúvida".
4. Passo de dúvida com IA: entrada = texto padrão do pedido + candidatos; saída fechada (id do candidato
   ou "nenhum"); registrar falha de IA como na TASK-133 (`ai_failed`).
5. Ligação na criação/edição de missão (`reconcile_mission_monitoring_item`) e na identidade do anúncio
   (catálogo da TASK-133), sem mudar chaves já gravadas.
6. **Rodapé "Fontes de dados" (aprovado pelo usuário, 2026-10-03):** crédito ao BuildCores OpenDB com a licença ODC-By 1.0 e link, e menção ao Wikidata (CC0); entra junto com a primeira carga de dados. Se o sistema for aberto a terceiros, a mesma nota acompanha qualquer cópia do banco.
7. Medição: quantos pedidos resolvem sem IA, com IA e sem resolução, por categoria.

## Perguntas em aberto

1. ~~Termos do CSV do Google Play~~ — fechada em 2026-10-03: não copiar. Falta cobrir o que o Wikidata não tem (POCO, Moto G etc.): seed manual das linhas mais pedidas e aprendizado pelos anúncios reais (catálogo da TASK-133).
2. Cobertura de modelos vendidos só no Brasil (a base do BuildCores é americana).
3. Como unificar com `product_identity_catalog_entries` (TASK-132/133) sem duplicar identidade.
4. Frequência de atualização das cargas.
5. Texto e local da atribuição ODC-By (rodapé do site e documentação).

## Relação com outras TASKs

TASK-133 (catálogo e identidade), TASK-136 (funil antes da IA: depende de a missão ter item), TASK-123
(decisão de não criar parser por regex por categoria).
