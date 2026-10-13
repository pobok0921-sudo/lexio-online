@echo off
chcp 65001 >nul
title 렉시오 인터넷 공개 (Cloudflare 터널)
cd /d "%~dp0"
echo ================================================
echo   렉시오 인터넷 공개 (Cloudflare 무료 터널)
echo ------------------------------------------------
echo   * 먼저 실행.bat 으로 게임 서버를 켜 두세요.
echo   * 잠시 후 아래에 https://xxxxx.trycloudflare.com
echo     주소가 나옵니다. 그 주소를 친구에게 보내세요.
echo   * 집 밖 친구도 그 주소로 바로 접속할 수 있어요.
echo   * 이 창을 닫으면 주소가 사라집니다(= 외부 접속 종료).
echo ================================================
echo.
set "CF=%~dp0cloudflared.exe"
if not exist "%CF%" set "CF=cloudflared"
if "%CF%"=="cloudflared" (
  where cloudflared >nul 2>nul
  if errorlevel 1 set "CF=%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"
)
"%CF%" tunnel --url http://localhost:8000
echo.
echo 터널이 종료되었습니다.
pause
