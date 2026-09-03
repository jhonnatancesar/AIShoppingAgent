# GG Oferta — Subtasks 12 a 16 do Redesign Frontend

> Documento de especificação para registrar o plano das Subtasks 12, 13, 14, 15 e 16.
>
> Este documento descreve objetivo, escopo, limites, critérios de conclusão, testes e validação visual de cada etapa.
>
> A execução deve ser sequencial. Não iniciar a próxima subtask antes da aprovação da anterior.

---

# Contexto geral

As etapas anteriores já fecharam a base funcional principal do GG Oferta:

- login único;
- experiência integrada USER/ADMIN;
- cadastro Web self-service;
- alteração e recuperação de senha;
- infraestrutura de verificação;
- área de Cupons autenticada;
- Landing pública;
- nova identidade visual GG Oferta;
- favicon e logos light/dark.

As Subtasks 12–16 têm como objetivo transformar a aplicação autenticada em uma experiência visual consistente, moderna, clara e agradável sem reescrever regras de negócio que já funcionam.

A prioridade agora é frontend, UI/UX, consistência e apresentação.

## Regra obrigatória de frontend

Nas Subtasks 12–16, usar obrigatoriamente a skill/plugin:

`frontend-design`

disponível no Claude Code.

A skill deve orientar tanto a implementação quanto a revisão visual real.

Não considerar uma etapa aprovada somente porque JSX, lint e typecheck passam.

É obrigatória inspeção real no navegador DEV.

---

# Regras globais das Subtasks 12–16

Aplicam-se a todas as etapas:

- Não alterar PROD.
- Não fazer push.
- Não fazer commit antes da revisão do usuário.
- Não iniciar a subtask seguinte antes da aprovação da atual.
- Não criar dados falsos apresentados como reais.
- Não criar métricas, depoimentos, estatísticas ou estados artificiais para preencher telas.
- Não enfraquecer autenticação, CSRF ou autorização.
- Backend continua sendo a autoridade de permissões.
- Não mudar regras de negócio apenas por conveniência visual.
- Refactors são permitidos quando houver motivo estrutural real.
- Não fazer refactor global apenas porque "ficaria mais bonito".
- Preservar dark/light.
- Preservar responsividade.
- Preservar acessibilidade.
- Não adicionar dependências grandes quando Tailwind/shadcn/Radix/motion já resolvem.
- Evitar estética genérica de template SaaS/IA.
- Manter branding público como GG Oferta.
- Nomes internos como `AIShoppingAgent` podem continuar em código/infraestrutura quando não forem visíveis ao usuário.

## Estilo desejado

A aplicação deve parecer:

- moderna;
- humana;
- confiável;
- prática;
- consistente;
- orientada a usuário comum;
- produto real, não painel técnico.

Evitar:

- excesso de gradientes;
- glow exagerado;
- cards em excesso;
- badges sem função;
- "AI slop";
- telas lotadas;
- textos institucionais;
- ícones decorativos aleatórios;
- componentes diferentes para a mesma ação;
- modais/alerts nativos quando houver padrão visual melhor;
- informação técnica desnecessária para USER.

---

# SUBTASK 12 — Fundação visual / Design System do GG Oferta

## Objetivo

Criar a fundação visual comum da aplicação autenticada para que as Subtasks 13–16 possam redesenhar áreas específicas sem cada uma inventar seus próprios padrões.

A Subtask 12 não é o redesign completo das telas.

Ela é a infraestrutura visual do redesign.

Ao final, o projeto deve possuir uma linguagem visual coerente, componentes-base confiáveis e padrões reutilizáveis.

## Escopo

### 1. Auditoria do frontend atual

Antes de alterar código, mapear:

- componentes shadcn existentes;
- componentes locais duplicados;
- Buttons;
- Inputs;
- Selects;
- multi-selects;
- Cards;
- badges;
- dialogs;
- dropdowns;
- estados de loading;
- estados vazios;
- mensagens de erro;
- confirmação;
- toast;
- tabelas/listas;
- header;
- sidebar;
- mobile navigation;
- tipografia;
- espaçamentos;
- tokens CSS;
- cores;
- border radius;
- shadows;
- dark/light;
- padrões de formulário;
- uso de `prompt()`;
- uso de `confirm()`;
- uso de `alert()`.

