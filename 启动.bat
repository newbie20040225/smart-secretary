@echo off
chcp 936 >nul
title 智能小秘书 - 启动
setlocal

REM ==========================================================
REM  智能小秘书 一键启动
REM  作用：用项目自带 Python 环境启动 MCP 服务端 + Gradio 界面
REM  停止：关闭本窗口，或双击同目录下的 关闭.bat
REM ==========================================================

cd /d "%~dp0"

REM 项目专用隔离 Python 环境，依赖都装在这里
set "VENVPY=C:\Users\16846\.workbuddy\binaries\python\envs\smart_secretary\Scripts\python.exe"

echo ==========================================================
echo   智能小秘书 - 启动中
echo ==========================================================
echo.

if not exist "%VENVPY%" (
    echo [错误] 找不到项目 Python 环境:
    echo        %VENVPY%
    echo.
    echo 请先安装依赖，或修改本脚本里的 VENVPY 路径。
    pause
    exit /b 1
)

echo [1/3] 检查并释放端口 7860 / 8000 ...
for %%P in (7860 8000) do call :freeme %%P

echo [2/3] 启动 MCP 服务端与 Web 界面 ...
echo [3/3] 启动完成后浏览器访问: http://127.0.0.1:7860
echo.
echo ----------------------------------------------------------
echo   关闭：关闭本窗口，或双击 关闭.bat
echo ----------------------------------------------------------
echo.

"%VENVPY%" run.py

echo.
echo [提示] 应用已退出。
pause
endlocal
exit /b 0

:freeme
for /f "tokens=5" %%A in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do (
    echo      端口 %1 被 PID %%A 占用，正在结束 ...
    taskkill /f /pid %%A >nul 2>&1
)
goto :eof
