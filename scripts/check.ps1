<#
Executa todas as verificações locais obrigatórias do projeto.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$temporaryDatabasePassword = $false
$originalDatabasePassword = [Environment]::GetEnvironmentVariable(
    "POSTGRES_PASSWORD",
    "Process"
)

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

    Get-Command python -ErrorAction Stop | Out-Null
    Get-Command docker -ErrorAction Stop | Out-Null

    if ([string]::IsNullOrWhiteSpace($originalDatabasePassword)) {
        $env:POSTGRES_PASSWORD = "local-pipeline-validation-only"
        $temporaryDatabasePassword = $true
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
        python -m pytest -p no:cacheprovider
    }
    Invoke-Check "Configuração do Docker Compose" {
        docker compose -f compose.yaml config --quiet
    }

    Write-Host "Pipeline local aprovado."
}
finally {
    if ($temporaryDatabasePassword) {
        Remove-Item Env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue
    }
    elseif ($null -ne $originalDatabasePassword) {
        $env:POSTGRES_PASSWORD = $originalDatabasePassword
    }
}
