[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "微信群 AI 军师 · 全自动云端热更新"

$repoUser = "zhangyue6600-afk"
$repoName = "wechat-ai-advisor"
$appDir = (Get-Item .).FullName
$verFile = Join-Path $appDir "version.json"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "🚀 微信群 AI 军师 · 全自动云端热更新引擎" -ForegroundColor Cyan
Write-Host "📦 官方发布地址: https://github.com/zhangyue6600-afk/wechat-ai-advisor" -ForegroundColor Gray
Write-Host "==================================================================" -ForegroundColor Cyan

$localVer = "1.0.0"
if (Test-Path $verFile) {
    try {
        $localContent = Get-Content -LiteralPath $verFile -Raw -Encoding UTF8
        $localObj = $localContent | ConvertFrom-Json
        if ($localObj.version) { $localVer = $localObj.version }
    } catch {}
}
Write-Host "`n📌 当前本地版本: v$localVer" -ForegroundColor White
Write-Host "🔍 正在连接云端检查最新版本..." -ForegroundColor Yellow

$remoteRaw = ""
$checkEndpoints = @(
    "https://ghproxy.net/https://raw.githubusercontent.com/zhangyue6600-afk/wechat-ai-advisor/main/version.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/zhangyue6600-afk/wechat-ai-advisor/main/version.json",
    "https://raw.githubusercontent.com/zhangyue6600-afk/wechat-ai-advisor/main/version.json"
)

foreach ($url in $checkEndpoints) {
    try {
        $remoteRaw = & curl.exe -s --connect-timeout 6 -L $url
        if ($remoteRaw -and ($remoteRaw -match '"version"')) { break }
    } catch {}
}

if (-not $remoteRaw -or -not ($remoteRaw -match '"version"')) {
    Write-Host "`n⚠️ 无法连接到云端更新服务器，请检查网络或稍后重试。" -ForegroundColor Yellow
    Write-Host "💡 您当前的本地版本仍可正常完全离线运行。" -ForegroundColor Gray
    Write-Host "==================================================================" -ForegroundColor Cyan
    exit 0
}

$remoteObj = $remoteRaw | ConvertFrom-Json
$remoteVer = $remoteObj.version

if ($remoteVer -eq $localVer) {
    Write-Host "`n✅ 您当前运行的已经是最新稳定版本 (v$localVer)！无需更新。" -ForegroundColor Green
    Write-Host "==================================================================" -ForegroundColor Cyan
    exit 0
}

Write-Host "`n🎉 发现最新版本: v$remoteVer (当前版本为 v$localVer)！" -ForegroundColor Green
Write-Host "--------------------------------------------------" -ForegroundColor DarkGray
Write-Host "【更新内容】:" -ForegroundColor Cyan
if ($remoteObj.changelog) {
    foreach ($item in $remoteObj.changelog) {
        Write-Host "  $item" -ForegroundColor White
    }
}
Write-Host "--------------------------------------------------" -ForegroundColor DarkGray

Write-Host "`n⚡ 正在自动下载最新完整更新包 (无需 Python 环境)..." -ForegroundColor Yellow

$tempZip = Join-Path $env:TEMP "wechat_advisor_update.zip"
$tempExtract = Join-Path $env:TEMP "wechat_advisor_extracted"

if (Test-Path $tempZip) { Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue }
if (Test-Path $tempExtract) { Remove-Item -LiteralPath $tempExtract -Recurse -Force -ErrorAction SilentlyContinue }

$downloadUrls = @(
    "https://ghproxy.net/https://github.com/zhangyue6600-afk/wechat-ai-advisor/releases/download/v$remoteVer/WeChat-AI-Advisor-v$remoteVer-Windows.zip",
    "https://mirror.ghproxy.com/https://github.com/zhangyue6600-afk/wechat-ai-advisor/releases/download/v$remoteVer/WeChat-AI-Advisor-v$remoteVer-Windows.zip",
    "https://github.com/zhangyue6600-afk/wechat-ai-advisor/releases/download/v$remoteVer/WeChat-AI-Advisor-v$remoteVer-Windows.zip"
)

$downloadOk = $false
foreach ($dl in $downloadUrls) {
    Write-Host "   正在通过节点加速下载: $dl" -ForegroundColor Gray
    & curl.exe -# -L --connect-timeout 10 -o $tempZip $dl
    if ((Test-Path $tempZip) -and ((Get-Item $tempZip).Length -gt 10000000)) {
        $downloadOk = $true
        break
    }
}

if (-not $downloadOk) {
    Write-Host "`n❌ 自动下载更新包失败，请检查网络或稍后重试。" -ForegroundColor Red
    exit 1
}

Write-Host "`n📦 下载完毕！正在安全覆盖核心文件 (自动保护您的已有群知识库)..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $tempExtract -Force | Out-Null
& tar.exe -xf $tempZip -C $tempExtract

Get-ChildItem -Path $tempExtract -Recurse | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
    $rel = $_.FullName.Substring($tempExtract.Length + 1)
    if ($rel -notlike "data\*") {
        $targetPath = Join-Path $appDir $rel
        $parent = Split-Path $targetPath -Parent
        if (-not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
    }
}

Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $tempExtract -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`n✨ 升级成功！已无损全量更新到全新版本 v$remoteVer！" -ForegroundColor Green
Write-Host "💡 提示：您保存在 data/ 目录中的所有知识库数据完好无损！" -ForegroundColor Gray
Write-Host "==================================================================" -ForegroundColor Cyan
