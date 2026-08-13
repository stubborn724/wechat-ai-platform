<#
.SYNOPSIS
    恢复微信公众号 AI 平台的本地运行环境。

.DESCRIPTION
    此脚本供 Windows 登录后的计划任务调用。它依次确保 Docker、基础容器、
    FastAPI 接口和 Vite 前端都已可用。每一步均先检查现有状态，避免电脑恢复
    或人工重复执行时启动重复的前端、后端进程。
#>

[CmdletBinding()]
param(
    [int]$DockerReadyTimeoutSeconds = 300,
    [int]$ServiceReadyTimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendPath = Join-Path $ProjectRoot 'frontend'
$BackendPath = Join-Path $ProjectRoot 'backend'
$RuntimeLogDirectory = Join-Path $ProjectRoot 'logs\runtime'
$ApiLogPath = Join-Path $RuntimeLogDirectory 'api-server.log'
$ApiErrorLogPath = Join-Path $RuntimeLogDirectory 'api-server-error.log'
$FrontendLogPath = Join-Path $RuntimeLogDirectory 'frontend.log'
$FrontendErrorLogPath = Join-Path $RuntimeLogDirectory 'frontend-error.log'

New-Item -ItemType Directory -Force -Path $RuntimeLogDirectory | Out-Null

function Write-RuntimeLog {
    <#
    .SYNOPSIS
        输出带时间的启动日志，便于在任务计划程序和日志文件中排查恢复失败。
    #>
    param([Parameter(Mandatory)][string]$Message)

    Write-Output ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Test-LocalPort {
    <#
    .SYNOPSIS
        判断本机端口是否已处于监听状态。

    .DESCRIPTION
        使用系统连接表而不是 HTTP 请求，确保在 API 仍处于鉴权状态时也能识别
        服务已经存在，避免重复拉起进程。
    #>
    param([Parameter(Mandatory)][int]$Port)

    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1)
}

function Wait-ForCondition {
    <#
    .SYNOPSIS
        在限定时间内轮询启动条件，超时后给出明确错误。
    #>
    param(
        [Parameter(Mandatory)][scriptblock]$Condition,
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) {
            return
        }
        Start-Sleep -Seconds 2
    }

    throw "等待 $Description 超时（${TimeoutSeconds} 秒）。请查看 $RuntimeLogDirectory。"
}

function Start-DockerDesktopIfNeeded {
    <#
    .SYNOPSIS
        当 Docker 引擎尚未可用时，尝试启动 Docker Desktop 并等待其就绪。
    #>
    try {
        docker version --format '{{.Server.Version}}' | Out-Null
        return
    }
    catch {
        $dockerDesktopCandidates = @(
            (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
            (Join-Path $env:LOCALAPPDATA 'Docker\Docker\Docker Desktop.exe')
        ) | Where-Object { Test-Path -LiteralPath $_ }

        if ($dockerDesktopCandidates.Count -eq 0) {
            throw 'Docker 引擎未就绪，且未找到 Docker Desktop 可执行文件。'
        }

        Write-RuntimeLog 'Docker 引擎未就绪，正在启动 Docker Desktop。'
        Start-Process -FilePath $dockerDesktopCandidates[0] | Out-Null
    }

    Wait-ForCondition -Description 'Docker 引擎就绪' -TimeoutSeconds $DockerReadyTimeoutSeconds -Condition {
        try {
            docker version --format '{{.Server.Version}}' | Out-Null
            return $true
        }
        catch {
            return $false
        }
    }
}

function Start-PlatformContainers {
    <#
    .SYNOPSIS
        启动数据库、缓存、对象存储与 Celery 容器。

    .DESCRIPTION
        使用 --no-recreate 只启动停止或缺失的容器。开机恢复不应因本地代码或
        compose 配置发生变化而重建正在执行文章任务的 Worker。
    #>
    Write-RuntimeLog '确保 Docker 基础服务和 Celery Worker 处于运行状态。'
    Push-Location $ProjectRoot
    try {
        docker compose up -d --no-recreate | Out-Host
    }
    finally {
        Pop-Location
    }
}

function Start-ApiServerIfNeeded {
    <#
    .SYNOPSIS
        在 8002 未监听时启动 FastAPI，并以 docs 健康页验证启动完成。
    #>
    if (Test-LocalPort -Port 8002) {
        Write-RuntimeLog 'API 服务已经监听 8002，跳过重复启动。'
        return
    }

    $pythonPath = Join-Path $BackendPath 'venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "未找到后端 Python 运行环境：$pythonPath"
    }

    Write-RuntimeLog '启动 FastAPI 服务（8002）。'
    # 强制 UTF-8 输出：日志里含 emoji，Windows 中文系统默认 GBK 重定向会抛
    # UnicodeEncodeError 导致请求 500（见 backend/app/main.py 顶部的同源修复）。
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $apiCommand = "Set-Location -LiteralPath '$BackendPath'; & '$pythonPath' -m uvicorn app.main:app --host 0.0.0.0 --port 8002 1>> '$ApiLogPath' 2>> '$ApiErrorLogPath'"
    Start-Process -FilePath 'pwsh.exe' -ArgumentList '-NoLogo', '-NoProfile', '-Command', $apiCommand -WindowStyle Hidden | Out-Null

    Wait-ForCondition -Description 'FastAPI 服务（8002）' -TimeoutSeconds $ServiceReadyTimeoutSeconds -Condition {
        try {
            return (Invoke-WebRequest -Uri 'http://localhost:8002/docs' -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200
        }
        catch {
            return $false
        }
    }
}

function Start-FrontendIfNeeded {
    <#
    .SYNOPSIS
        在 5173 未监听时启动 Vite 前端，并请求页面确认可访问。
    #>
    if (Test-LocalPort -Port 5173) {
        Write-RuntimeLog '前端服务已经监听 5173，跳过重复启动。'
        return
    }

    if (-not (Test-Path -LiteralPath (Join-Path $FrontendPath 'node_modules'))) {
        throw "前端依赖未安装：$FrontendPath\node_modules"
    }

    Write-RuntimeLog '启动 Vite 前端（5173）。'
    $frontendCommand = "Set-Location -LiteralPath '$FrontendPath'; npm.cmd run dev -- --host 0.0.0.0 1>> '$FrontendLogPath' 2>> '$FrontendErrorLogPath'"
    Start-Process -FilePath 'pwsh.exe' -ArgumentList '-NoLogo', '-NoProfile', '-Command', $frontendCommand -WindowStyle Hidden | Out-Null

    Wait-ForCondition -Description 'Vite 前端（5173）' -TimeoutSeconds $ServiceReadyTimeoutSeconds -Condition {
        try {
            return (Invoke-WebRequest -Uri 'http://localhost:5173/' -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200
        }
        catch {
            return $false
        }
    }
}

try {
    Write-RuntimeLog '开始恢复微信公众号 AI 平台。'
    Start-DockerDesktopIfNeeded
    Start-PlatformContainers
    Start-ApiServerIfNeeded
    Start-FrontendIfNeeded
    Write-RuntimeLog '平台恢复完成：前端 5173、API 8002 和 Docker Worker 均已可用。'
}
catch {
    Write-Error $_
    exit 1
}