Identificar inconsistências reais antes de padronizar.

Não sair substituindo tudo cegamente.

### 2. Tokens visuais

Revisar e consolidar os tokens já existentes.

Definir/ajustar de forma coerente:

- background;
- foreground;
- card;
- muted;
- primary;
- secondary;
- accent;
- destructive;
- border;
- input;
- ring;
- radius;
- shadows;
- espaçamentos recorrentes.

Preservar a identidade roxo/azul do GG Oferta sem transformar toda a aplicação em gradiente.

A identidade visual deve aparecer como acento, não como ruído.

### 3. Tipografia

Padronizar:

- H1;
- H2;
- H3;
- títulos de página;
- títulos de card;
- body;
- texto secundário;
- labels;
- captions;
- valores monetários;
- metadata.

Não trocar a fonte global sem motivo forte.

Se Inter já é a fonte da aplicação, preferir mantê-la.

Criar hierarquia clara de informação.

### 4. Layout base

Consolidar padrões para:

- largura de conteúdo;
- padding horizontal;
- espaçamento vertical;
- page header;
- seções;
- grid;
- stack;
- containers.

Desktop e mobile devem seguir regras previsíveis.

Evitar cada página ter largura/margens próprias sem necessidade.

### 5. Componentes-base

Revisar/criar/consolidar, quando necessário:

- `PageHeader`;
- `SectionHeader`;
- `Button`;
- `Input`;
- `Textarea`;
- `Select`;
- `Checkbox`;
- `Switch`;
- `Badge`;
- `Card`;
- `EmptyState`;
- `LoadingState`;
- `ErrorState`;
- `Skeleton`;
- `Dialog`;
- `AlertDialog`;
- `DropdownMenu`;
- `Tooltip`;
- `Tabs`;
- `Toast`/feedback global;
- componentes de formulário;
- paginação;
- filtros.

Não criar wrappers sem valor real.

Se shadcn já resolve, preferir compor o shadcn.

### 6. Feedback de ações

Padronizar:

- sucesso;
- erro;
- loading;
- confirmação;
- ação destrutiva;
- retry.

Eliminar progressivamente experiências como:

- `window.alert`;
- `window.confirm`;
- `window.prompt`;

quando a substituição puder ser feita estruturalmente sem redesenhar a tela inteira.

IMPORTANTE:

Não transformar a Subtask 12 em redesign do Admin.

Se `AdminHome` depende muito desses elementos e a troca exigir reestruturar a página inteira, preparar os componentes agora e deixar a aplicação específica para a Subtask 16.

### 7. Formulários

Definir padrão comum para:

- label;
- helper text;
- erro;
- required;
- disabled;
- loading;
- submit;
- cancel;
- field spacing.

Revisar especialmente multi-selects, pois o preflight já identificou múltiplas implementações.

Se houver duplicação clara, consolidar.

### 8. Navegação / shell

Revisar o shell atual apenas no nível estrutural:

- sidebar;
- header;
- active item;
- grupos;
- mobile menu;
- logo;
- toggle de tema;
- logout;
- seção Administração.

Pode ajustar inconsistências visuais do shell.

Não redesenhar as páginas internas ainda.

O shell precisa estar pronto para receber as áreas redesenhadas.

### 9. Dark / Light

Auditar todos os componentes-base nos dois temas.

Evitar:

- cores hardcoded;
- texto escuro no dark;
- tooltip claro no dark;
- bordas invisíveis;
- contraste ruim;
- gráficos dependentes de cor fixa.

O sistema de tema deve continuar único.

### 10. Acessibilidade

Padronizar:

- focus ring;
- labels;
- aria;
- contraste;
- teclado;
- dialogs;
- dropdowns;
- estados disabled;
- headings.

Não introduzir componentes visualmente bonitos mas inacessíveis.

### 11. Motion

Se motion já está disponível:

- definir uso discreto;
- transições rápidas;
- entradas sutis;
- evitar animação em tudo;
- respeitar `prefers-reduced-motion`.

Motion deve ajudar percepção de mudança, não chamar atenção para si.

### 12. O que NÃO fazer

