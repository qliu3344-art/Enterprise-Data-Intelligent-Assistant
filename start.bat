@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   企业数据智能助手 — 启动中...
echo ========================================
echo.

echo [1/3] 释放端口...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8002') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo   完成

echo [2/3] 打开浏览器...
start http://localhost:8002

echo [3/3] 启动服务...
echo.
echo   后端: http://localhost:8002
echo   API文档: http://localhost:8002/docs
echo   按 Ctrl+C 停止
echo.

REM --loop 指定事件循环工厂：Windows 默认的 ProactorEventLoop 用不了 psycopg 的异步
REM 模式（会话检查点要用），而 uvicorn 在 win32 上是绕过全局策略显式选 Proactor 的。
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --loop app.main:selector_loop_factory

pause
