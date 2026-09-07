// Bootstrap determinístico das 3 credenciais consumidoras do OmniRoute
// (ggoferta-ai / ggoferta-search / ggoferta-fetch) usadas pelo César
// Core (`deploy/prod/cesar-core.compose.yaml`, secrets `omniroute_ai`/
// `omniroute_search`/`omniroute_fetch`). Ver
// `docs/operations/prod-deployment-handoff.md`, seção 5.1 (etapa A),
// para o procedimento completo (como rodar, em que rede, com quais
// mounts) -- este arquivo só entra em execução dentro de um container
// descartável, nunca como serviço de longa duração.
//
// Contrato:
// - Nunca imprime valor de credencial em stdout/stderr -- só grava
//   direto no arquivo de destino (bind mount).
// - Idempotente: se o arquivo de destino já existe e não está vazio,
//   pula (não recria, não sobrescreve).
// - Se a chave já existir no OmniRoute por nome mas o arquivo estiver
//   ausente, PARA com erro explícito -- nunca duplica, nunca inventa
//   valor, nunca decide sozinho como recuperar (não usa o endpoint
//   `/api/keys/{id}/reveal`, que exigiria habilitar
//   `ALLOW_API_KEY_REVEAL` no serviço de longa duração -- ver "O que
//   NÃO fazer" no handoff).
"use strict";

const fs = require("fs");
const path = require("path");

const BASE = process.env.OMNIROUTE_BASE_URL || "http://omniroute:20128";

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

async function listKeyNames(cookie) {
  const res = await fetch(`${BASE}/api/keys?limit=200`, {
    headers: { cookie },
  });
  if (!res.ok) throw new Error(`list keys failed: ${res.status}`);
  const body = await res.json();
  return new Set((body.keys || []).map((k) => k.name));
}

async function createKey(cookie, name) {
  const res = await fetch(`${BASE}/api/keys`, {
    method: "POST",
    headers: { "content-type": "application/json", cookie },
    body: JSON.stringify({ name }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || typeof body.key !== "string" || !body.key) {
    throw new Error(`create key "${name}" failed: ${res.status}`);
  }
  return body.key;
}

function writeSecretFile(outPath, value) {
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, value, { mode: 0o600 });
}

async function main() {
  const password = process.env.OMNIROUTE_ADMIN_PASSWORD;
  if (!password) fail("OMNIROUTE_ADMIN_PASSWORD not set");

  // PAIRS: "name1:outpath1,name2:outpath2,..."
  const pairsRaw = process.env.BOOTSTRAP_KEY_PAIRS;
  if (!pairsRaw) fail("BOOTSTRAP_KEY_PAIRS not set");
  const pairs = pairsRaw.split(",").map((entry) => {
    const idx = entry.indexOf(":");
    if (idx < 0) fail(`invalid pair entry: ${entry}`);
    return { name: entry.slice(0, idx), outPath: entry.slice(idx + 1) };
  });

  const cookie = await login(password);
  let existingNames = null; // lazy: only list if actually needed

  for (const { name, outPath } of pairs) {
    const already = fs.existsSync(outPath) && fs.statSync(outPath).size > 0;
    if (already) {
      console.log(`OK  ${name}: secret file already present, skipped (idempotent).`);
      continue;
    }

    if (existingNames === null) {
      existingNames = await listKeyNames(cookie);
    }
    if (existingNames.has(name)) {
      fail(
        `${name}: a connection/key with this name already exists in OmniRoute, ` +
          `but the secret file is missing. Refusing to create a duplicate or guess ` +
          `a value. Recover the original file from backup, or remove the stale key ` +
          `named "${name}" in the OmniRoute admin panel and re-run this script.`
      );
    }

    const key = await createKey(cookie, name);
    writeSecretFile(outPath, key);
    console.log(`OK  ${name}: created and written to ${outPath}.`);
  }

  console.log("BOOTSTRAP_DONE");
}

main().catch((err) => fail(err.message));
