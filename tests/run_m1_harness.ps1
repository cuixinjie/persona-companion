param(
    [Parameter(Mandatory)]
    [string]$Runtime,
    [string]$Python = (Get-Command python -CommandType Application).Source,
    [ValidateSet('M1', 'M2', 'M3')]
    [string]$Milestone = 'M1',
    [string]$Package
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$runtimeRoot = (Resolve-Path -LiteralPath $Runtime).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$cli = Join-Path $runtimeRoot 'node_modules/@deepseek-ai/dsh/lib/bin.js'
$version = & node $cli --version
if ($LASTEXITCODE -ne 0 -or $version -ne '0.2.2-rc.2') {
    throw '验收需要独立安装 @deepseek-ai/dsh@0.2.2-rc.2。'
}

$run = Join-Path ([IO.Path]::GetTempPath()) ('persona-m1-run-' + [guid]::NewGuid())
$workspace = Join-Path $run 'workspace'
$skill = Join-Path $workspace '.dsh/skills/persona-companion'
$overlay = Join-Path $run 'overlay.yml'
$probe = Join-Path $run 'm1_harness_probe.mjs'
$previousDshHome = $env:DSH_HOME
$previousTelemetry = $env:DSH_TELEMETRY_DISABLED
$previousMode = $env:DSH_PERMISSION_MODE
$previousPython = $env:PERSONA_M1_TEST_PYTHON
$previousMilestone = $env:PERSONA_TEST_MILESTONE
$exitCode = 1
try {
    if ($Package) {
        $extracted = Join-Path $run 'extracted'
        Expand-Archive -LiteralPath (Resolve-Path -LiteralPath $Package).Path -DestinationPath $extracted
        $skillsDir = Split-Path -Parent $skill
        New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $extracted 'persona-companion') -Destination $skillsDir -Recurse
    } else {
        New-Item -ItemType Directory -Path $skill -Force | Out-Null
        $source = Join-Path $repo 'skill/persona-companion'
        Copy-Item -LiteralPath (Join-Path $source 'SKILL.md'), (Join-Path $source 'references') -Destination $skill -Recurse
        $scripts = Join-Path $skill 'scripts'
        New-Item -ItemType Directory -Path $scripts | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $source 'scripts') -Filter '*.py' -File | Copy-Item -Destination $scripts
        Copy-Item -LiteralPath (Join-Path $source 'scripts/migrations') -Destination $scripts -Recurse
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'm1_harness_probe.mjs') -Destination $probe
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'm3_flow.mjs') -Destination $run
    if ($Milestone -in ('M2', 'M3')) {
        $inbox = Join-Path $workspace 'private/tmp/imports'
        New-Item -ItemType Directory -Path $inbox -Force | Out-Null
        $clearFixture = if ($Package) { Join-Path $extracted 'examples/synthetic-chat.png' } else {
            Join-Path $PSScriptRoot 'fixtures/m2-clear.png'
        }
        Copy-Item -LiteralPath $clearFixture -Destination (Join-Path $inbox 'm2-clear.png')
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fixtures/m2-blurred.png') -Destination $inbox
    }
    $probeUri = [uri]::new($probe).AbsoluteUri
    @"
- id: headless-runner
  disabled: true
- insert:
    - id: persona-m1-probe
      name: '$probeUri'
"@ | Set-Content -LiteralPath $overlay -Encoding utf8
    & git -C $workspace init --quiet
    if ($LASTEXITCODE -ne 0) { throw '无法初始化隔离 Git 工作区。' }
    $env:DSH_HOME = Join-Path $run 'home'
    $env:DSH_TELEMETRY_DISABLED = '1'
    $env:DSH_PERMISSION_MODE = 'workspace-write'
    $env:PERSONA_M1_TEST_PYTHON = $pythonPath
    $env:PERSONA_TEST_MILESTONE = $Milestone
    Push-Location -LiteralPath $workspace
    try {
        & node $cli --profile headless --patch $overlay "$Milestone local probe"
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    $env:DSH_HOME = $previousDshHome
    $env:DSH_TELEMETRY_DISABLED = $previousTelemetry
    $env:DSH_PERMISSION_MODE = $previousMode
    $env:PERSONA_M1_TEST_PYTHON = $previousPython
    $env:PERSONA_TEST_MILESTONE = $previousMilestone
    if (Test-Path -LiteralPath $run) {
        $resolvedRun = (Resolve-Path -LiteralPath $run).Path
        $tempRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
        if ((Split-Path -Parent $resolvedRun) -eq $tempRoot -and
            (Split-Path -Leaf $resolvedRun) -match '^persona-m1-run-[0-9a-f-]{36}$') {
            Remove-Item -LiteralPath $resolvedRun -Recurse -Force
        }
    }
}
exit $exitCode

