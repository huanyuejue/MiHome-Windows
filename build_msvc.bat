@echo off
:: ============================================
:: MiHome-Windows one-click build entry (double-click friendly)
:: All build logic lives in build.ps1 to keep Nuitka
:: arguments in a single place.
::
:: NOTE: build.ps1 / make_installer.ps1 contain Chinese text and MUST stay
:: UTF-8 **with BOM**. Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI,
:: garbles the Chinese and can fail to parse at all. Prefer pwsh (PowerShell 7)
:: when available: it handles both encodings, so a lost BOM cannot break us.
:: ============================================
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
)
pause