Não redesenhar completamente:

- Pesquisa;
- Ofertas;
- Missões;
- Home;
- Perfil;
- Admin.

Esses itens pertencem às Subtasks 13–16.

Não alterar:

- matching;
- coleta;
- Telegram;
- regras de missão;
- backend de negócio;
- permissões.

## Testes / validação

Rodar:

- testes frontend afetados;
- typecheck;
- lint.

Inspeção visual real obrigatória em DEV:

- desktop;
- mobile;
- light;
- dark;
- sidebar;
- header;
- dialogs;
- forms;
- feedback;
- componentes-base.

Verificar teclado/foco.

## Critério de conclusão

A Subtask 12 termina quando:

- existe linguagem visual comum;
- componentes-base estão consistentes;
- estados vazios/loading/error seguem padrão;
- formulários seguem padrão;
- shell está coerente;
- dark/light está consistente;
- componentes estão prontos para reutilização nas Subtasks 13–16;
- nenhuma grande área funcional foi redesenhada prematuramente.

---

# SUBTASK 13 — Redesign de Pesquisa + Ofertas

## Objetivo

Redesenhar a experiência de descobrir produtos/ofertas e consultar ofertas disponíveis.

Essa etapa deve tornar Pesquisa e Ofertas as áreas mais claras e úteis do produto para quem quer encontrar algo para comprar.

## Escopo principal

Redesenhar:

- Pesquisa;
- lista geral de Ofertas;
- detalhes da Oferta;
- filtros relacionados;
- cards de produto/oferta;
- informação de preço;
- parcelamento;
- loja;
- imagem;
- condição;
- histórico/gráfico quando exibido nessa área.

Aplicar integralmente a fundação da Subtask 12.

### 1. Pesquisa

A pesquisa deve parecer simples.

Prioridade:

1. campo de busca;
2. resultado;
3. filtros relevantes;
4. decisão.

Evitar tela com controles demais antes do usuário pesquisar.

Revisar:

- search bar;
- placeholder;
- submit;
- debounce, se já existir;
- filtros;
- estado inicial;
- loading;
- zero resultados;
- erro;
- lista de resultados.

Não alterar o mecanismo de busca/matching sem necessidade funcional comprovada.

### 2. Ofertas

A lista de ofertas deve responder rapidamente:

- o que é;
- quanto custa;
- em qual loja;
- se é à vista/parcelado;
- se parece uma oportunidade relevante;
- como abrir.

Cards devem ter hierarquia clara.

Preço deve ser protagonista.

Evitar card lotado com metadata de mesma importância.

### 3. Imagens

Usar a imagem canônica já criada anteriormente.

Garantir:

- fallback;
- aspect ratio coerente;
- sem layout shift exagerado;
- imagem não dominando o card;
- mesma identidade visual para o mesmo produto quando aplicável.

Não reabrir a arquitetura de canonical image.

### 4. Condição

Exibir Novo/Usado quando relevante, seguindo a regra já aprovada.

Não reabrir classificação de condition.

UNKNOWN deve ser tratado defensivamente sem confundir usuário.

### 5. Parcelamento

Mostrar parcelamento de forma útil, não poluída.

Prioridade:

- preço à vista;
- melhor parcelamento/destaque;
- total parcelado quando relevante.

Não alterar regra de coleta.

### 6. Detalhe da oferta

Redesenhar `OfferDetailPage` para ter:

- produto;
- imagem;
- loja;
- preço;
- parcelamento;
- histórico;
- CTA real para anúncio;
- informações complementares.

Corrigir inconsistências visuais e de copy existentes.

Se houver teste antigo esperando "Abrir oferta na loja" enquanto UI real usa "Ver anúncio", alinhar teste/copy conforme decisão desta etapa.

### 7. Histórico / gráfico

Aplicar as correções funcionais já existentes.

Melhorar apresentação:

- leitura de preço;
- tooltip;
- eixo;
- ponto único;
- tema claro/escuro;
- ausência de dados.

Não mudar cálculo do histórico.

### 8. Filtros

Revisar filtros para:

- reduzir ruído;
- deixar controles frequentes visíveis;
- esconder avançados se necessário;
- funcionar bem em mobile.

