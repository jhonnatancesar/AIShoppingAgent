# TASK-091 — Fundação da aplicação web (item 1 da V1.2)

Status: **Implementada, validada e endurecida (2026-08-21/22) —
aguardando autorização do usuário para commit.**

## Decisões de preflight (usuário, 2026-08-21)

1. **Renderização:** SPA separada (React), consumindo a API do backend —
   não server-rendered.
2. **Framework:** React + Vite.
3. **Topologia/autenticação:** mesmo domínio — FastAPI serve o build
   estático da SPA (`StaticFiles`) sob o mesmo domínio/porta da API; login
   grava um cookie `httpOnly` (sessão de navegador nova e própria, não
   `UserAuthSession` — ver "Escopo"); sem CORS a configurar, sem servidor
   HTTP separado para o frontend.

## Origem

Item 1 da ordem de execução da V1.2 (`docs/internal/v1.2-scope.md`,
reorganizada em `DEC-072`/`DEC-073`): a V1.2 passa a ter como núcleo uma
aplicação web completa, com `/app` para USER e `/admin` para DEV/ADMIN
exclusivo, na mesma aplicação/backend/banco de hoje. Esta é a primeira
TASK de toda a V1.2 e pré-requisito de todas as seguintes — nenhuma outra
TASK da V1.2 pode começar antes desta fechar.

## Objetivo

Construir a base da aplicação web: autenticação, sessão de navegador,
autorização, separação real entre `/app` (USER) e `/admin` (DEV/ADMIN
exclusivo), layout/base visual mínimo, e proteção real de rotas e
endpoints — o backend valida autorização em todo caso, a interface nunca é
a única proteção de um recurso administrativo.

## Escopo

- `frontend/` — SPA React + Vite nova: roteamento próprio (`/app/...`,
  `/admin/...`), layout/base visual mínimo (sem biblioteca de componentes
  pesada nesta TASK), chamadas à API do backend.
- Backend serve o build estático da SPA (`StaticFiles`/`HTMLResponse` de
  fallback para roteamento client-side) sob o mesmo domínio/porta da API
  já existente — sem CORS, sem servidor HTTP separado.
- Sessão de navegador **nova e própria** (tabela dedicada, cookie
  `httpOnly`) — não reaproveita `UserAuthSession`, que é acoplada a
  `telegram_user_id` e usada hoje só para autorizar comandos do Telegram;
  misturar os dois canais na mesma tabela arriscaria lógica já validada
  em produção. Autenticação de login usa as credenciais já existentes
  (`UserCredential`, Argon2id, TASK-061) — a senha continua sendo a mesma
  que hoje autentica pelo link emitido via Telegram (`/recuperar`), sem
  uma segunda senha por canal.
- Endpoints de API para login/logout/sessão atual (`/api/v1/...`,
  convenção já existente em `docs/development/api-conventions.md`).
- Autorização real no backend em toda rota/endpoint `/admin`, reutilizando
  a matriz fail-closed já existente (`app.authorization`, papel único
  `USER ⊂ ADMIN ⊂ DEV`, `DEC-034`) — sem introduzir `user_roles`,
  múltiplos papéis simultâneos, RBAC avançado nem planos FREE/PLUS/PRO
  (`DEC-073`, ficam na V2).

## Fora de escopo

- Qualquer feature de negócio da V1.2 (itens 2 a 16) — gerenciamento de
  missões, página de produto, avaliações, histórico/gráficos, comparação
  entre lojas, pesquisa, dashboard técnico, administração de dados,
  controles operacionais, cupons, frete autenticado, Magalu, menor preço
  histórico externo, lives.
- Qualquer mudança no Telegram, no `IntentInterpreter`, na coleta ou no
  modelo de missões — esta TASK só constrói a casca da aplicação web.
- `user_roles`, múltiplos papéis simultâneos, RBAC avançado, planos
  FREE/PLUS/PRO (`DEC-073`, V2).

## Critérios de aceite

