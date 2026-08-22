<#
Executa todas as verificações locais obrigatórias do projeto.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$temporarySecretsDirectory = $null
$pipelineTemporaryDirectory = Join-Path $projectRoot ".pipeline-tmp"
$originalSecretsDirectory = [Environment]::GetEnvironmentVariable(
    "AISHOPPING_SECRETS_DIR", "Process"
)

function Resolve-DockerCommand {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $userInstall = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $userInstall) {
        return $userInstall
    }
    throw "Docker CLI não encontrado no PATH nem na instalação por usuário documentada."
}

function Invoke-Check {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [scriptblock]$Command
    )

    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "A verificação '$Name' falhou com código $LASTEXITCODE."
    }
}

try {
    Set-Location -LiteralPath $projectRoot

    $resolvedPipelineTemporary = [System.IO.Path]::GetFullPath(
        $pipelineTemporaryDirectory
    )
    $resolvedProjectRoot = [System.IO.Path]::GetFullPath($projectRoot)
    if (-not $resolvedPipelineTemporary.StartsWith($resolvedProjectRoot)) {
        throw "Diretório temporário do pipeline fora do projeto."
    }
    if (Test-Path -LiteralPath $resolvedPipelineTemporary) {
        Remove-Item -LiteralPath $resolvedPipelineTemporary -Recurse -Force
    }
    New-Item -ItemType Directory -Path $resolvedPipelineTemporary | Out-Null

    Get-Command python -ErrorAction Stop | Out-Null
    $dockerCommand = Resolve-DockerCommand

    $temporarySecretsDirectory = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("aishopping-secrets-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temporarySecretsDirectory | Out-Null
    foreach ($name in @(
        "postgres_password",
        "gemini_api_key_user",
        "gemini_api_key_admin_dev",
        "groq_api_key",
        "telegram_bot_token",
        "telegram_webhook_secret"
        "ops_controller_secret"
    )) {
        [System.IO.File]::WriteAllText(
            (Join-Path $temporarySecretsDirectory $name),
            "pipeline-validation-$name"
        )
    }
    $env:AISHOPPING_SECRETS_DIR = $temporarySecretsDirectory

    Invoke-Check "Instalação verificada do Gitleaks" {
        python scripts/install_gitleaks.py
    }
    Invoke-Check "Varredura de segredos" {
        python scripts/scan_secrets.py
    }
    Invoke-Check "Integridade das dependências" {
        python -m pip check
    }
    Invoke-Check "Lint" {
        python -m ruff check .
    }
    Invoke-Check "Formatação" {
        python -m ruff format --check .
    }
    Invoke-Check "Testes e cobertura" {
        python -m pytest -p no:cacheprovider --ignore=tests/integration --ignore=tests/e2e --basetemp="$resolvedPipelineTemporary\pytest"
    }
    Invoke-Check "Grafo de migrações" {
        python -m alembic -c backend/alembic.ini heads
    }
    Invoke-Check "Configuração do Docker Compose" {
        & $dockerCommand compose -f compose.yaml config --quiet
    }
    Invoke-Check "Testes de integração PostgreSQL" {
        python scripts/run_integration_tests.py
    }

    Write-Host "Pipeline local aprovado."
}
finally {
    if (Test-Path -LiteralPath $pipelineTemporaryDirectory) {
        $resolvedPipelineTemporary = [System.IO.Path]::GetFullPath(
            $pipelineTemporaryDirectory
        )
        $resolvedProjectRoot = [System.IO.Path]::GetFullPath($projectRoot)
        if (-not $resolvedPipelineTemporary.StartsWith($resolvedProjectRoot)) {
            throw "Recusa em remover temporário fora do projeto."
        }
        Remove-Item -LiteralPath $resolvedPipelineTemporary -Recurse -Force
    }
    if ($null -ne $temporarySecretsDirectory -and (
        Test-Path -LiteralPath $temporarySecretsDirectory
    )) {
        $resolvedTemporary = [System.IO.Path]::GetFullPath($temporarySecretsDirectory)
        $resolvedTempRoot = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::GetTempPath()
        )
        if (-not $resolvedTemporary.StartsWith($resolvedTempRoot)) {
            throw "Recusa em remover diretório temporário fora da pasta temporária."
        }
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
    if ($null -eq $originalSecretsDirectory) {
        Remove-Item Env:AISHOPPING_SECRETS_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:AISHOPPING_SECRETS_DIR = $originalSecretsDirectory
    }
}
