# TASK-055 — Implementar Store Providers selecionados

Status: Concluída em 2026-08-02

## Objetivo

Implementar Store Providers para as quatro fontes selecionáveis da V1.

## Fontes definidas

Disponíveis para seleção:

- Pichau;
- Terabyte;
- Amazon;
- Kabum.

***Futuro — não selecionáveis na V1:***

- Mercado Livre (ML);
- Shopee;
- AliExpress.

## Escopo

- Implementar um provider independente por fonte usando o adaptador de coleta e a base Playwright já definidos.
- Integrar somente as fontes explicitamente selecionadas; não descobrir, habilitar ou adicionar marketplaces automaticamente.
- Tratar diferenças de vendedor, frete, disponibilidade, moeda e evidência bruta quando a fonte exigir, sem antecipar comparação ou compra.
- Validar cada provider contra a fonte real e manter testes automatizados com dados sanitizados e estáveis.

O usuário poderá escolher uma ou mais das quatro fontes disponíveis. Mercado
Livre, Shopee e AliExpress devem aparecer no bot somente sob o rótulo
***Futuro***, sem permitir seleção ou iniciar coleta.

## Ordem e dependências

Executar após as TASK-023 e TASK-024, e antes da TASK-025, para validar a arquitetura de coleta com as fontes selecionadas antes da normalização de preço e moeda.

## Critério de aceite

Pichau, Terabyte, Amazon e Kabum possuem Store Providers funcionais, isolados e
validados nas origens reais. Uma busca usa todos os providers escolhidos pelo
usuário entre essas quatro fontes. Mercado Livre, Shopee e AliExpress aparecem
como futuras e não executam coleta.

## Entrega e validação

- Providers Playwright independentes em `backend/app/collection/providers`.
- Coleta paralela restrita às fontes explicitamente selecionadas por
  `CollectionAdapter.collect_selected`.
- Testes sanitizados estáveis e script de validação real
  `backend/scripts/validate_store_providers.py`, incluído na imagem Docker.
- Amazon e Kabum validadas em Chromium headless; Pichau validada em Chromium
  headed. Terabyte foi validada na origem por navegador interativo, mas pode
  encerrar ou bloquear automação headless/automatizada conforme a proteção da
  origem.
- Não foram usados stealth, resolução de CAPTCHA ou contorno de proteção. Em
  Ubuntu Server, execuções headed exigem display virtual (por exemplo, Xvfb).
- A imagem inicia um Xvfb interno e expõe `DISPLAY` para Chromium headed mesmo
  sem desktop. A imagem Linux foi construída e as quatro fontes foram validadas
  nela: Amazon e Kabum headless; Pichau e Terabyte headed via Xvfb.
