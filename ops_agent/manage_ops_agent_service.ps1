<#
Instala, atualiza, consulta, inicia/para e remove o Windows Service real
(pywin32) que supervisiona o collection_worker nativo (TASK-109) --
`ops_agent/collection_worker_ops_agent.py`, nome de serviço
`AIShoppingAgentOpsAgent`.

CAUSA RAIZ CORRIGIDA POR ESTE SCRIPT (achado de 2026-09-20, investigação
de por que o worker de coleta não retomava sozinho depois de reinícios de
PC/Docker/containers): `python collection_worker_ops_agent.py install`
(o procedimento até então documentado no docstring do próprio .py e em
docs/architecture/windows-collection-worker.md) NUNCA passava
`--startup auto`/`--startup delayed` -- confirmado lendo o código-fonte
real do pywin32 instalado neste projeto (`win32serviceutil.InstallService`:
`if startType is None: startType = win32service.SERVICE_DEMAND_START`, e
o próprio `usage()` do pywin32 documenta "default = manual"). Resultado
real: o serviço ficava registrado como início MANUAL -- depois de um
reinício de PC, ninguém o inicia de novo, então nada supervisiona o
collection_worker se ele cair ou não iniciar (o script
manage_collection_worker_task.ps1 já documenta que o reinício nativo do
Task Scheduler NÃO cobre esse caso -- essa cobertura é 100% deste
serviço). Este script força `Automatic (Delayed Start)` sempre, de forma
idempotente, e também automatiza a recuperação nativa do SCM (`sc
failure`) que antes era só um comando documentado no docstring do .py,
nunca executado por nenhum script.

Por que "Delayed Start" e não "Automatic" puro: o Ops Agent só supervisiona
a Scheduled Task (nunca abre o Edge diretamente) e não tem uma dependência
de serviço formal no Docker Desktop/Postgres -- "Delayed Start" evita
competir por I/O/CPU com o restante da sequência de boot do Windows sem
exigir login de ninguém (roda como LocalSystem, Session 0, sempre). Não
elimina sozinho a corrida com o Docker Desktop subindo (impossível
garantir por tipo de início) -- é o loop de retry do próprio worker/scanner
(collection_worker: backoff exponencial já existente no loop principal;
coupon worker: retry na abertura do store, ver worker.py) que absorve essa
janela, não o tipo de início do serviço.

