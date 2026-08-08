# Arquitetura

O alvo é um monólito modular em FastAPI. Os módulos de missões, catálogo/coleta, preços, compra, eventos, Telegram e IA devem possuir fronteiras explícitas, mas ser implantados juntos no MVP.

PostgreSQL será a fonte transacional. Serviços externos serão acessados por
adaptadores. O contrato assíncrono do AI Provider Manager é a fronteira
transversal obrigatória para qualquer chamada de IA; adaptadores, seleção de
modelos e fallback permanecem nas tarefas de implementação dos perfis.

A implementação atual contém o monólito FastAPI, módulos persistentes de catálogo, auditoria e missões e uma fronteira assíncrona de coleta. `app.collection` despacha pedidos por fonte, transporta resultados brutos e oferece uma sessão Playwright/Chromium isolada; não contém providers concretos, normalização ou persistência, responsabilidades adicionadas somente pelas tarefas correspondentes.

As interfaces HTTP seguem `docs/API_CONVENTIONS.md`. Endpoints de negócio serão versionados sob `/api/v1`; endpoints operacionais permanecem fora desse prefixo.

Aplicação e servidor emitem logs JSON em `stdout` conforme `docs/LOGGING.md`.
A TASK-045 separa os sinais operacionais: Prometheus coleta métricas
diretamente da API e do worker; traces sanitizados seguem por OTLP/HTTP ao
OpenTelemetry Collector e depois ao Jaeger. Essas ferramentas não são
dependências funcionais da API. `/health` verifica somente o processo e
`/ready` somente o PostgreSQL necessário para servir trabalho.

A TASK-036 adicionou um segundo processo da mesma imagem do monólito:
`telegram_notifier` executa `app.telegram.worker`, consome alertas no
PostgreSQL com locks transacionais e envia pela Bot API. Não é um novo serviço
de domínio nem uma fila externa; API e worker compartilham código, migrações e
banco e são implantados juntos pelo Docker Compose, inclusive em Linux
headless/Ubuntu Server.

As TASKs 038 a 041 adicionaram `app.purchase` como módulo determinístico. Ele
consulta missões, fontes, coletas e histórico já persistidos, produz recomendação
e comparação e cria uma confirmação vinculada à evidência apresentada. Não usa
IA nem processo novo. Elegibilidade e ordenação são compartilhadas; a
confirmação expira em 15 minutos, preserva a observação original como
proveniência e revalida os dados materiais correntes. A TASK-041 persiste a
solicitação imutável e sua resolução append-only no PostgreSQL, sem status
mutável nem qualquer ação financeira.
