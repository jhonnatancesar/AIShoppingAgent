<#
Instala, atualiza, consulta, habilita/desabilita e remove a Scheduled Task
nativa do Windows que roda o collection_worker da V1.2 (TASK-109) --
processo Python controlando um Microsoft Edge real via CDP loopback.

REQUISITO DE SESSÃO (não negociável, ver docs/architecture/
windows-collection-worker.md e docs/tasks/TASK-109.md, FASE 1):
o worker PRECISA rodar numa sessão Windows interativa real (auto-logon
mantendo a sessão sempre logada; a tela pode ficar BLOQUEADA -- bloquear
não é o mesmo que deslogar, e isso foi comprovado ao vivo). Sessão
DESLOGADA nunca foi validada neste projeto e não é suportada por este
script -- por isso o Principal é criado com -LogonType Interactive,
amarrado ao usuário informado em -UserId, nunca -LogonType
ServiceAccount/S4U/Password (que rodariam sem estação de janela
interativa e provavelmente quebrariam o Edge/CDP).

Reinício automático em queda do processo é responsabilidade do Windows
Ops Agent (ops_agent/collection_worker_ops_agent.py), não desta tarefa
-- TASK-109 FASE 1 comprovou ao vivo que RestartCount/RestartInterval
nativos do Task Scheduler não reiniciam um processo morto externamente
(Stop-Process -Force, 90s de espera, LastTaskResult=4294967295).

Idempotente: Install/Update sempre chamam Register-ScheduledTask -Force,
seguro rodar quantas vezes for preciso sem duplicar a tarefa.

Nunca recebe nem grava senha/secret -- -LogonType Interactive com
gatilho -AtLogOn para o mesmo -UserId não exige senha armazenada.

Uso:
    # Instalar (ou reinstalar) já desabilitada -- preparo pré-deploy,
    # não dispara nada até -Action Enable:
    powershell -File scripts\manage_collection_worker_task.ps1 -Action Install `
        -PythonPath "C:\...\python.exe" -ProjectRoot "C:\App\AIShoppingAgent" `
        -UserId ".\aishoppingagent-worker" -StartDisabled

    # Ver o que seria configurado, sem registrar nada de verdade:
    powershell -File scripts\manage_collection_worker_task.ps1 -Action Install `
        -PythonPath "C:\...\python.exe" -ProjectRoot "C:\App\AIShoppingAgent" `
        -UserId ".\aishoppingagent-worker" -WhatIf

    powershell -File scripts\manage_collection_worker_task.ps1 -Action Status
    powershell -File scripts\manage_collection_worker_task.ps1 -Action Enable
    powershell -File scripts\manage_collection_worker_task.ps1 -Action Disable
    powershell -File scripts\manage_collection_worker_task.ps1 -Action Remove
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Update", "Status", "Enable", "Disable", "Remove")]
    [string]$Action,

    [string]$TaskName = "AIShoppingAgent-CollectionWorker",

    # Obrigatórios só para Install/Update -- nunca hardcoded, sempre
    # informados pelo operador na hora (ou por um script de deploy que
    # conhece o ambiente daquela máquina).
    [string]$PythonPath,
    [string]$ProjectRoot,
    [string]$UserId,

    # Cria/atualiza a tarefa já desabilitada -- uso típico: preparar a
    # infraestrutura antes da janela de manutenção sem disparar coleta
    # real de produção (ver docs/architecture/windows-collection-worker.md,
    # seção "PODE ser preparado ANTES do downtime").
    [switch]$StartDisabled
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Garante acentuação PT-BR correta na saída independente de como o
# script é invocado (console local, sessão remota, chamado por outro
# script) -- sem isso, mensagens com acento podem sair ilegíveis em
# alguns hosts de console Windows.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
}
catch {
    # Hosts sem console real (ex.: alguns runners de CI) não suportam
    # trocar OutputEncoding -- não é fatal, só a exibição de acentos.
}

function Assert-Administrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    $isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        throw "Este script precisa rodar como Administrador (cria/altera Scheduled Task)."
    }
}

