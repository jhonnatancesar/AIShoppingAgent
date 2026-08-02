# TASK-023 — Criar adaptador de coleta

Status: Concluída em 2026-08-02

## Objetivo

Criar a fronteira neutra entre a orquestração e os Store Providers.

## Escopo

- Definir pedido, oferta bruta e resultado de coleta.
- Definir uma porta assíncrona para providers.
- Registrar e despachar providers por fonte, rejeitando fontes ausentes e contratos
  inconsistentes.
- Preservar dados brutos de vendedor, frete e fulfillment sem normalizar ou persistir.

Playwright, providers concretos, normalização e persistência permanecem nas TASKs
024, 055, 025 e 026, respectivamente.

## Critério de aceite

- Contratos imutáveis validam texto, timestamps e consistência da fonte.
- O adaptador encaminha uma solicitação ao provider correto.
- Dados brutos necessários a varejistas e marketplaces são preservados.
- Testes automatizados cobrem sucesso e violações do contrato.

