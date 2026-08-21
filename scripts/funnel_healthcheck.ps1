<#
Porta para Windows do antigo timer systemd
telegram-funnel-healthcheck (documentado em docs/PRODUCTION_SETUP.md,
"Auto-recuperação do Tailscale Funnel", criado em 2026-08-12 quando o
servidor ainda rodava Linux; perdido na migração para Windows Server e
recriado em 2026-08-21 depois de um incidente real onde a ausência dele
foi notada -- o webhook do Telegram ficou inacessível por horas sem
nenhuma recuperação automática).

Detecta a MESMA falha do incidente original: depois de um reboot ou de
qualquer reinício do serviço Tailscale/Docker, o Funnel pode reportar
"on" localmente (`tailscale funnel status`) sem estar de fato acessível
de fora -- só é detectável testando resolução DNS pública real (não o
hostname direto, que usa o atalho do MagicDNS do Tailscale e mascara o
problema) seguida de uma conexão HTTPS real ao IP resolvido.

Uso (a cada execução, idempotente):
    powershell -File scripts\funnel_healthcheck.ps1

Pensado para rodar via Scheduled Task a cada 5 minutos -- ver
scripts\register_funnel_healthcheck_task.ps1.
#>

[CmdletBinding()]
param(
    [string]$Hostname = "cesar-server.tail7d0ce1.ts.net",
    [int]$Port = 8000,
    [string]$PublicDnsServer = "8.8.8.8",
    [int]$FailureThreshold = 2,
    [int]$MinRestartIntervalMinutes = 10,
    [string]$StateFile = "",
    [string]$LogFile = "",
    [string]$TailscaleExe = "C:\Program Files\Tailscale\tailscale.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $StateFile) {
    $StateFile = Join-Path $scriptRoot "..\logs\funnel-healthcheck-state.json"
}
if (-not $LogFile) {
    $LogFile = Join-Path $scriptRoot "..\logs\funnel-healthcheck.log"
}

function Write-HealthcheckLog {
    param([Parameter(Mandatory)][string]$Message)
    $line = "[{0:yyyy-MM-ddTHH:mm:ssZ}] {1}" -f (Get-Date).ToUniversalTime(), $Message
    Write-Host $line
    $logDir = Split-Path -Parent $LogFile
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
}

function Get-HealthcheckState {
    if (Test-Path -LiteralPath $StateFile) {
        try {
            return Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        } catch {
            Write-HealthcheckLog "Estado anterior ilegível ($_) -- recomeçando do zero."
        }
    }
    return [pscustomobject]@{
        ConsecutiveFailures = 0
        LastRestartUtc      = $null
    }
}

function Save-HealthcheckState {
    param([Parameter(Mandatory)]$State)
    $stateDir = Split-Path -Parent $StateFile
    if (-not (Test-Path -LiteralPath $stateDir)) {
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    }
    $State | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding utf8
}

function Test-FunnelReachablePublicly {
    <#
    Reproduz o teste original (dig @8.8.8.8 + curl --resolve): resolve o
    hostname por um servidor DNS público explícito, nunca o resolver
    local (que pode responder via o atalho de MagicDNS do Tailscale e
    mascarar exatamente o problema que este script existe para pegar),
    depois faz uma conexão HTTPS real ao IP resolvido.
    #>
    try {
        $answer = Resolve-DnsName -Name $Hostname -Server $PublicDnsServer -Type A -ErrorAction Stop
    } catch {
        Write-HealthcheckLog "Falha ao resolver $Hostname via $PublicDnsServer -- $_"
        return $false
    }
    $resolvedIp = ($answer | Where-Object { $_.Type -eq "A" } | Select-Object -First 1).IPAddress
    if (-not $resolvedIp) {
        Write-HealthcheckLog "Nenhum registro A público encontrado para $Hostname via $PublicDnsServer."
        return $false
    }
    try {
        $statusCode = & curl.exe --silent --show-error --max-time 10 `
            --resolve "${Hostname}:443:${resolvedIp}" `
            --output NUL --write-out "%{http_code}" `
            "https://$Hostname/health" 2>&1
    } catch {
        Write-HealthcheckLog "curl falhou testando https://$Hostname/health via $resolvedIp -- $_"
        return $false
    }
    if ($statusCode -ne "200") {
        Write-HealthcheckLog "https://$Hostname/health via $resolvedIp respondeu '$statusCode' (esperado 200)."
        return $false
    }
    return $true
}

function Restart-TailscaleFunnel {
    Write-HealthcheckLog "Reiniciando o serviço Tailscale e reaplicando o Funnel na porta $Port..."
    Restart-Service -Name Tailscale -Force
    Start-Sleep -Seconds 5
    & $TailscaleExe funnel reset | Out-Null
    & $TailscaleExe funnel --bg $Port | Out-Null
}

$state = Get-HealthcheckState

if (Test-FunnelReachablePublicly) {
    if ($state.ConsecutiveFailures -gt 0) {
        Write-HealthcheckLog "Funnel voltou a responder publicamente -- zerando contador de falhas ($($state.ConsecutiveFailures) -> 0)."
    } else {
        Write-HealthcheckLog "OK -- Funnel acessível publicamente."
    }
    $state.ConsecutiveFailures = 0
    Save-HealthcheckState $state
    exit 0
}

$state.ConsecutiveFailures += 1
Write-HealthcheckLog "Funnel inacessível publicamente (falha $($state.ConsecutiveFailures) consecutiva(s), limite $FailureThreshold)."

if ($state.ConsecutiveFailures -lt $FailureThreshold) {
    Save-HealthcheckState $state
    exit 0
}

$nowUtc = [DateTime]::UtcNow
if ($state.LastRestartUtc) {
    $lastRestartUtc = [DateTime]::Parse($state.LastRestartUtc).ToUniversalTime()
    $minutesSinceRestart = ($nowUtc - $lastRestartUtc).TotalMinutes
    if ($minutesSinceRestart -lt $MinRestartIntervalMinutes) {
        Write-HealthcheckLog (
            "Já reiniciado há {0:N1} min -- aguardando o intervalo mínimo de " +
            "$MinRestartIntervalMinutes min para não entrar em loop."
        ) -f $minutesSinceRestart
        Save-HealthcheckState $state
        exit 0
    }
}

Restart-TailscaleFunnel
$state.ConsecutiveFailures = 0
$state.LastRestartUtc = $nowUtc.ToString("o")
Save-HealthcheckState $state
