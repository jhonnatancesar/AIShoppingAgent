# Provisiona/audita as connections `searxng-search` e `firecrawl` no
# OmniRoute (capabilities Search/Fetch do César Core --
# `CESAR_CORE_SEARCH_DEFAULT_PROVIDER`/`CESAR_CORE_FETCH_DEFAULT_PROVIDER`,
# `deploy/prod/cesar-core.compose.yaml`). Diferente das 4 connections de
# AI (`deploy/prod/omniroute-provisioning.md`, ainda manual, não mexidas
# aqui) e diferente do bootstrap de credenciais consumidoras
# (`bootstrap-omniroute-keys.ps1`, outro recurso do OmniRoute).
#
# Determinístico e idempotente: se as duas connections já existirem com a
# configuração esperada, o script só confirma isso. Se existirem com
# configuração incompatível, para com erro explícito -- nunca corrige
# sozinho. Nunca imprime valor de secret (nem a senha administrativa, nem
# a API key do Firecrawl).
#
# Uso (a partir da raiz do repositório GG Oferta, com o `omniroute` já no
# ar e healthy -- seção 5.1 do handoff):
#   .\deploy\prod\cesar-core\bootstrap-omniroute-search-fetch.ps1 `
#       -FirecrawlApiKey (Read-Host -AsSecureString "Firecrawl API key")
#
# Só é preciso fornecer -FirecrawlApiKey na PRIMEIRA execução (quando a
# connection `firecrawl` ainda não existe) -- reexecuções com a connection
# já provisionada não pedem nem usam o valor. Alternativa: grave o valor
# uma vez em `.secrets\firecrawl-api-key` (arquivo local, fora do Git) e
# rode sem o parâmetro -- o script lê de lá se o parâmetro não for dado.
#
# Encerra com código de saída não-zero se qualquer etapa falhar.

param(
    [securestring]$FirecrawlApiKey
)

$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$secretsDir = Join-Path $scriptDir ".secrets"
$adminEnvFile = Join-Path $secretsDir "omniroute-admin.env"
$firecrawlKeyFile = Join-Path $secretsDir "firecrawl-api-key"
$bootstrapJs = Join-Path $scriptDir "bootstrap-omniroute-search-fetch.js"
$containerImage = "diegosouzapw/omniroute@sha256:085c57adf499a8aaa9f35ccde95c0df9c11bd9ecd18d6c9edbf3b68b8079ba9d"
$networkName = "cesar-core_backend"
$omnirouteContainer = "cesar-core-omniroute-1"

if (-not (Test-Path $adminEnvFile)) {
    Write-Error "Arquivo de senha administrativa não encontrado em '$adminEnvFile'. Gere-o primeiro (seção 4 do handoff, 'Geração de secrets locais')."
    exit 1
}

$adminPassword = $null
foreach ($line in Get-Content -LiteralPath $adminEnvFile) {
    if ($line -match '^INITIAL_PASSWORD=(.*)$') {
        $adminPassword = $matches[1]
    }
}
if ([string]::IsNullOrEmpty($adminPassword)) {
    Write-Error "Linha 'INITIAL_PASSWORD=' não encontrada (ou vazia) em '$adminEnvFile'."
    exit 1
}

$firecrawlApiKeyPlain = $null
if ($FirecrawlApiKey) {
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($FirecrawlApiKey)
    try {
        $firecrawlApiKeyPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
elseif (Test-Path -LiteralPath $firecrawlKeyFile) {
    $firecrawlApiKeyPlain = (Get-Content -LiteralPath $firecrawlKeyFile -Raw).Trim()
}
# Se nenhum dos dois estiver disponível, segue sem -- só falha de verdade
# se a connection `firecrawl` precisar ser criada (script JS decide isso).

Write-Host "Aguardando o container '$omnirouteContainer' ficar healthy..."
$deadline = (Get-Date).AddSeconds(120)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    $status = (& docker inspect --format='{{.State.Health.Status}}' $omnirouteContainer 2>$null)
    if ($status -eq "healthy") { $healthy = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $healthy) {
    Write-Error "Container '$omnirouteContainer' não ficou healthy em 120s. Confirme 'docker compose -f deploy\prod\cesar-core.compose.yaml up -d omniroute' antes de rodar este script."
    exit 1
}
Write-Host "OK: '$omnirouteContainer' está healthy."

Write-Host "Provisionando/auditando connections Search (searxng-search) e Fetch (firecrawl)..."
$envArgs = @(
    "-e", "OMNIROUTE_ADMIN_PASSWORD=$adminPassword"
)
if ($firecrawlApiKeyPlain) {
    $envArgs += @("-e", "FIRECRAWL_API_KEY=$firecrawlApiKeyPlain")
}

& docker run --rm `
    --network $networkName `
    @envArgs `
    -v "${bootstrapJs}:/tmp/bootstrap.js:ro" `
    $containerImage `
    node /tmp/bootstrap.js

$exitCode = $LASTEXITCODE
$adminPassword = $null
$firecrawlApiKeyPlain = $null

if ($exitCode -ne 0) {
    Write-Error "Bootstrap de Search/Fetch falhou (exit $exitCode). Siga a mensagem BOOTSTRAP_FAILED acima antes de prosseguir."
    exit $exitCode
}

Write-Host "Connections searxng-search e firecrawl prontas. Prossiga com o gate Search/Fetch do handoff."
