// Bootstrap determinístico das connections `searxng-search` e `firecrawl`
// no OmniRoute -- as duas capabilities que o César Core expõe via
// `CESAR_CORE_SEARCH_DEFAULT_PROVIDER`/`CESAR_CORE_FETCH_DEFAULT_PROVIDER`
// (`deploy/prod/cesar-core.compose.yaml`). Diferente das 4 connections de
// AI (`deploy/prod/omniroute-provisioning.md`, seção 2 -- ainda manual,
// não mexidas por este script) e diferente das 3 credenciais consumidoras
// Core -> OmniRoute (`bootstrap-omniroute-keys.js` -- outro recurso,
// `/api/keys`, não `/api/providers`). Ver
// `docs/operations/prod-deployment-handoff.md`, seção 5.1, para o
// procedimento completo -- este arquivo só entra em execução dentro de um
// container descartável, nunca como serviço de longa duração.
//
// Schema real das duas connections auditado direto no OmniRoute DEV que já
// tem as duas funcionando (`GET /api/providers`, `createProviderSchema` em
// `C:\omniroute\src\shared\validation\schemas\provider.ts`):
// - searxng-search: sem `apiKey` (provider sem autenticação --
//   `providerAllowsOptionalApiKey` inclui "searxng-search" e "firecrawl"
//   explicitamente); precisa de `providerSpecificData.baseUrl` apontando
//   para o SearXNG real do bundle (`http://searxng:8080/search` -- nunca o
//   default `http://localhost:8888/search` que só existe como placeholder
//   de UI em `providerPageHelpers.ts`, nunca persistido).
// - firecrawl: precisa de `apiKey` real (schema-opcional, mas
//   operacionalmente exigido para o Firecrawl Cloud responder de verdade);
//   sem `providerSpecificData` nenhum.
// Não existe campo "capability" persistido em nenhuma das duas -- é
// implícito no próprio valor de `provider`, resolvido internamente pelo
// OmniRoute.
//
// Contrato de idempotência (mesmo espírito de bootstrap-omniroute-keys.js,
// adaptado a connections em vez de keys):
// - connection já existe E configuração bate com o esperado -> pula.
// - connection já existe mas configuração diverge (baseUrl errado,
//   isActive=false, ou firecrawl sem apiKey) -> PARA com erro explícito,
//   nunca corrige sozinho (pode ser customização deliberada do operador).
// - connection não existe -> cria com o valor exato documentado aqui.
// - nunca imprime o valor de nenhum apiKey (nem o admin, nem o do
//   Firecrawl) -- só nomes/status.
"use strict";

const BASE = process.env.OMNIROUTE_BASE_URL || "http://omniroute:20128";
const SEARXNG_BASE_URL = "http://searxng:8080/search";

function fail(message) {
  console.error("BOOTSTRAP_FAILED: " + message);
  process.exit(1);
}

async function login(password) {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const setCookie = res.headers.get("set-cookie") || "";
  const match = setCookie.match(/auth_token=([^;]+)/);
  if (!res.ok || !match) throw new Error(`login failed: ${res.status}`);
  return `auth_token=${match[1]}`;
}

async function findConnection(cookie, provider, name) {
  const res = await fetch(
    `${BASE}/api/providers?provider=${encodeURIComponent(provider)}&limit=200`,
    { headers: { cookie } }
  );
  if (!res.ok) throw new Error(`list providers failed: ${res.status}`);
  const body = await res.json();
  return (body.connections || []).find((c) => c.name === name) || null;
}

async function createConnection(cookie, payload) {
  const res = await fetch(`${BASE}/api/providers`, {
    method: "POST",
    headers: { "content-type": "application/json", cookie },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.connection || !body.connection.id) {
    // Nunca inclui o corpo cru da resposta no erro -- pode ecoar de volta
    // um apiKey em texto claro se o backend algum dia mudar o shape.
    throw new Error(`create connection "${payload.name}" failed: ${res.status}`);
  }
  return body.connection;
}

async function ensureSearxng(cookie) {
  const name = "searxng-search";
  const existing = await findConnection(cookie, "searxng-search", name);
  if (existing) {
    const baseUrl =
      existing.providerSpecificData && existing.providerSpecificData.baseUrl;
    if (existing.isActive && baseUrl === SEARXNG_BASE_URL) {
      console.log(`OK  ${name}: already provisioned with correct baseUrl, skipped (idempotent).`);
      return;
    }
    fail(
      `${name}: a connection with this name already exists in OmniRoute, ` +
        `but its configuration is incompatible (isActive=${existing.isActive}, ` +
        `baseUrl=${JSON.stringify(baseUrl)}, expected ${JSON.stringify(SEARXNG_BASE_URL)}). ` +
        `Refusing to modify it automatically -- review manually in the OmniRoute admin panel.`
    );
    return;
  }
  await createConnection(cookie, {
    provider: "searxng-search",
    name,
    priority: 1,
    providerSpecificData: { baseUrl: SEARXNG_BASE_URL },
  });
  console.log(`OK  ${name}: created with baseUrl=${SEARXNG_BASE_URL}.`);
}

async function ensureFirecrawl(cookie) {
  const name = "firecrawl";
  const existing = await findConnection(cookie, "firecrawl", name);
  if (existing) {
    if (existing.isActive && existing.apiKey) {
      console.log(`OK  ${name}: already provisioned with an API key, skipped (idempotent).`);
      return;
    }
    fail(
      `${name}: a connection with this name already exists in OmniRoute, ` +
        `but its configuration is incompatible (isActive=${existing.isActive}, ` +
        `apiKey ${existing.apiKey ? "present" : "missing"}). Refusing to modify it ` +
        `automatically -- review manually in the OmniRoute admin panel.`
    );
    return;
  }
  const apiKey = process.env.FIRECRAWL_API_KEY;
  if (!apiKey) {
    fail(
      `${name}: no connection found and FIRECRAWL_API_KEY was not provided -- ` +
        `cannot create one without a real key. Set FIRECRAWL_API_KEY and re-run.`
    );
    return;
  }
  await createConnection(cookie, {
    provider: "firecrawl",
    name,
    apiKey,
    priority: 1,
  });
  console.log(`OK  ${name}: created with an API key.`);
}

async function main() {
  const password = process.env.OMNIROUTE_ADMIN_PASSWORD;
  if (!password) fail("OMNIROUTE_ADMIN_PASSWORD not set");

  const cookie = await login(password);
  await ensureSearxng(cookie);
  await ensureFirecrawl(cookie);

  console.log("BOOTSTRAP_DONE");
}

main().catch((err) => fail(err.message));
