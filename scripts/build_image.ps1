$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$buildRoot = Join-Path "D:\CodexTemp\qianrenqianan" "build-context-$timestamp"

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
$frontendBuildRoot = Join-Path "D:\CodexTemp\qianrenqianan" "frontend-context-$timestamp"
New-Item -ItemType Directory -Force -Path $frontendBuildRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $frontendRoot "src") -Destination $frontendBuildRoot -Recurse
Copy-Item -LiteralPath (Join-Path $frontendRoot "public") -Destination $frontendBuildRoot -Recurse
foreach ($fileName in @("Dockerfile", "nginx.conf", "package.json", "package-lock.json", "index.html", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json", "vite.config.ts", "eslint.config.js")) {
    Copy-Item -LiteralPath (Join-Path $frontendRoot $fileName) -Destination $frontendBuildRoot
}

docker build -t adaptive-learning-agent-web:local $frontendBuildRoot
if ($LASTEXITCODE -ne 0) {
    throw "Frontend Docker image build failed with exit code $LASTEXITCODE"
}

Write-Output "Built backend from $buildRoot and frontend from $frontendBuildRoot"
