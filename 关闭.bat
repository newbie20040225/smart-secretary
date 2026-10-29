@echo off
chcp 936 >nul
title 智能小秘书 - 关闭
setlocal

REM ==========================================================
REM  智能小秘书 一键关闭
REM  作用：结束占用 7860 与 8000 端口的进程
REM ==========================================================

echo ==========================================================
echo   智能小秘书 - 正在关闭
echo ==========================================================
echo.

set FOUND=0

for %%P in (7860 8000) do call :killme %%P

if "%FOUND%"=="0" goto :none
echo.
echo [完成] 相关进程已结束。
goto :fin

:none
echo [信息] 未发现正在运行的服务，7860 与 8000 均空闲。

:fin
echo.
pause
endlocal
exit /b 0

:killme
for /f "tokens=5" %%A in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do (
    echo [关闭] 端口 %1 正在结束，PID = %%A
    taskkill /f /pid %%A >nul 2>&1
    set FOUND=1
)
goto :eof
