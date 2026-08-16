# Contexto permanente do projeto

## Missão

Construir, de forma incremental, um agente de compras que pesquisa produtos, registra preços, executa missões e apoia decisões de compra.

## Arquitetura alvo do MVP

- Monólito modular com FastAPI.
- PostgreSQL como banco de dados.
- Docker Compose para ambiente local.
- Playwright para coleta automatizada quando aplicável.
- Telegram como canal inicial de interação.
- Sistema de missões e histórico completo de preços.

## Escopo comercial da V1

A V1 permitirá pesquisar Pichau, Terabyte, Amazon e Kabum, conforme seleção do usuário. Mercado Livre, Shopee e AliExpress serão apresentados pelo bot como ***Futuro***, sem seleção ou coleta. Cada fonte ativa exige Store Provider próprio; nenhuma fonte será descoberta ou integrada automaticamente.

## Guardrails

- Todo o trabalho neste projeto — incluindo mensagens de progresso, raciocínio apresentado, hipóteses, achados, avisos, relatórios, conclusões e toda a documentação (TASKs, ROADMAP, CHANGELOG, PROJECT_CONTEXT) — deve ser conduzido em português do Brasil (PT-BR), sem exceção, mesmo quando ferramentas/comandos/bibliotecas retornarem saída em inglês. Só ficam literais em inglês os elementos técnicos que precisam permanecer assim (código-fonte, nomes de função/classe/arquivo, caminhos, comandos de terminal, parâmetros, variáveis de ambiente, identificadores, stack traces, mensagens de erro literais, SQL, payloads) — nunca usar isso como desculpa para escrever a explicação inteira em inglês. Mensagens de commit podem seguir Conventional Commits, com a descrição em português.
- Não implementar nada além da TASK solicitada.
- Antes de iniciar uma TASK, identificar todos os recursos necessários ao desenvolvimento e à validação real. Solicitar antecipadamente ao usuário qualquer chave, conta, permissão ou configuração ausente, orientando seu armazenamento seguro fora do Git e do chat.
- Não conectar módulos diretamente a Gemini, OpenAI ou Claude: o AI Provider Manager será a única porta de acesso.
- O perfil USER usará Gemini; ADMIN e DEV usarão a melhor IA disponível com fallback. PLUS é futuro e não será implementado no MVP inicial.
- Cada coleta de preço deverá ser persistida quando o mecanismo for implementado.

## Fonte de verdade

`docs/PROJECT_CONTEXT.md` registra o estado vivo; `docs/ROADMAP.md` registra a ordem de trabalho; `docs/tasks/` contém o escopo unitário.

## Continuidade no servidor — 2026-08-15

O ambiente autoritativo passou a ser `C:\app\AIShoppingAgent`, no Windows
Server. O pacote de consolidação dos fluxos determinísticos do Telegram e de
recuperação de senha foi commitado localmente em
`fd68939bcaac3d5926af6bc43eee7c05913f727c`, sem push, rebuild ou deploy.

O próximo problema registrado, ainda não corrigido, é o drift do
`alembic check` nas constraints `mission_command_values`,
`store_source_type_values` e `user_role_values`. Antes de qualquer ação, ler
`docs/HANDOFF_SERVER_2026-08-15.md` e `docs/ALEMBIC_CHECK_ISSUE.md`. Toda
reprodução deve usar PostgreSQL 18.4 descartável; o banco ativo e os
containers em execução não podem ser alterados sem autorização explícita.
