# 企业数据智能助手 — 一键启动脚本
# 用法: 右键 start.ps1 → "使用 PowerShell 运行"，或在终端输入 .\start.ps1

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  企业数据智能助手 — 启动中..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 Python
Write-Host "[1/4] 检查 Python..." -ForegroundColor Yellow
try { python --version 2>&1 | Out-Null } catch { Write-Host "错误: 未找到 Python" -ForegroundColor Red; pause; exit 1 }
Write-Host "  Python OK" -ForegroundColor Green

# 2. 检查 .env 配置
Write-Host "[2/4] 检查配置..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) { Write-Host "错误: 未找到 .env 文件" -ForegroundColor Red; pause; exit 1 }
$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch "DASHSCOPE_API_KEY=") { Write-Host "警告: .env 中未配置 DASHSCOPE_API_KEY" -ForegroundColor Yellow }
if ($envContent -notmatch "MYSQL_PASSWORD=") { Write-Host "警告: .env 中未配置 MYSQL_PASSWORD" -ForegroundColor Yellow }
Write-Host "  配置 OK" -ForegroundColor Green

# 3. 检查端口占用
Write-Host "[3/4] 检查端口..." -ForegroundColor Yellow
$existing = Get-NetTCPConnection -LocalPort 8002 -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  端口 8002 已被占用，正在释放..." -ForegroundColor Yellow
    foreach ($conn in $existing) {
        try { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop } catch {}
    }
    Start-Sleep -Seconds 2
    Write-Host "  端口已释放" -ForegroundColor Green
} else {
    Write-Host "  端口 8002 可用" -ForegroundColor Green
}

# 4. 启动服务
Write-Host "[4/4] 启动服务..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  后端: http://localhost:8002" -ForegroundColor Cyan
Write-Host "  API文档: http://localhost:8002/docs" -ForegroundColor Cyan
Write-Host "  按 Ctrl+C 停止服务" -ForegroundColor Gray
Write-Host ""

Start-Sleep -Seconds 1

# 打开浏览器
Start-Process "http://localhost:8002"

# 启动 uvicorn（前台运行）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002

pause
