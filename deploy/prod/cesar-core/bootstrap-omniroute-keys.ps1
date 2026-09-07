# Etapa A do procedimento de subida do bundle César Core (ver
# docs/operations/prod-deployment-handoff.md, seção 5.1): sobe só o
# serviço `omniroute`, aguarda ficar `healthy` e emite/grava as 3
# credenciais consumidoras (`ggoferta-ai`/`ggoferta-search`/
# `ggoferta-fetch`) exigidas pelos secrets de arquivo que o serviço
# `cesar-core` monta -- sem essas credenciais, o `cesar-core` não sobe
# (Docker monta secret de arquivo de forma fail-closed).
#
# Determinístico e idempotente: se os 3 arquivos já existirem e não
# estiverem vazios, o script não faz nada além de confirmar isso. Nunca
# imprime valor de credencial -- só nomes de arquivo e status.
#
# Uso (a partir da raiz do repositório GG Oferta, depois de
# `docker compose -f deploy\prod\cesar-core.compose.yaml up -d omniroute`):
#   .\deploy\prod\cesar-core\bootstrap-omniroute-keys.ps1
#
# Encerra com código de saída não-zero se qualquer etapa falhar. Não
# prossiga para subir o restante do stack (etapa B) se este script
# falhar -- a mensagem de erro já diz o que fazer, não decida por conta
# própria.

$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$composeFile = Join-Path $scriptDir "..\cesar-core.compose.yaml"
$secretsDir = Join-Path $scriptDir ".secrets"
$adminEnvFile = Join-Path $secretsDir "omniroute-admin.env"
$bootstrapJs = Join-Path $scriptDir "bootstrap-omniroute-keys.js"
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

Write-Host "Aguardando o container '$omnirouteContainer' ficar healthy..."
$deadline = (Get-Date).AddSeconds(120)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    $status = (& docker inspect --format='{{.State.Health.Status}}' $omnirouteContainer 2>$null)
    if ($status -eq "healthy") { $healthy = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $healthy) {
    Write-Error "Container '$omnirouteContainer' não ficou healthy em 120s. Confirme 'docker compose -f $composeFile up -d omniroute' antes de rodar este script."
    exit 1
}
Write-Host "OK: '$omnirouteContainer' está healthy."

New-Item -ItemType Directory -Force -Path $secretsDir | Out-Null

$pairs = "ggoferta-ai:/out/ggoferta-ai,ggoferta-search:/out/ggoferta-search,ggoferta-fetch:/out/ggoferta-fetch"

Write-Host "Emitindo/verificando credenciais consumidoras (ggoferta-ai / ggoferta-search / ggoferta-fetch)..."
& docker run --rm `
    --network $networkName `
    -e "OMNIROUTE_ADMIN_PASSWORD=$adminPassword" `
    -e "BOOTSTRAP_KEY_PAIRS=$pairs" `
    -v "${secretsDir}:/out" `
    -v "${bootstrapJs}:/tmp/bootstrap.js:ro" `
    $containerImage `
    node /tmp/bootstrap.js

$exitCode = $LASTEXITCODE
$adminPassword = $null

if ($exitCode -ne 0) {
    Write-Error "Bootstrap de credenciais falhou (exit $exitCode). NÃO prossiga para subir o restante do stack até resolver -- siga a mensagem BOOTSTRAP_FAILED acima."
    exit $exitCode
}

Write-Host "Credenciais prontas em '$secretsDir'. Prossiga com a etapa B (seção 5.1 do handoff): docker compose -f $composeFile up -d"
