[CmdletBinding()]
param(
    [ValidateSet('Status', 'PlanRollback', 'Rollback')]
    [string]$Mode = 'Status',

    [string]$ResourceGroupName = 'rg-civicops-ml-prod',

    [string]$ContainerAppName = 'ca-civicops-ml-prod',

    [string]$TargetRevision,

    [string]$ExpectedCurrentRevision,

    [string]$SubscriptionId,

    [switch]$ConfirmRollback
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-AzureJson {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $output = & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed: az $($Arguments -join ' ')"
    }

    $text = $output -join "`n"
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }
    return $text | ConvertFrom-Json
}

function Get-ScopeArguments {
    if ([string]::IsNullOrWhiteSpace($SubscriptionId)) {
        return @()
    }
    return @('--subscription', $SubscriptionId)
}

function Get-ContainerApp {
    $arguments = @(
        'containerapp', 'show',
        '--name', $ContainerAppName,
        '--resource-group', $ResourceGroupName,
        '--output', 'json'
    ) + (Get-ScopeArguments)
    return Invoke-AzureJson -Arguments $arguments
}

function Get-Revisions {
    $arguments = @(
        'containerapp', 'revision', 'list',
        '--name', $ContainerAppName,
        '--resource-group', $ResourceGroupName,
        '--output', 'json'
    ) + (Get-ScopeArguments)
    return @(Invoke-AzureJson -Arguments $arguments)
}

function Get-Traffic {
    param(
        [Parameter(Mandatory)]
        [object]$Application
    )

    return @($Application.properties.configuration.ingress.traffic)
}

function Get-CurrentRevisionName {
    param(
        [Parameter(Mandatory)]
        [object[]]$Traffic
    )

    $fullTraffic = @($Traffic | Where-Object { [int]$_.weight -eq 100 })
    if ($fullTraffic.Count -ne 1) {
        throw 'Rollback requires exactly one revision to hold 100 percent of production traffic.'
    }
    return [string]$fullTraffic[0].revisionName
}

function Get-Revision {
    param(
        [Parameter(Mandatory)]
        [object[]]$Revisions,

        [Parameter(Mandatory)]
        [string]$Name
    )

    $matches = @($Revisions | Where-Object { $_.name -eq $Name })
    if ($matches.Count -ne 1) {
        throw "Revision '$Name' was not found exactly once."
    }
    return $matches[0]
}

function Assert-RollbackCandidate {
    param(
        [Parameter(Mandatory)]
        [object]$Revision,

        [Parameter(Mandatory)]
        [string]$CurrentRevision
    )

    if ($Revision.name -eq $CurrentRevision) {
        throw 'The rollback target is already serving production traffic.'
    }
    if (-not [bool]$Revision.properties.active) {
        throw "Revision '$($Revision.name)' is not active."
    }
    if ($Revision.properties.healthState -ne 'Healthy') {
        throw "Revision '$($Revision.name)' is not healthy."
    }
    if ($Revision.properties.provisioningState -ne 'Provisioned') {
        throw "Revision '$($Revision.name)' is not fully provisioned."
    }
    $image = [string]$Revision.properties.template.containers[0].image
    if ($image -notmatch '@sha256:[0-9a-f]{64}$') {
        throw "Revision '$($Revision.name)' is not pinned to an immutable image digest."
    }
}

function Test-RevisionHealth {
    param(
        [Parameter(Mandatory)]
        [object]$Revision
    )

    $baseUrl = "https://$($Revision.properties.fqdn)"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $live = Invoke-WebRequest -Uri "$baseUrl/livez" -UseBasicParsing -TimeoutSec 90
    $liveMilliseconds = $timer.ElapsedMilliseconds
    $timer.Restart()
    $ready = Invoke-WebRequest -Uri "$baseUrl/healthz" -UseBasicParsing -TimeoutSec 30

    $readyBody = $ready.Content | ConvertFrom-Json
    if ($live.StatusCode -ne 200 -or $ready.StatusCode -ne 200) {
        throw "Revision '$($Revision.name)' did not return successful health responses."
    }
    if (-not [bool]$readyBody.model_loaded -or -not [bool]$readyBody.storage_ready) {
        throw "Revision '$($Revision.name)' is not ready for model or storage traffic."
    }

    return [ordered]@{
        livezStatus = $live.StatusCode
        livezMilliseconds = $liveMilliseconds
        healthzStatus = $ready.StatusCode
        modelLoaded = [bool]$readyBody.model_loaded
        storageReady = [bool]$readyBody.storage_ready
    }
}

function Set-ProductionRevision {
    param(
        [Parameter(Mandatory)]
        [string]$RevisionName
    )

    $arguments = @(
        'containerapp', 'ingress', 'traffic', 'set',
        '--name', $ContainerAppName,
        '--resource-group', $ResourceGroupName,
        '--revision-weight', "$RevisionName=100",
        '--output', 'none'
    ) + (Get-ScopeArguments)
    $null = Invoke-AzureJson -Arguments $arguments
}

