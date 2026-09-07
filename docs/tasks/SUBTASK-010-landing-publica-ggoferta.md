# Subtask 10 — Landing pública do GG Oferta (auditoria GG Oferta)

Status: **Concluída e publicada em `origin/main`** (commit `73014a3`,
03/09/2026 -- `feat: adiciona landing e identidade visual do GG Oferta`).
`frontend/src/pages/LandingPage.tsx` está ativo na rota `/`
(`frontend/src/App.tsx`), com teste dedicado
`frontend/tests/landing-page.mjs`. Este arquivo de planejamento nunca
tinha sido atualizado depois da execução (nem commitado) -- corrigido
nesta auditoria de saneamento de documentação (2026-09-07). O restante
do documento abaixo descreve a especificação original, mantida como
registro do escopo aprovado.

## Objetivo

Criar a página pública principal do GG Oferta em `https://ggoferta.com`
(rota `/` para visitante não autenticado). Hoje já existem: login Web,
cadastro Web self-service, recuperação de senha, aplicação autenticada em
`/app`, área administrativa integrada, missões/ofertas reais.

A Landing precisa apresentar o produto para quem ainda não está
autenticado e levar naturalmente para **Entrar** e **Criar conta**,
explicando rapidamente:

- o que é o GG Oferta;
- o que ele faz;
- como funciona;
- por que alguém usaria;
- como começar.

A pessoa não deve precisar conhecer Telegram, "missões", scraping,
providers, IA ou detalhes técnicos para entender o produto.

**Mensagem central:** o usuário informa o produto que procura e o GG
Oferta acompanha ofertas para ajudá-lo a encontrar oportunidades
relevantes.

Não transformar a Landing em documentação técnica.

## Identidade do produto

Nome público: **GG Oferta**. Não usar "AIShoppingAgent" como nome
comercial, nem nomes internos de serviços, nem linguagem de
projeto/DEV. Pode usar "GG Oferta" no header, hero, metadata e footer.

Se já existir logo/identidade visual real no projeto, reutilizar. Se não
existir, **não inventar uma marca completamente nova** — usar
apresentação tipográfica simples e consistente por enquanto. O design
system geral será tratado na Subtask 12.

## Estrutura mínima da Landing

1. **Header** — "GG Oferta", "Entrar", "Criar conta". Sem menu enorme;
   navegação por âncoras só se realmente ajudar, mantendo simples.

2. **Hero** — precisa responder imediatamente "o que esse produto faz?".
   Título e texto curtos, humanos, claros. Exemplo conceitual (não copiar
   obrigatoriamente): *"Encontre a oferta certa sem ficar procurando toda
   hora." / "Diga o que você quer comprar. O GG Oferta acompanha preços e
   mostra quando encontra uma oportunidade que combina com a sua
   busca."* CTA principal: Criar conta. CTA secundário: Entrar. Nenhum
   CTA falso.

