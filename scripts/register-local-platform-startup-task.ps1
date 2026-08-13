<#
.SYNOPSIS
    注册“微信公众号 AI 平台自动恢复”Windows 登录自启动入口。

.DESCRIPTION
    优先使用当前用户的 Windows 计划任务；若企业策略拒绝创建计划任务，则依次
    回退到当前用户的“启动”文件夹和 HKCU Run 注册表项。三种入口均在用户登录后
    触发，因为 Docker Desktop 需要当前用户的 WSL/桌面运行环境。实际恢复逻辑
    位于 start-local-platform.ps1。
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StartupScriptPath = Join-Path $ProjectRoot 'scripts\start-local-platform.ps1'
$TaskName = '微信公众号AI平台自动恢复'
$StartupDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
$StartupLauncherPath = Join-Path $StartupDirectory '微信公众号AI平台自动恢复.vbs'
$RunRegistryPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunRegistryValueName = '微信公众号AI平台自动恢复'

if (-not (Test-Path -LiteralPath $StartupScriptPath)) {
    throw "未找到平台恢复脚本：$StartupScriptPath"
}

try {
    # 登录后延迟一分钟，给网络和 Docker Desktop 留出初始化时间；脚本仍会继续轮询 Docker。
    $action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$StartupScriptPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $trigger.Delay = 'PT1M'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'Windows 登录后自动恢复微信公众号 AI 平台的 Docker、API 与前端服务。' -Force -ErrorAction Stop | Out-Null

    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        throw "计划任务 $TaskName 未出现在 Windows 任务列表中。"
    }
    Write-Output "已注册 Windows 计划任务：$TaskName"
}
catch {
    # 某些企业 Windows 策略会禁止用户注册计划任务。启动文件夹属于当前用户，不需要
    # 管理员权限，且仍会在登录后执行同一份幂等恢复脚本。
    try {
        $escapedScriptPath = $StartupScriptPath.Replace("'", "''")
        $launcherContent = @"
' 微信公众号 AI 平台登录恢复启动器。由注册脚本生成，请勿手工修改。
Set shell = CreateObject("WScript.Shell")
shell.Run "pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ""$escapedScriptPath""", 0, False
"@
        Set-Content -LiteralPath $StartupLauncherPath -Value $launcherContent -Encoding ASCII -ErrorAction Stop
        Write-Warning "Windows 计划任务注册被拒绝，已改用当前用户启动文件夹：$StartupLauncherPath"
    }
    catch {
        # 企业策略可能同时锁定计划任务和启动文件夹。HKCU Run 是用户级注册表项，
        # 无需管理员权限；命令直接调用恢复脚本，不依赖额外的启动器文件。
        $runCommand = "pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$StartupScriptPath`""
        New-Item -Path $RunRegistryPath -Force -ErrorAction Stop | Out-Null
        New-ItemProperty -Path $RunRegistryPath -Name $RunRegistryValueName -Value $runCommand -PropertyType String -Force -ErrorAction Stop | Out-Null

        $registeredCommand = (Get-ItemProperty -Path $RunRegistryPath -Name $RunRegistryValueName -ErrorAction Stop).$RunRegistryValueName
        if ($registeredCommand -ne $runCommand) {
            throw "HKCU Run 注册表项 $RunRegistryValueName 写入后校验失败。"
        }

        Write-Warning "Windows 计划任务与启动文件夹均被策略限制，已改用当前用户注册表登录自启动。"
        Write-Output "注册表位置：$RunRegistryPath\\$RunRegistryValueName"
    }
}