Não inventar filtro sem suporte backend.

### 9. Estado vazio

Criar estados humanos:

- nenhuma oferta;
- nenhum resultado;
- filtro restritivo;
- erro de carregamento.

Evitar mensagens técnicas.

### 10. Mobile

Pesquisa/Ofertas precisam funcionar particularmente bem no celular.

Validar:

- cards;
- filtros;
- imagens;
- preço;
- CTA;
- detalhes;
- gráfico.

## Não fazer

Não alterar:

- coleta;
- matching;
- deduplicação;
- histórico de preço;
- parsers;
- provider stores;
- banco;
- Telegram.

Só corrigir bug funcional se ele for descoberto e for estritamente necessário para a UI representar corretamente os dados.

## Testes / validação

Testar:

- pesquisa;
- zero resultados;
- filtros;
- cards;
- detalhes;
- parcelamento;
- canonical image;
- gráfico;
- dark/light;
- mobile.

Inspeção visual real obrigatória.

## Critério de conclusão

Pesquisa e Ofertas devem parecer uma experiência única, clara e madura, com preço e decisão de compra como foco.

---

# SUBTASK 14 — Redesign de Missões

## Objetivo

Redesenhar toda a experiência Web de criação, consulta, edição e gerenciamento de Missões.

Missão é um conceito central do GG Oferta, mas a interface precisa explicar isso naturalmente para o usuário.

## Escopo

Redesenhar:

- lista de Missões;
- criar Missão;
- detalhe;
- editar;
- pausar;
- retomar;
- cancelar;
- estados;
- critérios;
- lojas;
- categorias/filtros disponíveis;
- ofertas relacionadas à missão.

Não alterar o fluxo Telegram, salvo correção visual/copy compartilhada inevitável.

### 1. Conceito de Missão

A UI deve explicar missão como:

"o que você quer que o GG Oferta acompanhe".

Evitar depender de linguagem técnica.

O usuário precisa entender:

- o que está sendo monitorado;
- preço desejado;
- lojas;
- status;
- resultado.

### 2. Lista de Missões

Cada missão deve mostrar de forma rápida:

- nome/produto;
- status;
- preço alvo, se houver;
- lojas;
- quantidade/estado de ofertas relevantes;
- última atividade relevante, se existir realmente.

Não inventar estatística.

### 3. Status

Padronizar visualmente:

- ativa;
- pausada;
- cancelada;
- outros estados reais.

Usar badges consistentes.

Ação disponível deve depender do estado real.

### 4. Criar Missão

Revisar o fluxo para diminuir fricção.

O usuário deve conseguir entender os campos sem documentação externa.

Agrupar os campos de forma lógica.

Exemplo:

- o que você procura;
- quanto pretende pagar;
- onde acompanhar;
- opções adicionais.

Não criar wizard de múltiplas telas se não houver ganho real.

### 5. Editar Missão

Usar os mesmos componentes/regras da criação.

Evitar formulário de edição visualmente diferente.

Preservar validações backend.

### 6. Ações

Pausar, retomar e cancelar precisam ter:

- feedback visual;
- confirmação adequada quando destrutivo;
- loading;
- erro tratado.

Aplicar Dialog/Toast da Subtask 12.

### 7. Detalhe da Missão

Organizar:

- critérios;
- status;
- lojas;
- ações;
- ofertas relacionadas.

Não transformar a página em dump de campos.

### 8. Oferta relacionada

Usar componentes/padrões visuais compatíveis com Subtask 13.

Não duplicar card de oferta completamente diferente.

### 9. Mobile

Missões precisam ser administráveis no celular:

- criar;
- editar;
- pausar;
- retomar;
- cancelar;
- abrir oferta.

## Não fazer

Não alterar:

- matching;
- IA de interpretação do Telegram;
- regras de criação;
- quotas;
- scheduler;
- collection worker;
- semântica de status.

Se algum bug funcional for descoberto, reportar e corrigir apenas se necessário para a experiência.

## Testes / validação

Testar:

- lista;
- criação;
- edição;
- pausa;
- retomada;
- cancelamento;
- detalhe;
- USER;
- ADMIN usando funções USER;
- dark/light;
- mobile;
- estados vazios/erro/loading.