Uso (como Administrador):
    # Instala (ou corrige o tipo de início de uma instalação já existente)
    # e configura a recuperação nativa do SCM -- idempotente, seguro rodar
    # de novo a qualquer momento, inclusive só para corrigir uma instalação
    # anterior feita com o comando antigo (sem -Action Install de novo):
    powershell -File ops_agent\manage_ops_agent_service.ps1 -Action Install `
        -PythonPath "C:\...\python.exe"

    # Só corrige o tipo de início + recuperação de uma instalação já
    # existente, sem reinstalar o serviço:
    powershell -File ops_agent\manage_ops_agent_service.ps1 -Action FixStartup

    powershell -File ops_agent\manage_ops_agent_service.ps1 -Action Status
    powershell -File ops_agent\manage_ops_agent_service.ps1 -Action Start
    powershell -File ops_agent\manage_ops_agent_service.ps1 -Action Stop
    powershell -File ops_agent\manage_ops_agent_service.ps1 -Action Remove
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Update", "FixStartup", "Status", "Start", "Stop", "Remove")]
    [string]$Action,

    [string]$ServiceName = "AIShoppingAgentOpsAgent",

    # Obrigatório só para Install/Update -- python.exe do ambiente que tem
    # pywin32 instalado (mesmo critério de manage_collection_worker_task.ps1:
    # nunca hardcoded, sempre informado pelo operador ou por um script de
    # deploy que conhece o ambiente daquela máquina).
    [string]$PythonPath,

    [string]$ScriptPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# $PSScriptRoot pode vir vazio dependendo do host/modo de invocação
# (confirmado neste ambiente) -- mesmo padrão já usado em
# manage_coupon_worker_task.ps1 ($ScriptDir via $MyInvocation), em vez de
# depender de $PSScriptRoot como valor default de parâmetro.
if (-not $ScriptPath) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ScriptPath = Join-Path $scriptDir "collection_worker_ops_agent.py"
}

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
        throw "Este script precisa rodar como Administrador (cria/altera Windows Service)."
    }
}

function Set-DelayedAutoStartAndRecovery {
    param([Parameter(Mandatory = $true)][string]$ServiceName)

    # `sc.exe config ... start= delayed-auto`: corrige o tipo de início,
    # idempotente (reaplicar o mesmo valor não tem efeito colateral). Nunca
    # via Set-Service -StartupType (não suporta "AutomaticDelayedStart" em
    # todo Windows Server suportado por este projeto até a validação
    # explícita do contrário -- sc.exe é o denominador comum).
    if ($PSCmdlet.ShouldProcess("Serviço '$ServiceName'", "sc.exe config start= delayed-auto")) {
        $configOutput = & sc.exe config $ServiceName start= delayed-auto 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao configurar início automático (atrasado) do serviço '$ServiceName': $configOutput"
        }
        Write-Host "Serviço '$ServiceName' configurado para Automático (Início Atrasado)."
    }

    # Recuperação nativa do SCM em caso de queda do PRÓPRIO Ops Agent
    # (nunca dependeu deste script antes -- só existia como comando
    # documentado no docstring de collection_worker_ops_agent.py, nunca
    # automatizado). reset=86400 (1 dia): a contagem de falhas volta a
    # zero depois de um dia sem quedas, evitando esgotar o teto de
    # tentativas por um evento antigo já superado.
    if ($PSCmdlet.ShouldProcess("Serviço '$ServiceName'", "sc.exe failure (restart/5s, 15s, 30s)")) {
        $failureOutput = & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/restart/30000 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao configurar recuperação nativa do serviço '$ServiceName': $failureOutput"
        }
        Write-Host "Recuperação nativa do SCM configurada para '$ServiceName' (restart em 5s/15s/30s, reset em 24h)."
    }
}

function Install-OrUpdate-OpsAgentService {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$PywinAction
    )

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        throw "Python não encontrado em: $PythonPath"
    }
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        throw "Script do Ops Agent não encontrado em: $ScriptPath"
    }

    $target = "Windows Service '$ServiceName' (python: $PythonPath, script: $ScriptPath)"
    if ($PSCmdlet.ShouldProcess($target, "$PythonPath $ScriptPath $PywinAction")) {
        # pywin32 (win32serviceutil.HandleCommandLine) lê --startup ANTES do
        # verbo install/update na linha de comando -- ver usage() do
        # próprio pywin32 ("Usage: ... [options] install|update|..."). Aqui
        # passamos --startup delayed também na instalação (defesa em
        # profundidade), mas o Set-DelayedAutoStartAndRecovery abaixo é
        # quem GARANTE o resultado, via sc.exe, independente de o pywin32
        # aceitar/aplicar corretamente esse argumento em toda versão.
        & $PythonPath $ScriptPath --startup delayed $PywinAction
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao executar '$PywinAction' do serviço '$ServiceName' (código $LASTEXITCODE)."
        }
    }

    Set-DelayedAutoStartAndRecovery -ServiceName $ServiceName
}

function Get-OpsAgentServiceStatus {
    param([Parameter(Mandatory = $true)][string]$ServiceName)

    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "Serviço '$ServiceName' não existe."
        return
    }
    $service | Select-Object Name, State, StartMode, StartName | Format-List
    Write-Host "Configuração de recuperação nativa (sc.exe qfailure):"
    & sc.exe qfailure $ServiceName
}

Assert-Administrator

switch ($Action) {
    "Install" {
        if (-not $PSBoundParameters.ContainsKey("PythonPath")) {
            throw "-PythonPath é obrigatório para -Action Install."
        }
        Install-OrUpdate-OpsAgentService -ServiceName $ServiceName -PythonPath $PythonPath `
            -ScriptPath $ScriptPath -PywinAction "install"
    }
    "Update" {
        if (-not $PSBoundParameters.ContainsKey("PythonPath")) {
            throw "-PythonPath é obrigatório para -Action Update."
        }
        Install-OrUpdate-OpsAgentService -ServiceName $ServiceName -PythonPath $PythonPath `
            -ScriptPath $ScriptPath -PywinAction "update"
    }
    "FixStartup" {
        $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if (-not $service) {
            throw "Serviço '$ServiceName' não existe -- rode -Action Install primeiro."
        }
        Set-DelayedAutoStartAndRecovery -ServiceName $ServiceName
    }
    "Status" { Get-OpsAgentServiceStatus -ServiceName $ServiceName }
    "Start" {
        if ($PSCmdlet.ShouldProcess("Serviço '$ServiceName'", "Start-Service")) {
            Start-Service -Name $ServiceName
            Write-Host "Serviço '$ServiceName' iniciado."
        }
    }
    "Stop" {
        if ($PSCmdlet.ShouldProcess("Serviço '$ServiceName'", "Stop-Service")) {
            Stop-Service -Name $ServiceName
            Write-Host "Serviço '$ServiceName' parado."
        }
    }
    "Remove" {
        $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if (-not $service) {
            Write-Host "Serviço '$ServiceName' já não existe."
        }
        elseif ($PSBoundParameters.ContainsKey("PythonPath")) {
            if ($PSCmdlet.ShouldProcess("Serviço '$ServiceName'", "$PythonPath $ScriptPath remove")) {
                & $PythonPath $ScriptPath remove
                if ($LASTEXITCODE -ne 0) {
                    throw "Falha ao remover o serviço '$ServiceName' (código $LASTEXITCODE)."
                }
                Write-Host "Serviço '$ServiceName' removido."
            }
        }
        else {
            throw "-PythonPath é obrigatório para -Action Remove (chama '<python> $ScriptPath remove')."
        }
    }
}