function Install-OrUpdate-WorkerTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$UserId,
        [bool]$Disabled
    )

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        throw "Python não encontrado em: $PythonPath"
    }
    $backendDir = Join-Path $ProjectRoot "backend"
    if (-not (Test-Path -LiteralPath $backendDir)) {
        throw "Diretório backend/ não encontrado em: $ProjectRoot (esperado: $backendDir)"
    }

    $action = New-ScheduledTaskAction `
        -Execute $PythonPath `
        -Argument "-m app.collection.worker" `
        -WorkingDirectory $backendDir

    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId

    # LogonType Interactive: precisa da estação de janela interativa real
    # do usuário -- nunca ServiceAccount/S4U/Password, que rodam sem
    # sessão interativa e quebrariam o Edge/CDP (ver bloco de comentário
    # no topo do arquivo). RunLevel Limited: o worker não precisa de
    # privilégio elevado para nada documentado (Postgres via rede,
    # Edge via CDP loopback, sem instalação/registro do sistema).
    $principal = New-ScheduledTaskPrincipal `
        -UserId $UserId `
        -LogonType Interactive `
        -RunLevel Limited

    # ExecutionTimeLimit zero: o worker é um processo de longa duração
    # (loop contínuo), nunca deve ser encerrado pelo próprio Task
    # Scheduler por "tempo demais rodando" (o default do cmdlet, se
    # omitido, é limitar a alguns dias). RestartCount 0 de propósito --
    # ver bloco de comentário no topo. MultipleInstances IgnoreNew evita
    # dois processos de coleta concorrentes se o gatilho disparar de novo
    # com uma instância já rodando.
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 0

    if ($Disabled) {
        $settings.Enabled = $false
    }

    $description = "Worker de coleta nativo da V1.2 (TASK-109): Python " +
        "controlando Microsoft Edge real via CDP loopback. Exige sessao " +
        "Windows interativa permanentemente logada (auto-logon); tela " +
        "bloqueada e suportado e ja validado ao vivo, sessao deslogada " +
        "nunca foi validada e nao e suportada. Reinicio automatico em " +
        "queda e responsabilidade do Windows Ops Agent " +
        "(ops_agent/collection_worker_ops_agent.py), nao desta tarefa."

    $target = "Scheduled Task '$TaskName' (usuario: $UserId, python: $PythonPath, workdir: $backendDir)"
    if ($PSCmdlet.ShouldProcess($target, "Register-ScheduledTask")) {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description $description `
            -Force | Out-Null
        $stateNote = if ($Disabled) { "desabilitada (não dispara no próximo logon)" } else { "habilitada" }
        Write-Host "Tarefa '$TaskName' registrada/atualizada, $stateNote."
    }
}

function Get-WorkerTaskStatus {
    param([Parameter(Mandatory = $true)][string]$TaskName)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Tarefa '$TaskName' não existe."
        return
    }
    $task | Format-List TaskName, State
    $task | Get-ScheduledTaskInfo | Format-List LastRunTime, LastTaskResult, NextRunTime
}

function Set-WorkerTaskEnabled {
    param([Parameter(Mandatory = $true)][string]$TaskName, [Parameter(Mandatory = $true)][bool]$Enabled)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Tarefa '$TaskName' não existe -- rode -Action Install primeiro."
    }
    $verb = if ($Enabled) { "Enable-ScheduledTask" } else { "Disable-ScheduledTask" }
    if ($PSCmdlet.ShouldProcess("Scheduled Task '$TaskName'", $verb)) {
        if ($Enabled) {
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
            Write-Host "Tarefa '$TaskName' habilitada."
        }
        else {
            Disable-ScheduledTask -TaskName $TaskName | Out-Null
            Write-Host "Tarefa '$TaskName' desabilitada (não dispara no próximo logon)."
        }
    }
}

function Remove-WorkerTask {
    param([Parameter(Mandatory = $true)][string]$TaskName)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Tarefa '$TaskName' já não existe."
        return
    }
    if ($PSCmdlet.ShouldProcess("Scheduled Task '$TaskName'", "Unregister-ScheduledTask")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Tarefa '$TaskName' removida."
    }
}

Assert-Administrator

switch ($Action) {
    "Install" {
        foreach ($required in @("PythonPath", "ProjectRoot", "UserId")) {
            if (-not $PSBoundParameters.ContainsKey($required)) {
                throw "-$required é obrigatório para -Action Install."
            }
        }
        Install-OrUpdate-WorkerTask -TaskName $TaskName -PythonPath $PythonPath `
            -ProjectRoot $ProjectRoot -UserId $UserId -Disabled:$StartDisabled.IsPresent
    }
    "Update" {
        foreach ($required in @("PythonPath", "ProjectRoot", "UserId")) {
            if (-not $PSBoundParameters.ContainsKey($required)) {
                throw "-$required é obrigatório para -Action Update."
            }
        }
        Install-OrUpdate-WorkerTask -TaskName $TaskName -PythonPath $PythonPath `
            -ProjectRoot $ProjectRoot -UserId $UserId -Disabled:$StartDisabled.IsPresent
    }
    "Status" { Get-WorkerTaskStatus -TaskName $TaskName }
    "Enable" { Set-WorkerTaskEnabled -TaskName $TaskName -Enabled $true }
    "Disable" { Set-WorkerTaskEnabled -TaskName $TaskName -Enabled $false }
    "Remove" { Remove-WorkerTask -TaskName $TaskName }
}