3. **Como funciona** — cerca de 3 passos: diga o que está procurando → o
   GG Oferta acompanha ofertas → veja/receba oportunidades relevantes. Se
   usar o termo "missão", explicar de forma natural (ex.: *"Crie uma
   missão para o produto que você procura."*), sem presumir que o
   visitante já conhece a palavra.

4. **Recursos reais** — mostrar só funcionalidades que **existem
   atualmente no código**; auditar antes de escrever. Candidatos a
   confirmar: criação de missões, definição de preço/critério,
   acompanhamento de ofertas, histórico de preços, parcelamento quando
   coletado, pausa/retomada de missões, consulta pela Web, alertas pelo
   Telegram, cadastro e gerenciamento da conta pela Web. Não anunciar
   funcionalidade futura como atual.

5. **Lojas** — se a lista atual continuar sendo as seis fontes
   suportadas (Amazon, Mercado Livre, Magazine Luiza/Magalu, KaBuM!,
   Pichau, Terabyte), pode existir uma seção discreta mostrando isso;
   **confirmar no código atual antes**. Nunca incluir Shopee, AliExpress
   ou qualquer loja ainda não suportada. Sem necessidade de logos
   externos — texto/cards simples bastam.

6. **CTA final** — reforço curto do produto + Criar conta + Entrar. Sem
   urgência artificial.

7. **Footer** — GG Oferta, link de acesso, link de cadastro, links
   públicos reais que já existirem e fizerem sentido. Não criar páginas
   legais inexistentes só para preencher footer; só linkar
   Privacidade/Termos se já existirem de verdade.

## Copy — regras de conteúdo

Linguagem acessível e direta. Evitar: "revolucionário", "a melhor
plataforma", "economize milhares", "milhares de usuários", "X ofertas
encontradas" sem dado real, porcentagem inventada, depoimento inventado,
avaliação 5 estrelas falsa, empresas parceiras que não são parceiras, "IA
que encontra o menor preço da internet" se isso não puder ser garantido.

**Proibido:** fake social proof, fake métricas, fake testimonials,
garantias de economia inventadas.

Benefícios factuais permitidos: evita pesquisa manual repetitiva,
centraliza acompanhamento, permite definir o que você procura, apresenta
ofertas coletadas das fontes suportadas.

## IA

Não vender a Landing como "produto de IA". A inteligência interna do
sistema não precisa ser protagonista da comunicação. Se mencionar
tecnologia inteligente/automação, fazer de forma secundária e só se fiel
ao comportamento atual. O visitante quer comprar melhor, não conhecer a
arquitetura.

## Telegram

Pode explicar que o Telegram é um canal de alerta/integração, se estiver
funcional. Nunca: transformar a Landing numa página do bot, exigir
Telegram para usar o GG Oferta, dizer que o cadastro depende do
Telegram, colocar URL/link de bot não verificado, inventar username do
bot. O Web agora é uma porta de entrada real.

## Comportamento de rotas

Visitante não autenticado: `/` → Landing pública; `/login` → Login;
`/cadastro` → Cadastro; `/recuperar` → Recuperação.

Usuário já autenticado abrindo `/`: preferencialmente redirecionar para
`/app`, se isso for consistente com a arquitetura atual. Não obrigar
usuário autenticado a passar pela Landing toda vez. Se houver motivo
estrutural para um comportamento diferente, reportar antes de escolher.

## Segurança

Landing pública e predominantemente estática. Não expor: configuração
interna, endpoints administrativos, secrets, nomes internos de
infraestrutura, métricas operacionais, informações de usuários. Não
alterar autenticação/CSRF/cookies da Subtask 9 para construir a Landing
— os CTAs só usam os fluxos já existentes.

## Design

Página pública, precisa parecer produto de verdade. Usar a
skill/plugin de frontend disponível no Claude Code para orientar a
implementação visual. Landing moderna, limpa, profissional, responsiva,
coerente com GG Oferta, sem aparência de template genérico de IA/SaaS.

Evitar: excesso de gradientes, blobs decorativos sem função, dezenas de
cards, ícones aleatórios, hero gigantesco vazio, texto centralizado em
todas as seções, animação exagerada, "dashboard fake" com dados
inventados.

Pode usar representação visual do produto **somente** se baseada no
produto real (ex.: composição inspirada nos cards/telas reais, claramente
demonstrativa, sem fingir ser dado real). Se uma captura/componente real
do app puder ser reutilizada sem expor dados de DEV/usuários, é melhor.
Não fabricar estatísticas para preencher a tela.

## Design system

Subtask 12 tratará a fundação/design system geral. Nesta subtask:
reutilizar Tailwind/shadcn/componentes existentes; criar componentes
locais da Landing quando necessário; não refatorar o frontend inteiro;
não padronizar todas as páginas do sistema; não migrar todos os
componentes; não fazer "grande limpeza" global. Algo claramente
reutilizável pode ser consolidado depois, na Subtask 12.

## Responsividade

Funcionar bem em desktop, notebook, tablet, mobile. Validar
especialmente: header, hero, CTAs, cards/seções, textos, largura,
footer. Sem overflow horizontal, elementos cortados, botão saindo da
tela, texto ilegível.

## Dark/light

Se a aplicação já suporta claro/escuro, a Landing deve respeitar o
mesmo sistema — não criar um segundo mecanismo de tema. Validar
contraste nos dois temas.

## Acessibilidade

Manter: headings em hierarquia correta, links semanticamente corretos,
botões só para ações, foco por teclado visível, labels/aria quando
necessário, contraste aceitável, navegação funcional por teclado. Não
sacrificar semântica por estética.

## SEO/metadata

Como `/` passa a ser a entrada pública do produto, configurar metadata
básica real: title, description, viewport já existente, favicon
existente se houver. Texto factual. Exemplo conceitual de title: "GG
Oferta — acompanhe preços e encontre boas ofertas". Não criar
infraestrutura avançada de SEO/SSR nesta subtask, não migrar Vite para
Next.js só por SEO. Se a SPA limitar metadata dinâmica, aceitar o limite
atual e configurar o básico.

## Performance

Não carregar assets gigantes sem necessidade. Evitar dependências novas
só para efeitos visuais simples. Imagens: otimizar, dimensões adequadas,
lazy loading quando aplicável. Sem vídeo pesado/autoplay.

## Não fazer nesta subtask

Cupons (Subtask 11), design system global (Subtask 12), overhaul de
Pesquisa/Ofertas (Subtask 13), overhaul de Missões (Subtask 14), overhaul
Home/Profile (Subtask 15), overhaul Admin (Subtask 16), novas lojas,
novas regras de coleta, mudanças de matching, mudanças em Telegram, novo
backend sem necessidade da Landing, newsletter, blog, depoimentos,
métricas públicas, pricing/plano pago inventado, checkout/pagamento,
login social, analytics externo novo sem decisão, chatbot público.

## Testes (a fazer quando esta subtask for executada)

1. `/` público renderiza a Landing para visitante anônimo;
2. CTA "Entrar" leva para `/login`;
3. CTA "Criar conta" leva para `/cadastro`;
4. usuário autenticado abrindo `/` segue o comportamento definido;
5. nenhuma funcionalidade/loja futura é anunciada como atual;
6. refresh/acesso direto a `/` funciona;
7. rotas `/login`, `/cadastro`, `/recuperar`, `/app` continuam
   funcionando;
8. nenhum redirect loop foi introduzido.

Seguir o padrão de testes de frontend já em uso no projeto. Executar:
frontend tests afetados, typecheck, lint, backend só se realmente
alterado.

## Validação visual real (obrigatória quando executada)

Subir DEV e abrir a Landing em navegador real. Desktop: hero, header,
todas as seções, CTA final, footer. Mobile (~375px): navegação, CTAs,
seções, footer. Temas: light e dark. Interação: Entrar, Criar conta,
navegação interna se houver, teclado/foco. Também: refresh em `/`,
acesso direto, ausência de console errors relevantes, ausência de
requests quebrados, ausência de overflow. Não considerar validado
olhando só JSX ou screenshot estática.

## Conteúdo real

Antes de finalizar, comparar cada afirmação comercial da Landing com o
que o produto realmente faz. Se uma frase não puder ser sustentada pelo
sistema atual, reescrever — nunca inventar suporte. A Landing deve ser
comercialmente boa, mas factual.

## Arquivos/escopo

Alterações concentradas no frontend e na infraestrutura mínima de SPA
necessária. Se `/` depender de algum comportamento backend específico,
corrigir só o necessário para servir a Landing corretamente. Não usar
esta subtask para alterar domínio de negócio.

## Regras gerais

Não alterar PROD; não fazer push; não commit antes da revisão do
usuário; não iniciar a Subtask 11 a partir desta subtask; não incluir
tooling local no futuro commit; não usar dados artificiais como se
fossem reais; não criar fake social proof; não inventar recurso futuro;
não refatorar telas autenticadas por estética.

## Relatório final esperado (quando esta subtask for executada)

1. estado de `/` antes da mudança;
2. arquitetura escolhida para a Landing;
3. estrutura/seções criadas;
4. copy final principal;
5. funcionalidades reais apresentadas;
6. lojas apresentadas;
7. comportamento anônimo/autenticado;
8. arquivos alterados;
9. testes;
10. resultado do typecheck/lint;
11. inspeção visual desktop;
12. inspeção visual mobile;
13. inspeção light/dark;
14. acessibilidade básica;
15. comportamento de refresh/rotas;
16. qualquer limitação encontrada;
17. git diff/status.