1. `/app` e `/admin` existem como rotas navegáveis, com layout base.
2. Login/logout funcionam com a senha já existente (`UserCredential`).
3. USER autenticado nunca acessa `/admin` (autorização checada no
   backend, testada com tentativa direta de URL, não só ausência de link
   na interface).
4. DEV/ADMIN acessa `/admin` normalmente.
5. Nenhuma TASK futura da V1.2 precisa reabrir a fundação para adicionar
   uma página nova.
6. Pipeline oficial completo aprovado; validação real contra PostgreSQL e
   navegador real (não só testes unitários/mock).

## Critérios de aceite — endurecimento (usuário, 2026-08-21)

Antes de considerar a fundação web fechada, o usuário pediu 4 pontos
adicionais de endurecimento -- registrados aqui como critérios de aceite
formais, não como escopo novo:

7. Token de `WebSession` gerado por gerador criptograficamente seguro,
   com entropia real documentada; banco nunca guarda o token bruto, só o
   hash.
8. Atributos do cookie de sessão (`HttpOnly`, `Secure`, `SameSite`,
   `Path`, `Max-Age`) corretos e controlados por configuração de
   ambiente -- nunca hardcoded para produção.
9. CSRF: toda operação mutável (`POST`/`PUT`/`PATCH`/`DELETE`)
   autenticada por cookie exige defesa explícita; `GET`/`HEAD` nunca
   exigem.
10. Fixação de sessão: login sempre emite identificador novo, nunca
    promove um identificador pré-existente/fornecido pelo cliente.
11. O build do frontend precisa estar dentro da imagem Docker real que
    roda em produção -- Node só em build-time, nunca em runtime; os três
    serviços (`api`/`telegram_notifier`/`collection_worker`) continuam a
    mesma imagem.
12. Deep link/refresh em qualquer rota client-side da SPA nunca retorna
    404; qualquer caminho que não pertença explicitamente à SPA (`/api/*`
    e qualquer outra rota real de backend, presente ou futura) nunca cai
    no fallback da SPA, para nenhum método HTTP.
13. Contas de teste usadas na validação existem só no banco descartável
    local, nunca em migration/seed/código/imagem/documentação com senha.

## Resultado (2026-08-21/22)

Todos os 13 critérios de aceite (6 originais + 7 de endurecimento)
validados, após 4 rodadas de revisão do usuário. Ver `DEC-074`
(`docs/internal/decision-log.md`) para o detalhamento arquitetural das
decisões de endurecimento (incluindo o que mudou a cada rodada).

Rodada 2 corrigiu 3 problemas concretos apontados na revisão da rodada 1:
frontend estava em JavaScript em vez de TypeScript (decisão de preflight
já aprovada, não cumprida); CSRF só cobria `POST`/`DELETE` por rota
individual, arriscando ficar sem proteção em `PUT`/`PATCH` de TASKs
futuras (corrigido para middleware genérico sobre `/api/v1/`); isolamento
de `/api/*` usava blacklist de prefixos de backend, corrigido para
whitelist explícita das rotas da SPA.

Rodada 3 corrigiu o próprio CSRF genérico da rodada 2: o middleware sobre
`/api/v1/` era amplo demais -- mascarava `404` de rota inexistente como
`403 csrf_invalid`, e protegeria erroneamente um futuro endpoint
autenticado por Bearer/service token só por estar sob esse prefixo.
Corrigido para uma dependência escopada ao router da `WebSession`
(`app.webapp.router.router`). Efeito colateral descoberto na validação: o
catch-all da SPA, registrado só para `GET`, devolvia `405` (não `404`)
para métodos mutáveis numa rota inexistente -- também corrigido.

Rodada 4 corrigiu o CSRF de novo, no sentido oposto: escopar ao router era
estreito demais -- só protegeria endpoints daquele router específico, e
um endpoint futuro de missões/ofertas/admin em outro router não herdaria
nada. Desenho final: CSRF acoplado à dependência `require_web_session`
(`app.webapp.dependency`), não a nenhum router -- a proteção acompanha a
autenticação por cookie, em qualquer módulo.

