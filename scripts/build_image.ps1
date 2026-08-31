param(
    [string]$TempRoot = "D:\CodexTemp\qianrenqianan-harness\docker-build",
    [switch]$KeepBuildContext
)

$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot)
$buildRoot = Join-Path $resolvedTempRoot "backend-$timestamp"
$frontendBuildRoot = Join-Path $resolvedTempRoot "frontend-$timestamp"

function Remove-ValidatedBuildContext {
    param([string]$Path)

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $expectedPrefix = $resolvedTempRoot.TrimEnd('\') + '\'
    if (-not $resolvedPath.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build context outside temp root: $resolvedPath"
    }
    if (Test-Path -LiteralPath $resolvedPath) {
        Remove-Item -LiteralPath $resolvedPath -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $resolvedTempRoot | Out-Null

try {
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot "app") -Destination $buildRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $sourceRoot "alembic") -Destination $buildRoot -Recurse

    foreach ($fileName in @("Dockerfile", "pyproject.toml", "uv.lock", "README.md", "alembic.ini")) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot $fileName) -Destination $buildRoot
    }

    docker build -t adaptive-learning-agent:local $buildRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image build failed with exit code $LASTEXITCODE"
    }

    $frontendRoot = Join-Path $sourceRoot "frontend"
    New-Item -ItemType Directory -Force -Path $frontendBuildRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $frontendRoot "src") -Destination $frontendBuildRoot -Recurse
    if (Test-Path -LiteralPath (Join-Path $frontendRoot "public")) {
        Copy-Item -LiteralPath (Join-Path $frontendRoot "public") -Destination $frontendBuildRoot -Recurse
    }
    foreach ($fileName in @("Dockerfile", "nginx.conf", "package.json", "package-lock.json", "index.html", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json", "vite.config.ts", "eslint.config.js")) {
        Copy-Item -LiteralPath (Join-Path $frontendRoot $fileName) -Destination $frontendBuildRoot
    }

    docker build -t adaptive-learning-agent-web:local $frontendBuildRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend Docker image build failed with exit code $LASTEXITCODE"
    }

    Write-Output "Built adaptive-learning-agent:local and adaptive-learning-agent-web:local"
}
finally {
    if (-not $KeepBuildContext) {
        Remove-ValidatedBuildContext -Path $buildRoot
        Remove-ValidatedBuildContext -Path $frontendBuildRoot
    }
}
