# Backlog de Evoluções Futuras

Este documento é o repositório de ideias que surgirem durante o desenvolvimento e não pertencem à versão atual. Registrar uma ideia aqui não a aprova, não cria uma TASK e não altera o escopo da V1.

Para cada item novo, registrar uma descrição curta, a motivação e eventuais dependências. A priorização e a transformação em tarefa dependem de decisão explícita posterior.

## Novas fontes de oferta

- Store Providers para Mercado Livre, Shopee e AliExpress, apresentados como ***Futuro*** no bot da V1.
- Inclusão após a V1 de lojas ou marketplaces que não forem selecionados para a TASK-055.
- Critérios de qualificação, confiabilidade e manutenção de Store Providers.
- Evolução da normalização de vendedores, frete, impostos, prazo e políticas específicas além do necessário às fontes selecionadas.
- **Frete e parcelamento autenticados por usuário** (`DEC-045`): depois que
  a V1.2 disponibilizar consulta autenticada de frete/parcelamento somente
  para ADMIN/DEV, a V2 poderá abrir essa mesma capacidade para usuários
  comuns. Exige projeto próprio de credenciais/sessões isoladas por
  usuário, autorização, proteção de dados e ciclo de vida das sessões,
  além de regras específicas por marketplace — nada disso é antecipado na
  V1 nem na V1.2.

## Canais e experiência do usuário

- Dashboard web para acompanhamento de missões, ofertas e histórico.
- Integração com WhatsApp.
- Aplicativo mobile.
- Multi-idioma.
- Confirmação/verificação de e-mail no onboarding da V2; o e-mail da V1
  permanece opcional, não verificado e sem uso para recuperação.

## Inteligência e automação

- Na V1.2, tratar missões sem valor informado com uma pergunta explícita de
  orçamento. Se a pessoa não possuir um valor, consultar primeiro o histórico
  elegível e depois fontes externas reais para propor uma referência de mercado
  de itens/marcas mais baratos, com regras de evidência e dados insuficientes a
  definir em TASK própria (`DEC-044`).
- Ampliar ainda mais a robustez do `IntentInterpreter` (TASK-032/057) para
  novos tipos e estilos de linguagem informal além do conjunto já validado,
  com nova rodada de validação real (`DEC-017`).
- Perfil pago para usuários na V2, com créditos e entitlements próprios.
  A `DEC-061` mantém USER e DEV exclusivamente gratuitos na V1.
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
