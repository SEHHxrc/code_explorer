param(
    [string]$ProjectRoot = '',
    [string]$Proxy = 'https://goproxy.cn'
)
# 仅准备本项目的 Go 模块缓存；不执行目标代码，不修改原项目的模块清单。
$ErrorActionPreference = 'Stop'
if (-not $Proxy.StartsWith('https://')) { throw '模块代理必须使用 HTTPS。' }
$workspacePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$goEnvironmentPath = Join-Path $workspacePath '.goenv'
$savedEnvironment = @{}
Get-ChildItem Env: | Where-Object { $_.Name -match '^(GO|CGO_)' } | ForEach-Object {
    $savedEnvironment[$_.Name] = $_.Value
}
try {
    foreach ($key in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key, $null, 'Process') }
    foreach ($directory in @('gopath', 'modules', 'build', 'tmp', 'staging', 'logs')) {
        New-Item -ItemType Directory -Path (Join-Path $goEnvironmentPath $directory) -Force | Out-Null
    }
    $env:GOPATH = Join-Path $goEnvironmentPath 'gopath'
    $env:GOMODCACHE = Join-Path $goEnvironmentPath 'modules'
    $env:GOCACHE = Join-Path $goEnvironmentPath 'build'
    $env:GOTMPDIR = Join-Path $goEnvironmentPath 'tmp'
    $env:GOPROXY = $Proxy
    $env:GOSUMDB = 'sum.golang.org'
    $env:GOTOOLCHAIN = 'local'
    $env:GOPACKAGESDRIVER = 'off'
    $env:GOWORK = 'off'
    $env:GOENV = 'off'
    $env:GOAUTH = 'off'
    $env:GOVCS = '*:off'
    $env:CGO_ENABLED = '0'
    $stagingPath = Join-Path (Join-Path $goEnvironmentPath 'staging') ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stagingPath | Out-Null
    $moduleDirectories = [System.Collections.Generic.List[string]]::new()
    $roots = @(@{ Source = $PSScriptRoot; Name = 'tool' })
    if ($ProjectRoot) { $roots += @{ Source = (Resolve-Path -LiteralPath $ProjectRoot).Path; Name = 'target' } }
    foreach ($root in $roots) {
        $sourcePath = [IO.Path]::GetFullPath($root.Source)
        $copyRoot = Join-Path $stagingPath $root.Name
        # 只复制 Go 模块清单，不复制脚本或源码；保留目录层级使本地 replace 仍指向副本。
        $manifests = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
        $pending = [System.Collections.Generic.Stack[string]]::new()
        $pending.Push($sourcePath)
        while ($pending.Count) {
            $current = $pending.Pop()
            foreach ($manifest in Get-ChildItem -LiteralPath $current -Filter go.mod -File) {
                if ($manifest.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '模块清单不能是符号链接。' }
                $manifests.Add($manifest)
            }
            foreach ($child in Get-ChildItem -LiteralPath $current -Directory -Force) {
                if ($child.Name -notin @('.git', 'node_modules', '.goenv', '.venv', 'vendor') -and
                    -not ($child.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $pending.Push($child.FullName) }
            }
        }
        foreach ($manifest in $manifests) {
            $relative = [IO.Path]::GetRelativePath($sourcePath, $manifest.DirectoryName)
            $destination = [IO.Path]::GetFullPath((Join-Path $copyRoot $relative))
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
            Copy-Item -LiteralPath $manifest.FullName -Destination (Join-Path $destination 'go.mod')
            $sum = Join-Path $manifest.DirectoryName 'go.sum'
            if (Test-Path -LiteralPath $sum) {
                if ((Get-Item -LiteralPath $sum).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '模块校验清单不能是符号链接。' }
                Copy-Item -LiteralPath $sum -Destination (Join-Path $destination 'go.sum')
            }
            $moduleDirectories.Add($destination)
        }
        foreach ($directory in @($moduleDirectories | Where-Object { $_.StartsWith($copyRoot + [IO.Path]::DirectorySeparatorChar) -or $_ -eq $copyRoot })) {
            $metadata = & go mod edit -json (Join-Path $directory 'go.mod') | ConvertFrom-Json
            if ($LASTEXITCODE -ne 0) { throw '模块清单解析失败。' }
            foreach ($replacement in $metadata.Replace) {
                if (-not $replacement.New.Version) {
                    $local = [IO.Path]::GetFullPath((Join-Path $directory $replacement.New.Path))
                    if (-not ($local -eq $copyRoot -or $local.StartsWith($copyRoot + [IO.Path]::DirectorySeparatorChar))) {
                        throw '本地 replace 超出暂存项目，不允许安装。'
                    }
                    if (-not (Test-Path -LiteralPath (Join-Path $local 'go.mod'))) { throw '本地 replace 缺少模块清单。' }
                }
            }
        }
    }
    foreach ($directory in $moduleDirectories) {
        Write-Host "下载模块清单：$([IO.Path]::GetRelativePath($stagingPath, $directory))（可重试复用缓存）"
        Push-Location -LiteralPath $directory
        try {
            & go mod download all
            if ($LASTEXITCODE -ne 0) { throw '模块下载失败；已下载缓存保留，原项目未修改。' }
        } finally { Pop-Location }
    }
    New-Item -ItemType File -Path (Join-Path $goEnvironmentPath 'ready') -Force | Out-Null
    Write-Host "Go 隔离缓存准备完成：$goEnvironmentPath"
} finally {
    Get-ChildItem Env: | Where-Object { $_.Name -match '^(GO|CGO_)' } | ForEach-Object {
        [Environment]::SetEnvironmentVariable($_.Name, $null, 'Process')
    }
    foreach ($key in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process') }
}
