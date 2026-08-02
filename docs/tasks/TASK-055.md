# TASK-055 — Implementar Store Providers selecionados

Status: Pendente

## Objetivo

Implementar Store Providers para todas as lojas e marketplaces explicitamente selecionados pelo usuário para a V1.

## Escopo

- Registrar no próprio arquivo, antes da implementação, a lista fechada de fontes selecionadas pelo usuário.
- Implementar um provider independente por fonte usando o adaptador de coleta e a base Playwright já definidos.
- Integrar somente as fontes explicitamente selecionadas; não descobrir, habilitar ou adicionar marketplaces automaticamente.
- Tratar diferenças de vendedor, frete, disponibilidade, moeda e evidência bruta quando a fonte exigir, sem antecipar comparação ou compra.
- Validar cada provider contra a fonte real e manter testes automatizados com dados sanitizados e estáveis.

Kabum não é obrigatória nem exclusiva. Ela só será incluída se estiver na lista selecionada pelo usuário.

## Ordem e dependências

Executar após as TASK-023 e TASK-024, e antes da TASK-025, para validar a arquitetura de coleta com as fontes selecionadas antes da normalização de preço e moeda.

## Critério de aceite

Cada fonte registrada na lista fechada possui um Store Provider funcional, isolado e validado na origem real. Uma busca usa todos os providers selecionados e retorna os dados brutos necessários à normalização posterior, sem integrar fontes não aprovadas.