### Sessão e cookie (auditoria, ponto 1)

- **Token:** `secrets.token_urlsafe(32)` — 32 bytes de `os.urandom`
  (gerador criptograficamente seguro), 256 bits de entropia, 43
  caracteres base64url. Imprevisível (testado com 50 emissões
  consecutivas, nenhuma colisão).
- **Armazenamento:** só `token_digest()` (SHA-256) vai para
  `web_sessions.token_hash`; o token bruto nunca é persistido, só
  devolvido para o cookie do cliente (`tests/test_authentication_service_web.py`
  prova isso diretamente).
- **Rotação/invalidação:** todo login (`issue_web_session`) revoga toda
  sessão ativa anterior do usuário antes de emitir a nova — nunca há mais
  de uma sessão web ativa por usuário. Logout (`revoke_web_session`)
  marca `revoked_at` e limpa o cookie.
- **TTL absoluto:** 12h, reforçado por `CHECK` no banco
  (`ck_web_sessions_absolute_ttl`) — testado contra Postgres real
  (`test_expires_at_matches_the_absolute_ttl_check_constraint`).
- **Após expiração/logout:** `get_web_session_user` retorna `None` sem
  distinguir "expirado" de "revogado" de "nunca existiu" (evita
  enumeração) — usuário precisa logar de novo, sem renovação silenciosa.
- **Múltiplas sessões:** política "uma sessão ativa por vez", igual à
  `UserAuthSession` já existente.

### Cookie (ponto 1)

Atributos reais confirmados por teste (`tests/test_webapp_router.py`) e
por captura HTTP real contra o container: `HttpOnly=true`; `Secure`
controlado por `settings.environment == "production"` (nunca hardcoded —
testado nos dois sentidos: ausente em `development`, presente em
`production`); `SameSite=Lax`; `Path=/`; `Max-Age=43200` (12h).

### Token no banco (ponto 2)

Já estava correto no desenho original: `web_sessions.token_hash` guarda
só o SHA-256 do token (64 caracteres hex), nunca o valor bruto. Fluxo
confirmado: `token = secrets.token_urlsafe(32)` → cookie recebe o bruto →
banco recebe `hashlib.sha256(token).hexdigest()` → lookup por hash na
requisição seguinte.

### CSRF (ponto 3, bloqueador)

Implementado (não existia antes desta rodada de endurecimento):
double-submit cookie girado na fronteira de login. Ver `DEC-074` para o
histórico completo das 4 rodadas -- resumo do desenho final:

`app.webapp.dependency` tem dois níveis. `_resolve_web_session` (interno,
nunca usado fora do módulo) só resolve cookie → hash → `WebSession` →
`User`, `401` se ausente/inválida/expirada/revogada, nunca aplica CSRF.
`require_web_session` (pública -- **a** dependência de autenticação web)
depende de `_resolve_web_session` e, só para métodos mutáveis, chama
`app.webapp.csrf.validate_csrf`. `require_admin_web_session` compõe sobre
`require_web_session`, acrescentando `Permission.ADMIN_PANEL_ACCESS`.

Qualquer endpoint de **qualquer** router/módulo que declare
`Depends(require_web_session)` (ou `require_admin_web_session`) herda
autenticação por cookie **e** CSRF automaticamente -- a proteção
acompanha a dependência, não o router onde o endpoint mora. Login é a
única exceção explícita: ainda não existe `WebSession` nesse ponto, então
`create_web_session` valida CSRF diretamente via `Depends(validate_csrf)`,
usando o cookie anônimo emitido pela casca da SPA antes do login. Logout
e a consulta de sessão atual dependem de `require_web_session` como
qualquer outro endpoint do canal web -- nenhuma configuração específica
de router.

Ordem das validações, garantida pela própria árvore de dependências do
FastAPI (não por um `if` manual): sessão primeiro (`401` se inválida),
CSRF depois, só se a sessão for válida e o método for mutável (`403`).
Um request sem sessão nenhuma nunca é confundido com falha de CSRF.

