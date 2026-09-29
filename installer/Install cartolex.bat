@echo off
rem SPDX-License-Identifier: MIT
rem Windows: double-click to install or update cartolex. The first time, Windows may
rem warn about an unknown publisher: More info, then Run anyway.
rem The work is done by install-cartolex.ps1, next to this file.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-cartolex.ps1"
echo.
pause
