# Arquitetura

O alvo é um monólito modular em FastAPI. Os módulos de missões, catálogo/coleta, preços, compra, eventos, Telegram e IA devem possuir fronteiras explícitas, mas ser implantados juntos no MVP.

PostgreSQL será a fonte transacional. Serviços externos serão acessados por
adaptadores. O contrato assíncrono do AI Provider Manager é a fronteira
transversal obrigatória para qualquer chamada de IA; adaptadores, seleção de
modelos e fallback permanecem nas tarefas de implementação dos perfis.

A implementação atual contém o monólito FastAPI, módulos persistentes de catálogo, auditoria e missões e uma fronteira assíncrona de coleta. `app.collection` despacha pedidos por fonte, transporta resultados brutos e oferece uma sessão Playwright/Chromium isolada; não contém providers concretos, normalização ou persistência, responsabilidades adicionadas somente pelas tarefas correspondentes.

As interfaces HTTP seguem `docs/API_CONVENTIONS.md`. Endpoints de negócio serão versionados sob `/api/v1`; endpoints operacionais permanecem fora desse prefixo.

Aplicação e servidor emitem logs JSON em `stdout` conforme `docs/LOGGING.md`, sem acoplamento a uma plataforma externa de observabilidade.

A TASK-036 adicionou um segundo processo da mesma imagem do monólito:
`telegram_notifier` executa `app.telegram.worker`, consome alertas no
PostgreSQL com locks transacionais e envia pela Bot API. Não é um novo serviço
de domínio nem uma fila externa; API e worker compartilham código, migrações e
banco e são implantados juntos pelo Docker Compose, inclusive em Linux
headless/Ubuntu Server.

As TASKs 038 e 039 adicionaram `app.purchase` como módulo somente leitura. Ele
consulta missões, fontes, coletas e histórico já persistidos e produz uma
recomendação e uma comparação determinísticas sem IA, novo processo ou nova
tabela. Os dois fluxos compartilham a mesma regra de elegibilidade e ordenação;
confirmação e trilha permanecem nas TASKs 040 e 041.