Prova arquitetural dedicada (`tests/test_webapp_dependency.py`): um
router propositalmente sem relação nenhuma com `app.webapp.router`
(simulando missões), com endpoints usando só
`Depends(require_web_session)`/`Depends(require_admin_web_session)`,
exige CSRF/autenticação/autorização exatamente como o router de sessão
web -- prova de que a proteção acompanha a dependência, não o router.

Validado com `TestClient` (login válido/ausente/inválido; todo método
mutável autenticado por `WebSession` sem CSRF → `403`, com CSRF válido →
aceito; sessão inválida → `401` mesmo com CSRF válido; `GET`/`HEAD`/
`OPTIONS` nunca exigem; endpoint fora de `require_web_session` não
afetado; composição ADMIN completa) e com requisições HTTP reais contra o
container: login sem CSRF → `403`; logout sem sessão nenhuma → `401` (não
`403`); `POST`/`PUT`/`PATCH`/`DELETE` para `/api/v1/rota-inexistente` →
`404` (não `403`, não `405`); `POST /telegram/webhook` sem CSRF (payload
inválido de propósito) → `422` (nunca `403 csrf_invalid`); fluxo completo
login → `/app` → `/admin` (USER negado, DEV permitido) → logout,
validado ponta a ponta no container real.

### Fixação de sessão (ponto 4)

Já coberta pelo desenho original — `issue_web_session` não tem parâmetro
para receber um identificador externo, sempre gera um novo e revoga o
anterior. 4 testes novos provam isso explicitamente (incluindo inspeção
de assinatura da função).

### Docker (pontos 5–9)

`Dockerfile` movido para a raiz, multi-stage (`frontend-build` com
`node:22-alpine` → estágio Python final via `COPY --from=frontend-build`).
Node confirmado ausente do runtime (`which node` dentro do container
construído → não encontrado). Contexto de build dos 3 serviços mudou de
`./backend` para `.` em `compose.yaml`, mesma imagem única — nenhuma
duplicação. `AISHOPPING_SPA_DIST_DIR=/app/frontend-dist` só no `api`.
Validado com `docker compose build` real + `docker compose up` real
contra Postgres containerizado (não só `npm run build` no host):
`frontend-dist/index.html` e `assets/*.{js,css}` confirmados dentro da
imagem construída.

### SPA routing / `/api` isolado (ponto 8)

Corrigido um bug real encontrado durante a auditoria: o catch-all
`/{full_path:path}` da SPA mascarava qualquer rota de API inexistente
como `200 index.html`. Rodada 1 corrigiu com uma blacklist de prefixos
reservados; rodada 2 substituiu por **whitelist explícita** das rotas da
SPA (`_SPA_OWNED_TOP_LEVEL_SEGMENTS = {"", "login", "app", "admin"}`,
espelhando `frontend/src/App.tsx`), apontada pelo usuário como o desenho
mais seguro: qualquer caminho fora da whitelist é `404` por padrão, sem
depender de alguém lembrar de listar cada rota de backend nova.
Testado com `TestClient` (`tests/test_webapp_spa.py`: caminhos de
API/health/etc., e também caminhos genuinamente desconhecidos como
`qualquer-coisa-desconhecida`, `app2`, `loginx` — todos `404`) e com
requisições HTTP reais contra o container (`/api/v1/rota-que-nao-existe`
e `/rota-totalmente-desconhecida` → `404`, nunca `index.html`). Deep link
(`/app/rota-profunda`, `/admin/rota-profunda`) e refresh confirmados
servindo a mesma casca (`200`), tanto em teste quanto no container real.

Correção da rodada 3: o catch-all estava registrado só para `GET`
(`@app.get(...)`), então uma rota inexistente atingida por
`POST`/`PUT`/`PATCH`/`DELETE` batia no padrão de caminho (`/{full_path:path}`
casa com qualquer string) mas não no método -- Starlette respondia `405
Method Not Allowed`, não `404`. Corrigido para `app.api_route(...,
methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])`, com `404`
imediato para qualquer método que não seja `GET`/`HEAD` (a SPA só serve
navegação `GET`). Testado com os 4 métodos mutáveis contra uma rota
inexistente, em teste e no container real.

