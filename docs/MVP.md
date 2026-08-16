# MVP — Escopo da V1

## Regra de escopo

Este documento define integralmente a V1. Qualquer item que não esteja listado nesta página está fora do escopo da primeira versão e deve ser registrado em `docs/BACKLOG.md` quando for uma evolução desejável.

## O que faz parte da V1

- Ambiente local reproduzível para o monólito modular FastAPI, PostgreSQL e Docker Compose.
- Persistência de usuários, produtos, ofertas, missões, eventos e observações históricas de preço.
- Criação, consulta e acompanhamento de missões de compra.
- Coleta e normalização de preços das lojas e marketplaces selecionados, preservando cada observação no histórico.
- Alertas de preço e eventos relacionados às missões.
- AI Provider Manager como única porta de acesso a IA, com USER e DEV restritos
  à mesma capacidade gratuita; pesquisa web é capability opt-in exclusiva de
  DEV via OpenRouter Free.
- Interação e notificações essenciais via Telegram.
- Recomendações e comparação básica de ofertas com evidências de preço.
- Controles mínimos de autenticação, autorização, proteção de segredos, resiliência, observabilidade e auditoria.
- Testes de integração e ponta a ponta para o fluxo crítico, documentação operacional e checklist de release.

## Limites explícitos

- A V1 permite selecionar Pichau, Terabyte, Amazon e Kabum, individualmente ou em conjunto.
- Mercado Livre, Shopee e AliExpress aparecem no bot sob o rótulo ***Futuro*** e não podem ser selecionados na V1.
- Marketplaces podem fazer parte da V1 quando selecionados; cada integração exige provider e validação próprios.
- A V1 não inclui descoberta automática de fontes nem suporte genérico a marketplaces não selecionados.
- A V1 não inclui ações financeiras automáticas; qualquer fluxo assistido de compra requer confirmação explícita.

## Critérios objetivos de conclusão do MVP

- O ambiente local sobe de forma documentada e reproduzível.
- Um usuário autorizado consegue criar e consultar uma missão pelo canal Telegram.
- O sistema pesquisa todas as fontes selecionadas, normaliza os dados e preserva cada coleta no histórico.
- Uma condição de preço configurada para uma missão produz evento e notificação rastreáveis.
- A recomendação ou comparação básica apresenta evidências históricas da oferta.
- Todo uso de IA passa pelo AI Provider Manager; nenhum módulo chama provedores diretamente.
- Os fluxos críticos possuem testes de integração e ponta a ponta executáveis.
- A documentação operacional, os controles mínimos de segurança e o checklist de release estão concluídos.

Os critérios acima exigem execução somente nas TASKs explicitamente aprovadas; este documento não autoriza implementação antecipada.