Inspeção real obrigatória.

## Critério de conclusão

O usuário deve conseguir criar e gerenciar uma missão sem precisar conhecer a arquitetura do GG Oferta.

---

# SUBTASK 15 — Redesign de Home + Perfil / Minha Conta

## Objetivo

Transformar a Home autenticada em um painel realmente útil para o usuário e consolidar a área Minha Conta/Perfil.

A Home não deve ser apenas uma página de boas-vindas.

Ela deve responder:

- o que está acontecendo;
- o que preciso ver;
- qual é minha próxima ação.

## Parte A — Home

### 1. Conteúdo real

Usar somente dados reais já disponíveis.

Possíveis informações, se suportadas atualmente:

- missões ativas;
- missões pausadas;
- ofertas recentes/relevantes;
- atalhos para criar missão;
- atalhos para pesquisar;
- status de conta/Telegram;
- avisos reais.

Não criar dashboard fake.

### 2. Prioridade

A Home deve favorecer ações:

- Criar missão;
- Pesquisar;
- Ver ofertas;
- Ver missões.

Não ocupar metade da tela com saudação.

Uma saudação discreta pode permanecer.

### 3. ADMIN

ADMIN usa a mesma Home USER.

Não encher a Home com métricas administrativas.

Administração continua em `/admin`.

Se houver um aviso/atalho administrativo discreto, só usar se houver valor real.

### 4. Estados

Home deve funcionar para:

- usuário novo sem missão;
- usuário com missão;
- usuário sem oferta;
- usuário com ofertas;
- Telegram vinculado/não vinculado;
- quotas/limites quando realmente aplicáveis.

## Parte B — Minha Conta / Perfil

### 1. Estrutura

Organizar a conta por seções claras:

- Perfil;
- Segurança;
- Telegram;
- Preferências;
- Notificações;
- estado de e-mail;
- capacidade/plano, apenas se realmente existente.

Não mostrar detalhes internos desnecessários.

### 2. Segurança

Reaproveitar os fluxos de credenciais já existentes:

- alterar senha;
- estado de e-mail;
- recuperação;
- sessões quando aplicável.

Não reimplementar segurança.

### 3. Telegram

Mostrar estado de vínculo de forma simples.

Se existir ação real de vincular/desvincular, apresentá-la.

Não inventar ação.

### 4. Preferências

Organizar preferências atuais sem duplicar regras.

Não obrigar preferências que antes eram opcionais.

### 5. Feedback

Usar Toast/Dialog/estados da Subtask 12.

Evitar alerts nativos.

### 6. Mobile

Perfil deve ser confortável em mobile.

Evitar formulário gigante sem seções.

## Não fazer

Não alterar autenticação.

Não alterar recovery/challenges.

Não criar plano pago.

Não criar métricas falsas.

Não alterar Telegram backend sem necessidade.

## Testes / validação

Home:

- usuário novo;
- usuário com dados;
- estados vazios;
- atalhos;
- ADMIN.

Perfil:

- edição;
- alteração de senha;
- Telegram;
- e-mail;
- preferências;
- dark/light;
- mobile.

Inspeção visual real obrigatória.

## Critério de conclusão

Home deve ser útil; Perfil deve ser organizado e previsível.

---

# SUBTASK 16 — Redesign do Admin

## Objetivo

Redesenhar a área administrativa por último, usando toda a fundação criada nas etapas anteriores.

O Admin deve continuar dentro do mesmo produto e do mesmo shell, mas com ferramentas adequadas a ADMIN/DEV conforme permissões reais.

## Escopo

Revisar e redesenhar:

- dashboard Admin;
- usuários;
- operações;
- controles existentes;
- feedback/suporte/sugestões de loja;
- métricas reais disponíveis;
- estados/ações administrativas;
- qualquer ferramenta administrativa já existente e funcional.

Não inventar ferramentas sem backend.

### 1. Admin integrado

Preservar decisão anterior:

- mesmo login;
- mesmo shell;
- seção Principal;
- seção Administração.

Não criar "outro sistema".

### 2. Dashboard

Mostrar somente dados administrativos reais.

Não criar gráficos/números fake.

Priorizar:

