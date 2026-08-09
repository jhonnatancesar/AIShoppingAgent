# Arquitetura

O alvo é um monólito modular em FastAPI. Os módulos de missões, catálogo/coleta, preços, compra, eventos, Telegram e IA devem possuir fronteiras explícitas, mas ser implantados juntos no MVP.

PostgreSQL será a fonte transacional. Serviços externos serão acessados por
adaptadores. O contrato assíncrono do AI Provider Manager é a fronteira
transversal obrigatória para qualquer chamada de IA; adaptadores, seleção de
modelos e fallback permanecem nas tarefas de implementação dos perfis.

A implementação atual contém o monólito FastAPI, módulos persistentes de
catálogo, auditoria e missões e uma fronteira assíncrona de coleta.
`app.collection` despacha pedidos por fonte, transporta resultados brutos e
oferece uma sessão Playwright/Chromium isolada. Pichau, Terabyte, Amazon e
Kabum possuem providers concretos; normalização e persistência permanecem em
módulos separados.

As interfaces HTTP seguem `docs/API_CONVENTIONS.md`. Endpoints de negócio serão versionados sob `/api/v1`; endpoints operacionais permanecem fora desse prefixo.

No webhook Telegram, a confiança possui duas camadas: o segredo compartilhado
autentica o transporte e, somente depois, a TASK-046 aceita a pessoa em chat
privado direto (`chat.id == message.from.id`) quando o `User` interno está
ativo. A TASK-047 aplica RBAC/ownership e a TASK-061 exige uma sessão por senha
para operações funcionais. Credenciais, sessões e tokens formam um módulo
separado (`app.authentication`); o formulário nunca define identidade
(`adr/ADR-009-autenticacao-minima-telegram.md`, `docs/AUTHENTICATION.md`).

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

A TASK-048 retira secrets do ambiente dos contêineres. Produção usa somente
`*_FILE` apontando para mounts em `/run/secrets`, concedidos por serviço;
conflito com valor direto falha fechado. API e worker executam como usuário
non-root. O contrato e os limites operacionais estão em `docs/SECRETS.md` e
`adr/ADR-011-secrets-por-arquivo.md`.

A TASK-049 adiciona fatos persistentes mínimos para replay/rate limit e retry
de eventos, mantendo `events` imutável. Timeouts, retries de leituras seguras e
circuit breakers por integração ficam locais a cada processo; nenhuma operação
potencialmente não idempotente recebe retry cego. O desenho não acrescenta
Redis, broker ou outro serviço (`docs/RESILIENCE.md`,
`adr/ADR-012-limites-e-resiliencia-local.md`).

A TASK-051 consolida a operação de um único Ubuntu Server em
`docs/OPERATIONS.md`. Portas da API, PostgreSQL e observabilidade bindam no
loopback por padrão; o tráfego entre serviços continua na rede interna do
Compose. Backup manual e restauração validada fornecem recuperação operacional
básica, sem promessa de disaster recovery. Rollback de código exige
compatibilidade com o schema atual e nunca autoriza downgrade destrutivo
automático (`adr/ADR-014-operacao-privada-e-recuperacao-manual.md`).

As TASKs 038 a 041 adicionaram `app.purchase` como módulo determinístico. Ele
consulta missões, fontes, coletas e histórico já persistidos, produz recomendação
e comparação e cria uma confirmação vinculada à evidência apresentada. Não usa
IA nem processo novo. Elegibilidade e ordenação são compartilhadas; a
confirmação expira em 15 minutos, preserva a observação original como
proveniência e revalida os dados materiais correntes. A TASK-041 persiste a
solicitação imutável e sua resolução append-only no PostgreSQL, sem status
mutável nem qualquer ação financeira.
