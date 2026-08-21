<#
Registra a Scheduled Task do Windows que roda scripts\funnel_healthcheck.ps1
a cada 5 minutos -- equivalente ao antigo timer systemd
telegram-funnel-healthcheck.timer (ver docs/PRODUCTION_SETUP.md).

Precisa rodar como Administrador (cria a tarefa na sessão SYSTEM, para
funcionar mesmo sem sessão RDP interativa aberta).

Uso:
    powershell -File scripts\register_funnel_healthcheck_task.ps1
#>

[CmdletBinding()]
param(
    [string]$TaskName = "AIShoppingAgent-FunnelHealthcheck",
    [int]$IntervalMinutes = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\funnel_healthcheck.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Não encontrado: $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description ("Verifica a cada $IntervalMinutes min se o Tailscale Funnel do " +
        "webhook do Telegram está de fato acessível publicamente (não só " +
        "localmente) e reinicia tailscaled/reaplica o Funnel sozinho após 2 " +
        "falhas seguidas, no máximo 1 restart a cada 10 min. Porta Windows do " +
        "antigo timer systemd telegram-funnel-healthcheck.") `
    -Force | Out-Null

Write-Host "Tarefa '$TaskName' registrada, rodando a cada $IntervalMinutes min."
Get-ScheduledTask -TaskName $TaskName | Format-List TaskName, State
