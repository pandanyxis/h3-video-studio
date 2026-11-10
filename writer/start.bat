@echo off
setlocal
cd /d "%~dp0"

rem A future portable release can place a self-contained Python runtime here.
if exist "runtime\python\python.exe" (
  "runtime\python\python.exe" -m h3_standalone %*
  goto :done
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparing H3 Prompt Writer for the first launch...
  where uv >nul 2>nul
  if not errorlevel 1 (
    uv venv --python 3.15 ".venv" || goto :setup_failed
  ) else (
    where py >nul 2>nul
    if not errorlevel 1 (
      for %%V in (3.15 3.14 3.13 3.12) do (
        py -%%V -c "import sys" >nul 2>nul
        if not errorlevel 1 (
          py -%%V -m venv ".venv" || goto :setup_failed
          goto :environment_ready
        )
      )
      goto :python_missing
    ) else (
      where python >nul 2>nul || goto :python_missing
      python -c "import sys; sys.exit(not ((3,12) <= sys.version_info[:2] < (3,16)))" >nul 2>nul || goto :python_missing
      python -m venv ".venv" || goto :python_missing
    )
  )
)

:environment_ready
rem Use versions supported by the pinned binary video dependencies.
".venv\Scripts\python.exe" -c "import sys; sys.exit(not ((3,12) <= sys.version_info[:2] < (3,16)))" >nul 2>nul
if errorlevel 1 (
  echo This virtual environment uses an unsupported Python version.
  echo Rename .venv to keep a backup, then run start.bat with Python 3.15 or uv installed.
  goto :failed
)
rem Check existing environments too, including upgrades and interrupted setup.
".venv\Scripts\python.exe" -c "import aiohttp, av, PIL, numpy; from importlib.metadata import version; assert 19 <= int(version('av').split('.')[0]) < 20" >nul 2>nul
if errorlevel 1 (
  echo Installing required dependencies...
  where uv >nul 2>nul
  if not errorlevel 1 (
    uv pip install --python ".venv\Scripts\python.exe" --only-binary :all: -r requirements.txt || goto :setup_failed
  ) else (
    ".venv\Scripts\python.exe" -m pip install --only-binary :all: -r requirements.txt || goto :setup_failed
  )
)

".venv\Scripts\python.exe" -m h3_standalone %*
if errorlevel 1 goto :failed
goto :done

:python_missing
echo.
echo A compatible Python 3.12-3.15 was not found. Python 3.15 is recommended.
echo Install Python once, or use a release that includes runtime\python.
goto :failed

:setup_failed
echo.
echo The local environment could not be prepared.
goto :failed

:failed
pause
exit /b 1

:done
endlocal
