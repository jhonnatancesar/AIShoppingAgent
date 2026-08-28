<#
Provisiona/audita a configuração de runtime do worker de coleta nativo
(TASK-109, `python -m app.collection.worker`) em nível de Máquina do
Windows -- companheiro de `manage_collection_worker_task.ps1` (que cuida
só da Scheduled Task).

FONTE ÚNICA DOS SECRETS (DEC-104, ver docs/architecture/
windows-collection-worker.md): `C:\App\AIShoppingAgent\.secrets\`, os
MESMOS arquivos já usados pelos containers Docker via `*_FILE`. Este
script NUNCA grava, lê o conteúdo ou imprime nenhum segredo -- só
referencia caminho absoluto de arquivo já existente, através das
variáveis `AISHOPPING_*_FILE`, mesmo mecanismo que `app/core/config.py`
já usa (`_SECRET_FILE_FIELDS`). Nenhum secret é aceito como parâmetro
deste script, nunca vai para `backend\.env`, nunca vai para o Git.

Variáveis de MÁQUINA (Environment "Machine", registry
HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment) --
comprovado ao vivo nesta PROD (2026-08-28) que a Scheduled Task
(LogonType Interactive) lê essas variáveis FRESCAS a cada disparo, sem
precisar de logoff/reboot/restart de serviço: configuramos as variáveis
e, na mesma sessão já logada, um `Start-ScheduledTask` imediato (via
Windows Ops Agent) já iniciou o worker com a configuração nova
carregada, conectou no Postgres e no Gemini ADMIN/DEV, e coletou de
verdade. Por isso este script NÃO cria nenhum launcher/wrapper
intermediário -- provou-se desnecessário.

Settings realmente consumidos pelo collection worker (auditado em
`app/collection/worker.py`, `app/ai_provider/manager.py`
`build_admin_dev_ai_provider_manager`, `app/collection/shared_collection.py`,
`app/market_research/`, v1.2.1/v1.2.2 -- código idêntico entre as duas
tags, só compose.yaml mudou):

  OBRIGATÓRIOS (sem default, worker crasha no startup sem eles):
    - database_password       (secreto)  -> AISHOPPING_DATABASE_PASSWORD_FILE
    - gemini_api_key_admin_dev(secreto)  -> AISHOPPING_GEMINI_API_KEY_ADMIN_DEV_FILE

  OPCIONAIS, com efeito real se ausentes (fail-soft, nunca crasha):
    - groq_api_key       (secreto)  -> AISHOPPING_GROQ_API_KEY_FILE
        ausente: cascata ADMIN/DEV perde o 2o nível de fallback.
    - openrouter_api_key (secreto)  -> AISHOPPING_OPENROUTER_API_KEY_FILE
        ausente: cascata ADMIN/DEV perde o 3o nível (último) de fallback.
    - firecrawl_api_key  (secreto)  -> AISHOPPING_FIRECRAWL_API_KEY_FILE
        ausente: TASK-113 (avaliação de mercado) fica desligada, resto
        do worker funciona normalmente.
    - edge_cdp_url    (não secreto) -> AISHOPPING_EDGE_CDP_URL
        ausente: Magalu/MercadoLivre/Terabyte falham isolados (não têm
        fallback Playwright, TASK-105/109); Amazon/Kabum/Pichau caem
        para Playwright puro.

  NÃO USADO pelo worker (confirmado por grep no código -- só usado pelo
  caminho de requisição do usuário final em app/ai_provider/manager.py,
  função diferente de build_admin_dev_ai_provider_manager):
    - gemini_api_key_user -- este script NUNCA configura essa variável.

  NÃO USADOS pelo worker (usados só por outros serviços -- ops_controller,
  telegram_notifier, api -- este script nunca configura):
    - telegram_bot_token, telegram_webhook_secret, ops_controller_secret,
      windows_ops_agent_secret.

  Com default já correto para este ambiente (não sobrepostos por este
  script, evita duplicação desnecessária -- DEC-104):
    - database_name = "aishoppingagent", database_user = "aishoppingagent"
      (batem com POSTGRES_DB/POSTGRES_USER do compose.yaml).
    - todo o resto (poll/batch/retry/circuit/cadence/fan-out/...) --
      tuning com default documentado em docs/installation/configuration.md,
      adequado a produção, nunca sobreposto aqui.

  Explicitamente configurados por este script mesmo tendo default
  (determinismo pedido -- nunca depender implicitamente de "localhost"):
    - database_host -> AISHOPPING_DATABASE_HOST (default do Settings é
      "localhost"; produção exige "127.0.0.1" explícito).
    - database_port -> AISHOPPING_DATABASE_PORT (default já bate com
      produção, mas fica explícito por determinismo).

Uso:
    powershell -File scripts\manage_collection_worker_config.ps1 -Action Status

    powershell -File scripts\manage_collection_worker_config.ps1 -Action Install

    # Ver o que seria configurado, sem gravar nada de verdade:
    powershell -File scripts\manage_collection_worker_config.ps1 -Action Install -WhatIf

    powershell -File scripts\manage_collection_worker_config.ps1 -Action Remove
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Update", "Status", "Remove")]
    [string]$Action,

    [string]$ProjectRoot = "C:\App\AIShoppingAgent",
    [string]$SecretsDir = "C:\App\AIShoppingAgent\.secrets",

    # Não secretos -- defaults já corretos para esta PROD (host Postgres
    # publicado em 127.0.0.1, porta 5432 default do compose.yaml; Edge
    # CDP na porta já validada ao vivo nesta máquina, DEC-103/DEC-104).
    [string]$DatabaseHost = "127.0.0.1",
    [int]$DatabasePort = 5432,
    [string]$EdgeCdpUrl = "http://127.0.0.1:9223"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
}
catch {
    # Hosts sem console real não suportam trocar OutputEncoding -- só
    # afeta exibição de acentos, não é fatal.
}

function Assert-Administrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    $isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        throw "Este script precisa rodar como Administrador (grava variável de Máquina)."
    }
}

# Nome da variável AISHOPPING_*_FILE -> nome do arquivo dentro de
# $SecretsDir. "Required=$true" interrompe Install/Update se o arquivo
# não existir (nunca inventa valor); "Required=$false" apenas deixa a
# variável de fora (comportamento fail-soft já suportado pelo código).
$script:SecretFileMap = [ordered]@{
    "AISHOPPING_DATABASE_PASSWORD_FILE"        = @{ File = "postgres_password"; Required = $true }
    "AISHOPPING_GEMINI_API_KEY_ADMIN_DEV_FILE" = @{ File = "gemini_api_key_admin_dev"; Required = $true }
    "AISHOPPING_GROQ_API_KEY_FILE"              = @{ File = "groq_api_key"; Required = $false }
    "AISHOPPING_OPENROUTER_API_KEY_FILE"        = @{ File = "openrouter_api_key"; Required = $false }
    "AISHOPPING_FIRECRAWL_API_KEY_FILE"         = @{ File = "firecrawl_api_key"; Required = $false }
}

# Identidades esperadas na ACL de $SecretsDir (DEC-104) -- qualquer outra
# identidade com acesso (BUILTIN\Users, Authenticated Users, Everyone,
# etc.) reprova o preflight.
$script:ExpectedAclIdentities = @(
    "NT AUTHORITY\SYSTEM",
    "BUILTIN\Administrators",
    "$env:COMPUTERNAME\Administrator"
)

function Get-NonSecretVars {
    param(
        [Parameter(Mandatory = $true)][string]$DatabaseHost,
        [Parameter(Mandatory = $true)][int]$DatabasePort,
        [Parameter(Mandatory = $true)][string]$EdgeCdpUrl
    )
    return [ordered]@{
        "AISHOPPING_DATABASE_HOST" = $DatabaseHost
        "AISHOPPING_DATABASE_PORT" = [string]$DatabasePort
        "AISHOPPING_EDGE_CDP_URL"  = $EdgeCdpUrl
    }
}

function Test-SecretsAcl {
    param([Parameter(Mandatory = $true)][string]$SecretsDir)

    if (-not (Test-Path -LiteralPath $SecretsDir)) {
        return @{ Ok = $false; Detail = "diretório não existe" }
    }
    $acl = Get-Acl -LiteralPath $SecretsDir
    $identities = $acl.Access | ForEach-Object { $_.IdentityReference.Value }
    $unexpected = $identities | Where-Object { $_ -notin $script:ExpectedAclIdentities } | Select-Object -Unique
    if ($unexpected) {
        return @{ Ok = $false; Detail = "identidades inesperadas com acesso: $($unexpected -join ', ')" }
    }
    $missing = $script:ExpectedAclIdentities | Where-Object { $_ -notin $identities }
    if ($missing) {
        return @{ Ok = $false; Detail = "identidades esperadas sem acesso: $($missing -join ', ')" }
    }
    return @{ Ok = $true; Detail = "somente $($script:ExpectedAclIdentities -join ', ')" }
}

function Assert-Preflight {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$SecretsDir
    )

    if (-not (Test-Path -LiteralPath $ProjectRoot)) {
        throw "ProjectRoot não encontrado: $ProjectRoot"
    }
    if (-not (Test-Path -LiteralPath $SecretsDir)) {
        throw "SecretsDir não encontrado: $SecretsDir"
    }
    if (-not [System.IO.Path]::IsPathRooted($SecretsDir)) {
        throw "SecretsDir precisa ser um caminho absoluto: $SecretsDir"
    }

    # Nunca dentro do Git: SecretsDir precisa estar coberto por
    # .gitignore (ou fora da árvore do repositório) -- nunca aceitar
    # silenciosamente um diretório de segredos rastreado.
    $gitignorePath = Join-Path $ProjectRoot ".gitignore"
    if (Test-Path -LiteralPath $gitignorePath) {
        $secretsLeafName = Split-Path -Leaf $SecretsDir
        $ignored = Select-String -LiteralPath $gitignorePath -Pattern ([regex]::Escape($secretsLeafName)) -Quiet
        if (-not $ignored -and $SecretsDir.StartsWith($ProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "$SecretsDir está dentro do repositório mas não aparece no .gitignore -- corrija antes de continuar (nunca versionar secrets)."
        }
    }

    $aclResult = Test-SecretsAcl -SecretsDir $SecretsDir
    if (-not $aclResult.Ok) {
        throw "ACL de '$SecretsDir' inadequada: $($aclResult.Detail)"
    }

    foreach ($varName in $script:SecretFileMap.Keys) {
        $entry = $script:SecretFileMap[$varName]
        if (-not $entry.Required) { continue }
        $filePath = Join-Path $SecretsDir $entry.File
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "Secret obrigatório ausente: '$($entry.File)' não encontrado em $SecretsDir (esperado para $varName). Não será gerado valor artificial -- provisionar o arquivo antes de continuar."
        }
    }
}

function Install-OrUpdate-WorkerConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$SecretsDir,
        [Parameter(Mandatory = $true)][string]$DatabaseHost,
        [Parameter(Mandatory = $true)][int]$DatabasePort,
        [Parameter(Mandatory = $true)][string]$EdgeCdpUrl
    )

    Assert-Preflight -ProjectRoot $ProjectRoot -SecretsDir $SecretsDir

    $nonSecret = Get-NonSecretVars -DatabaseHost $DatabaseHost -DatabasePort $DatabasePort -EdgeCdpUrl $EdgeCdpUrl
    foreach ($name in $nonSecret.Keys) {
        $value = $nonSecret[$name]
        if ($PSCmdlet.ShouldProcess("Variável de Máquina $name", "definir")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Machine")
        }
    }

    foreach ($varName in $script:SecretFileMap.Keys) {
        $entry = $script:SecretFileMap[$varName]
        $filePath = Join-Path $SecretsDir $entry.File
        $exists = Test-Path -LiteralPath $filePath -PathType Leaf
        if ($exists) {
            if ($PSCmdlet.ShouldProcess("Variável de Máquina $varName", "apontar para $filePath")) {
                [Environment]::SetEnvironmentVariable($varName, $filePath, "Machine")
            }
        }
        else {
            # Opcional e ausente -- garante que não fica um _FILE
            # apontando para um arquivo que não existe (isso faria
            # Settings() lançar exceção em vez de tratar como ausente).
            if ($PSCmdlet.ShouldProcess("Variável de Máquina $varName", "remover (arquivo opcional ausente)")) {
                [Environment]::SetEnvironmentVariable($varName, $null, "Machine")
            }
        }
    }

    Write-Host "Configuração de Máquina do worker aplicada."
    Get-WorkerConfigStatus -SecretsDir $SecretsDir -DatabaseHost $DatabaseHost -DatabasePort $DatabasePort -EdgeCdpUrl $EdgeCdpUrl
}

function Get-WorkerConfigStatus {
    param(
        [Parameter(Mandatory = $true)][string]$SecretsDir,
        [string]$DatabaseHost,
        [int]$DatabasePort,
        [string]$EdgeCdpUrl
    )

    Write-Host ""
    Write-Host "--- configuração não secreta ---"
    $nonSecretNames = @("AISHOPPING_DATABASE_HOST", "AISHOPPING_DATABASE_PORT", "AISHOPPING_EDGE_CDP_URL")
    foreach ($name in $nonSecretNames) {
        $current = [Environment]::GetEnvironmentVariable($name, "Machine")
        $state = if ($current) { "configurada ($current)" } else { "não configurada" }
        Write-Host "$name = $state"
    }

    Write-Host ""
    Write-Host "--- secrets (somente referência de arquivo, nunca conteúdo) ---"
    foreach ($varName in $script:SecretFileMap.Keys) {
        $entry = $script:SecretFileMap[$varName]
        $current = [Environment]::GetEnvironmentVariable($varName, "Machine")
        $varState = if ($current) { "configurada" } else { "não configurada" }
        $filePath = Join-Path $SecretsDir $entry.File
        $fileState = if (Test-Path -LiteralPath $filePath -PathType Leaf) { "encontrado" } else { "não encontrado" }
        $requiredNote = if ($entry.Required) { "obrigatório" } else { "opcional" }
        Write-Host "$varName = $varState | FILE ($requiredNote) = $fileState"
    }

    Write-Host ""
    Write-Host "--- ACL de $SecretsDir ---"
    $aclResult = Test-SecretsAcl -SecretsDir $SecretsDir
    $aclState = if ($aclResult.Ok) { "OK" } else { "FAIL" }
    Write-Host "ACL = $aclState ($($aclResult.Detail))"
}

function Remove-WorkerConfig {
    $nonSecretNames = @("AISHOPPING_DATABASE_HOST", "AISHOPPING_DATABASE_PORT", "AISHOPPING_EDGE_CDP_URL")
    foreach ($name in $nonSecretNames + $script:SecretFileMap.Keys) {
        if ($PSCmdlet.ShouldProcess("Variável de Máquina $name", "remover")) {
            [Environment]::SetEnvironmentVariable($name, $null, "Machine")
        }
    }
    Write-Host "Configuração de Máquina do worker removida (variáveis apagadas; arquivos em .secrets\ intocados)."
}

Assert-Administrator

switch ($Action) {
    "Install" {
        Install-OrUpdate-WorkerConfig -ProjectRoot $ProjectRoot -SecretsDir $SecretsDir `
            -DatabaseHost $DatabaseHost -DatabasePort $DatabasePort -EdgeCdpUrl $EdgeCdpUrl
    }
    "Update" {
        Install-OrUpdate-WorkerConfig -ProjectRoot $ProjectRoot -SecretsDir $SecretsDir `
            -DatabaseHost $DatabaseHost -DatabasePort $DatabasePort -EdgeCdpUrl $EdgeCdpUrl
    }
    "Status" {
        Get-WorkerConfigStatus -SecretsDir $SecretsDir -DatabaseHost $DatabaseHost `
            -DatabasePort $DatabasePort -EdgeCdpUrl $EdgeCdpUrl
    }
    "Remove" {
        Remove-WorkerConfig
    }
}
