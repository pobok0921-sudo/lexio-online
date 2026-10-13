@echo off
chcp 65001 >nul
title 렉시오 온라인 서버
echo ================================================
echo   렉시오 온라인 게임 서버
echo ------------------------------------------------
echo   내 PC에서 접속:  http://localhost:8000
echo   같은 와이파이:   http://(이 PC IP):8000
echo   (종료하려면 이 창에서 Ctrl+C 또는 창 닫기)
echo ================================================
echo.
cd /d "%~dp0"
python server.py
pause
