# Arquitetura

O alvo é um monólito modular em FastAPI. Os módulos de missões, catálogo/coleta, preços, compra, eventos, Telegram e IA devem possuir fronteiras explícitas, mas ser implantados juntos no MVP.

PostgreSQL será a fonte transacional. Serviços externos serão acessados por adaptadores. O AI Provider Manager será um módulo transversal obrigatório para qualquer chamada de IA.

A implementação atual contém o monólito FastAPI, módulos persistentes de catálogo, auditoria e missões e uma fronteira assíncrona de coleta. `app.collection` despacha pedidos por fonte e transporta resultados brutos sem conhecer navegador, normalização ou persistência; essas responsabilidades são adicionadas somente pelas tarefas correspondentes.

As interfaces HTTP seguem `docs/API_CONVENTIONS.md`. Endpoints de negócio serão versionados sob `/api/v1`; endpoints operacionais permanecem fora desse prefixo.

Aplicação e servidor emitem logs JSON em `stdout` conforme `docs/LOGGING.md`, sem acoplamento a uma plataforma externa de observabilidade.