function Test-PublicHealth {
    param(
        [Parameter(Mandatory)]
        [string]$Fqdn
    )

    $lastError = $null
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri "https://$Fqdn/healthz" -UseBasicParsing -TimeoutSec 30
            $body = $response.Content | ConvertFrom-Json
            if ($response.StatusCode -eq 200 -and $body.model_loaded -and $body.storage_ready) {
                return
            }
        }
        catch {
            $lastError = $_
        }
        Start-Sleep -Seconds 5
    }

    if ($null -ne $lastError) {
        throw "The public health check failed after the traffic change: $($lastError.Exception.Message)"
    }
    throw 'The public health check did not become ready after the traffic change.'
}

$accountArguments = @('account', 'show', '--output', 'json') + (Get-ScopeArguments)
$account = Invoke-AzureJson -Arguments $accountArguments
$application = Get-ContainerApp
$revisions = Get-Revisions
$traffic = Get-Traffic -Application $application
$currentRevision = Get-CurrentRevisionName -Traffic $traffic

if ($Mode -eq 'Status') {
    [ordered]@{
        mode = $Mode
        subscription = $account.name
        resourceGroup = $ResourceGroupName
        containerApp = $ContainerAppName
        runningStatus = $application.properties.runningStatus
        latestReadyRevision = $application.properties.latestReadyRevisionName
        currentRevision = $currentRevision
        revisions = @($revisions | ForEach-Object {
            [ordered]@{
                name = $_.name
                active = [bool]$_.properties.active
                healthState = $_.properties.healthState
                trafficWeight = [int]$_.properties.trafficWeight
                image = $_.properties.template.containers[0].image
            }
        })
    } | ConvertTo-Json -Depth 6
    exit 0
}

if ([string]::IsNullOrWhiteSpace($TargetRevision)) {
    throw "Mode '$Mode' requires -TargetRevision."
}

$target = Get-Revision -Revisions $revisions -Name $TargetRevision
Assert-RollbackCandidate -Revision $target -CurrentRevision $currentRevision

if ($Mode -eq 'Rollback') {
    if (-not $ConfirmRollback) {
        throw 'Rollback is locked. Re-run with -ConfirmRollback after reviewing PlanRollback output.'
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedCurrentRevision)) {
        throw 'Rollback requires -ExpectedCurrentRevision to prevent a stale traffic change.'
    }
    if ($ExpectedCurrentRevision -ne $currentRevision) {
        throw "Expected current revision '$ExpectedCurrentRevision' does not match '$currentRevision'."
    }
}

$targetHealth = Test-RevisionHealth -Revision $target

if ($Mode -eq 'PlanRollback') {
    $unchangedApplication = Get-ContainerApp
    $unchangedCurrent = Get-CurrentRevisionName -Traffic (Get-Traffic -Application $unchangedApplication)
    if ($unchangedCurrent -ne $currentRevision) {
        throw 'Production traffic changed while the rollback plan was being evaluated.'
    }

    [ordered]@{
        mode = $Mode
        ready = $true
        currentRevision = $currentRevision
        targetRevision = $target.name
        targetImage = $target.properties.template.containers[0].image
        targetHealth = $targetHealth
        productionTrafficChanged = $false
    } | ConvertTo-Json -Depth 6
    exit 0
}

$preChangeApplication = Get-ContainerApp
$preChangeCurrent = Get-CurrentRevisionName -Traffic (Get-Traffic -Application $preChangeApplication)
if ($preChangeCurrent -ne $currentRevision) {
    throw "Production traffic changed from '$currentRevision' to '$preChangeCurrent' during rollback validation."
}

Set-ProductionRevision -RevisionName $target.name
try {
    Test-PublicHealth -Fqdn $application.properties.configuration.ingress.fqdn
}
catch {
    Set-ProductionRevision -RevisionName $currentRevision
    throw "Rollback health verification failed and traffic was restored to '$currentRevision'. $($_.Exception.Message)"
}

$updatedApplication = Get-ContainerApp
$updatedCurrent = Get-CurrentRevisionName -Traffic (Get-Traffic -Application $updatedApplication)
if ($updatedCurrent -ne $target.name) {
    Set-ProductionRevision -RevisionName $currentRevision
    throw "Azure did not retain 100 percent traffic on '$($target.name)'; traffic was restored to '$currentRevision'."
}

[ordered]@{
    mode = $Mode
    succeeded = $true
    previousRevision = $currentRevision
    currentRevision = $updatedCurrent
    rollbackTargetHealth = $targetHealth
} | ConvertTo-Json -Depth 6