- saúde operacional;
- usuários;
- tarefas/itens que exigem atenção;
- feedback pendente;
- ações reais.

### 3. Users

Redesenhar gerenciamento de usuários:

- listagem;
- status;
- role;
- ações existentes;
- criação manual se ainda existir;
- bloqueio/desbloqueio, se suportado;
- exclusão/tombstone, se suportado.

A autorização continua no backend.

### 4. Feedback

A Subtask 7 deixou backend pronto para:

- bug;
- support;
- store_suggestion;
- channel;
- status new/reviewed/closed.

Agora criar a UI Admin real para isso.

Deve permitir:

- listar;
- filtrar;
- abrir;
- identificar usuário/canal/data;
- ler mensagem;
- ver loja/link quando aplicável;
- marcar reviewed;
- marcar closed.

Não transformar em help desk completo.

Não criar:

- SLA;
- chat de ticket;
- prioridade complexa;
- assignment;
- automações.

### 5. Operações

Revisar ferramentas existentes.

Substituir:

- prompt;
- confirm;
- alert;

por componentes adequados quando ainda existirem.

Ação perigosa exige confirmação apropriada.

### 6. Tabelas

Aplicar padrão da Subtask 12:

- paginação;
- filtros;
- ordenação;
- responsive behavior;
- empty/loading/error.

No mobile, tabelas podem virar cards/listas quando necessário.

### 7. DEV

Não assumir ADMIN e DEV como equivalentes visualmente além das permissões efetivas.

A UI deve refletir o que backend autoriza.

Não conceder funcionalidade nova.

### 8. Saúde / métricas

Se backend fornece dados reais:

- apresentar.

Se não fornece:

- não inventar.

Não transformar essa etapa em observability backend.

### 9. Auditoria

Se já existem `AuditEntry`/logs administrativos, mostrar apenas o que fizer sentido e estiver realmente disponível.

Não construir novo sistema de auditoria.

### 10. Segurança

Validar:

- USER não acessa `/admin`;
- endpoints retornam 403;
- ADMIN funciona;
- DEV conforme permissões;
- manipulação frontend não concede acesso.

## Não fazer

Não criar:

- novo RBAC;
- novo sistema de ticket;
- novas métricas backend;
- novas APIs só para preencher layout;
- novo collector;
- alterações em PROD.

Se a UI revelar falta de endpoint realmente necessário, reportar antes de ampliar backend.

## Testes / validação

Testar:

- acesso USER;
- ADMIN;
- DEV quando viável;
- dashboard;
- usuários;
- feedback;
- filtros;
- paginação;
- ações destrutivas;
- dialogs;
- toast;
- dark/light;
- desktop/mobile.

Inspeção real obrigatória.

## Critério de conclusão

Admin deve parecer parte do GG Oferta, não um painel improvisado separado.

As ferramentas administrativas existentes devem ficar claras, consistentes e seguras.

---

# Ordem final de execução

Executar estritamente:

1. Subtask 12 — Fundação visual / Design System
2. Subtask 13 — Pesquisa + Ofertas
3. Subtask 14 — Missões
4. Subtask 15 — Home + Perfil
5. Subtask 16 — Admin

Cada uma deve:

1. auditar o estado atual;
2. usar `frontend-design`;
3. implementar somente seu escopo;
4. rodar testes;
5. fazer inspeção visual real;
6. apresentar relatório;
7. PARAR sem commit;
8. aguardar aprovação;
9. somente então fazer commit;
10. nunca iniciar a próxima automaticamente.

---

# Formato de relatório para cada Subtask

Ao final de cada etapa, reportar:

1. estado encontrado antes;
2. problemas identificados;
3. conceito visual adotado;
4. arquitetura/componentes reutilizados;
5. arquivos alterados;
6. funcionalidades preservadas;
7. eventuais bugs reais encontrados;
8. testes;
9. typecheck;
10. lint;
11. desktop dark;
12. desktop light;
13. mobile dark;
14. mobile light;
15. acessibilidade;
16. console/network;
17. limitações ou itens conscientemente adiados;
18. `git diff --check`;
19. `git status --short`.

Finalizar sempre com:

**NÃO commitado. Aguardando revisão.**