### Frontend em TypeScript (correção da rodada 2)

A decisão de preflight (2026-08-21, aprovada pelo usuário via
`AskUserQuestion`) já definia React + **TypeScript** + Vite; a
implementação da rodada 1 saiu em JavaScript puro por engano. Corrigido:
todos os arquivos fonte convertidos para `.tsx`/`.ts`
(`tsconfig.json`/`tsconfig.app.json`/`tsconfig.node.json` no padrão do
scaffold oficial `react-ts` do Vite), `npm run build` agora roda
`tsc -b && vite build` (o build falha se houver erro de tipo). Tipos
explícitos para o usuário da sessão, o cliente HTTP e o contexto de
autenticação. `npm run lint`/`npm run build` (com checagem de tipos)
aprovados; a imagem Docker foi reconstruída com o frontend em TypeScript
e revalidada contra o container real.

### Autorização em deep link real (ponto 9)

Repetido contra o container real (não só `TestClient`): USER →
`/app` (200), `/admin` (307 redirect para `/login`); DEV → `/admin`
(200), `/admin/rota-profunda` (200).

### Contas de teste (pontos 10–11)

Todas as contas de validação usadas nas quatro rodadas existiram só dentro
de containers Postgres descartáveis criados exclusivamente para cada
validação — nunca em migration, seed, código-fonte, `.env` ou imagem
Docker. Cada container e seu volume foram destruídos (`docker compose
down -v`) ao final de cada rodada; nenhum dado sobrevive. Busca por
usernames/senhas nos arquivos alterados desta TASK não encontrou nenhuma
senha ou segredo real. `Gitleaks` (parte de `scripts/check.ps1`) aprovado
contra árvore de trabalho, arquivos versionados e histórico do Git em
todas as rodadas.

### Configuração (ponto 12)

Nenhuma configuração nova dispersa: `Secure` do cookie usa
`settings.environment` (já existente); TTL de sessão usa `SESSION_TTL`
(já existente); caminho do build da SPA usa `settings.spa_dist_dir` (já
existente, TASK-091 original); CSRF não precisou de configuração nova
(cookie espelha o mesmo padrão do cookie de sessão). Nenhuma leitura
direta de `os.environ` introduzida.

### Pipeline final (ponto 15)

`scripts/check.ps1`: 1372 testes unitários + cobertura 90,54% (subiu de
1316/90,38% no início do endurecimento, com 56 testes novos ao longo das
quatro rodadas -- incluindo o novo `tests/test_webapp_dependency.py`
dedicado à prova arquitetural de CSRF), grafo de migrações com head
único, Docker Compose config aprovado, 57 testes de integração PostgreSQL
(incluindo os 8 de `WebSession`). `webapp/csrf.py`, `webapp/dependency.py`,
`webapp/router.py` e `webapp/spa.py` em 100% de cobertura. `ruff check .`
limpo em todo o projeto. `git diff --check` sem erro de whitespace
(incluindo os arquivos novos, verificado via stage temporário +
`git diff --cached --check`, depois desfeito). `npm run lint`/`npm run
build` (com `tsc -b`) do `frontend/` aprovados. `docker compose build`/`up`
reais aprovados quatro vezes (uma por rodada) contra Postgres
containerizado, incluindo a validação funcional completa do desenho final
de CSRF (seção CSRF acima).

### Limitações reais restantes

Nenhuma bloqueante identificada. Fica fora do escopo desta TASK (V1.2,
itens 2+): qualquer feature de negócio sobre a fundação (missões,
avaliações, histórico, dashboard, etc.).

### Conclusão

**TASK-091 pronta para commit**, pendente autorização explícita do
usuário (nenhum commit, push ou redeploy foi feito).
