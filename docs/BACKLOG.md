# Backlog de Evoluções Futuras

Este documento é o repositório de ideias que surgirem durante o desenvolvimento e não pertencem à versão atual. Registrar uma ideia aqui não a aprova, não cria uma TASK e não altera o escopo da V1.

Para cada item novo, registrar uma descrição curta, a motivação e eventuais dependências. A priorização e a transformação em tarefa dependem de decisão explícita posterior.

## Novas fontes de oferta

- Store Providers para Mercado Livre, Shopee e AliExpress, apresentados como ***Futuro*** no bot da V1.
- Inclusão após a V1 de lojas ou marketplaces que não forem selecionados para a TASK-055.
- Critérios de qualificação, confiabilidade e manutenção de Store Providers.
- Evolução da normalização de vendedores, frete, impostos, prazo e políticas específicas além do necessário às fontes selecionadas.

## Canais e experiência do usuário

- Dashboard web para acompanhamento de missões, ofertas e histórico.
- Integração com WhatsApp.
- Aplicativo mobile.
- Multi-idioma.

## Inteligência e automação

- Ampliar ainda mais a robustez do `IntentInterpreter` (TASK-032/057) para
  novos tipos e estilos de linguagem informal além do conjunto já validado,
  com nova rodada de validação real (`DEC-017`).
- Perfil pago na V2, com créditos configurados para comparar Gemini premium,
  OpenAI e Claude e aplicar fallback conforme capacidade e disponibilidade.
- OCR para extrair informações de imagens, comprovantes ou páginas.
- IA local.
- Plano PLUS com uso de múltiplos provedores de IA.
- Sistema de plugins para extensões de fontes, canais e comportamentos.

## Dados e análise

- Analytics avançado de preços e comportamento de compra.
- Relatórios exportáveis e agendados.
- Comparador de preços avançado, com critérios configuráveis e visualizações históricas.

## Plataforma e integrações

- API pública.
- Sistema de pagamentos.
- Integrações adicionais não essenciais à V1.

## Papéis e planos da V2

- múltiplos papéis por usuário e tabela `user_roles`;
- composição simultânea de USER, ADMIN e DEV;
- planos FREE, PLUS e PRO, assinatura e entitlements;
- gestão administrativa explícita de papéis;
- separação estrutural avançada entre papéis de autorização e perfis de IA;
- ferramentas cross-user de suporte/auditoria somente com permissão própria.

## Regra de uso

Tudo que não constar em `docs/MVP.md` é considerado fora do escopo da V1. Quando uma ideia for explicitamente descartada para a V1, ela deve ser registrada também em `docs/OUT_OF_SCOPE.md`.
