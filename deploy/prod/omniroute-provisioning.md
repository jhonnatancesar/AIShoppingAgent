# Provisionamento determinístico: providers AI no OmniRoute (`user-cascade` / `admin-dev-cascade`)

Cópia mantida em sincronia manual do runbook original
(`C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`,
repositório `cesar-core`) -- duplicada aqui deliberadamente para que o
deploy em PROD não precise clonar/acessar aquele repositório. Se os dois
arquivos divergirem no futuro, o deste repositório (GG Oferta) é quem
importa para o deploy; o do `cesar-core` continua sendo a referência para
quem desenvolve o Core em si.

Desired state necessário para reproduzir a política de providers AI descrita
em `docs/architecture/gg-oferta-core.md` (repositório `cesar-core`, "Política
de providers AI") em qualquer ambiente (PROD incluído), sem depender do
volume/banco de um OmniRoute DEV específico. Nenhum id (connection ou combo)
é canônico -- cada ambiente gera os seus próprios ao criar os recursos
abaixo; **nunca copie um UUID de outro ambiente para um combo step**, sempre
use o id retornado pela própria criação da connection naquele ambiente.

Pré-requisito: OmniRoute 3.8.50 rodando e acessível (ver
`deploy/prod/cesar-core.compose.yaml` neste mesmo repositório), com uma
sessão administrativa válida (`POST /api/auth/login`, cookie `auth_token`).

## 1. Secrets necessários (nomes apenas -- nunca valores aqui nem no Git)

| Nome do secret | Uso |
|---|---|
| `omniroute_admin_password` | Login administrativo do OmniRoute, usado só durante o provisionamento (não é lido pelos serviços em runtime). |
| `gemini_user_api_key` | Chave Gemini (AI Studio, tier gratuito) dedicada ao perfil USER. |
| `gemini_admin_dev_api_key` | Chave Gemini dedicada a ADMIN/DEV -- **diferente da chave USER** (duas credenciais Gemini distintas, nunca a mesma). |
| `groq_admin_dev_api_key` | Chave Groq, ADMIN/DEV. |
| `openrouter_admin_dev_api_key` | Chave OpenRouter, ADMIN/DEV. |

Nenhum desses secrets é usado pelo César Core em runtime -- eles só existem
dentro do OmniRoute (`provider_connections.apiKey`, nunca fora dele) a
partir do momento em que a connection é criada. O César Core continua
usando exclusivamente a sua própria credencial de consumidor
(`CESAR_CORE_OMNIROUTE_AI_API_KEY_FILE`, ver
`deploy/prod/cesar-core.compose.yaml`), nunca uma das quatro acima.

## 2. Connections (`POST /api/providers`)

Quatro connections reais -- porque USER e ADMIN/DEV usam credenciais
Gemini distintas. Criar cada uma com prioridade alta (`priority: 1`) e
capturar o `id` retornado (necessário no passo 3):

```jsonc
// Gemini USER
{ "provider": "gemini", "apiKey": "<gemini_user_api_key>", "name": "Gemini USER", "priority": 1, "defaultModel": "gemini-3.6-flash" }

// Gemini ADMIN/DEV
{ "provider": "gemini", "apiKey": "<gemini_admin_dev_api_key>", "name": "Gemini ADMIN_DEV", "priority": 1, "defaultModel": "gemini-3.6-flash" }

// Groq ADMIN/DEV
{ "provider": "groq", "apiKey": "<groq_admin_dev_api_key>", "name": "Groq ADMIN_DEV", "priority": 1, "defaultModel": "openai/gpt-oss-120b" }

// OpenRouter ADMIN/DEV
{ "provider": "openrouter", "apiKey": "<openrouter_admin_dev_api_key>", "name": "OpenRouter ADMIN_DEV", "priority": 1, "defaultModel": "openrouter/free" }
```

Modelos: os mesmos já aprovados no GG Oferta antes da migração para o
Core (DEC-050) -- não trocar sem uma nova decisão explícita, nunca usar
`latest`/preview.

Validar cada connection isoladamente antes de prosseguir:
`POST /api/providers/{id}/test` com `{"validationModelId": "<mesmo modelo da tabela acima>"}` --
espera `valid: true`.

## 3. Combos (`POST /api/combos`, `strategy: "priority"`)

Usar os `id`s reais retornados no passo 2 (nunca um id de outro
ambiente) em cada step `connectionId`. O passo final de ambos os combos
é a string literal `"oc/mimo-v2.5-free"` (fallback gratuito/no-auth do
catálogo nativo -- prefixo `oc/`, não `opencode/`, porque providers
no-auth roteiam pelo alias).

```jsonc
// user-cascade -- USER nunca alcança Groq/OpenRouter: eles simplesmente
// não existem neste combo.
{
  "name": "user-cascade",
  "description": "USER: Gemini USER -> fallback gratuito/no-auth",
  "strategy": "priority",
  "models": [
    { "provider": "gemini", "model": "gemini-3.6-flash", "connectionId": "<id da connection Gemini USER>", "label": "Gemini USER" },
    "oc/mimo-v2.5-free"
  ]
}

// admin-dev-cascade
{
  "name": "admin-dev-cascade",
  "description": "ADMIN/DEV: Gemini ADMIN_DEV -> Groq -> OpenRouter -> fallback gratuito/no-auth",
  "strategy": "priority",
  "models": [
    { "provider": "gemini", "model": "gemini-3.6-flash", "connectionId": "<id da connection Gemini ADMIN_DEV>", "label": "Gemini ADMIN_DEV" },
    { "provider": "groq", "model": "openai/gpt-oss-120b", "connectionId": "<id da connection Groq ADMIN_DEV>", "label": "Groq ADMIN_DEV" },
    { "provider": "openrouter", "model": "openrouter/free", "connectionId": "<id da connection OpenRouter ADMIN_DEV>", "label": "OpenRouter ADMIN_DEV" },
    "oc/mimo-v2.5-free"
  ]
}
```

Validar cada passo isoladamente: `POST /api/combos/test` com
`{"comboName": "user-cascade"}` e `{"comboName": "admin-dev-cascade"}` --
cada passo distinto deve responder `status: "ok"` (o passo
`oc/mimo-v2.5-free` pode ocasionalmente exceder 15-20s de latência; é
uma característica operacional conhecida do fallback gratuito, não uma
falha de configuração -- repetir o teste antes de investigar).

## 4. Associação USER/ADMIN_DEV → combo (César Core)

Nenhum id de connection ou combo entra na configuração do César Core --
só os **nomes** dos combos, exatamente como criados no passo 3:

```
CESAR_CORE_AI_ENABLED=true
CESAR_CORE_AI_USER_MODEL=user-cascade
CESAR_CORE_AI_ADMIN_DEV_MODEL=admin-dev-cascade
```

`ai_profile=user` (contrato GG → Core) resolve para `user-cascade`;
`ai_profile=admin_dev` resolve para `admin-dev-cascade`.

## 5. Verificação final

- `GET /ready` e `GET /v1/capabilities` do César Core confirmam `ai: available`.
- `POST /v1/ai/generate` com `ai_profile=user` deve resolver via Gemini
  USER (ou o fallback, se Gemini estiver indisponível no momento);
  `ai_profile=admin_dev` deve resolver via Gemini ADMIN_DEV (ou cascata
  abaixo, se indisponível).
- `AIResponse.provider` (rastreado via header real `X-OmniRoute-Provider`
  do OmniRoute, não inferido) deve mostrar o provider realmente usado.

## 6. Gate obrigatório antes de ativar em PROD: prova do happy path Gemini

Esta política foi validada em DEV com o fallback completo (ver
`docs/operations/prod-deployment-handoff.md`, seção 10, neste
repositório), mas o happy path com Gemini como prioridade 1 respondendo
de fato **não foi reproduzido em DEV** — 20 chamadas reais (10 por
perfil) ao longo de ~47 minutos caíram consistentemente no fallback
(`groq` para ADMIN/DEV, `oc` para USER), por indisponibilidade/quota da
API Gemini gratuita usada nesta bateria de testes, não por defeito de
configuração (as duas connections seguem `valid: true` no teste
isolado). Isso fica registrado como **validação operacional pendente,
não como blocker técnico** desta implementação.

**Antes de ativar esta policy em PROD**, depois que a quota/disponibilidade
do Gemini se normalizar, executar uma prova curta e real (mesmo padrão
usado em DEV, sem desativar nenhuma connection):

1. `POST /v1/ai/generate` com `ai_profile=user`, todas as connections
   ativas → esperado `AIResponse.provider == "gemini"`.
2. `POST /v1/ai/generate` com `ai_profile=admin_dev`, todas as connections
   ativas → esperado `AIResponse.provider == "gemini"`.

Se **qualquer uma** das duas falhar novamente **fora de uma condição de
quota/indisponibilidade conhecida e documentada no momento do teste**,
tratar como **blocker operacional** e não prosseguir com a ativação em
PROD sem investigar — não é mais o cenário aceito nesta rodada. Não
altere fila, prioridade, combo, fallback ou arquitetura para tentar
forçar sucesso.
