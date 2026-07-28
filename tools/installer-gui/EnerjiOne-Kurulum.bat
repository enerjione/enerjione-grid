@echo off
REM EnerjiOne Grid — Saha Kurulum Araci
REM Cift tiklayarak calistirin. Python 3.11+ kurulu olmali.
setlocal
cd /d "%~dp0"

REM `py` launcher yoksa `python`a dus.
where py >nul 2>&1 && (set PY=py -3) || (set PY=python)

REM Bagimlilik eksikse bir kez kur — sahada "modul yok" hatasiyla ugrasilmasin.
%PY% -c "import paramiko" >nul 2>&1 || (
  echo Ilk calistirma: gerekli bilesen kuruluyor...
  %PY% -m pip install -r requirements.txt || (
    echo.
    echo HATA: bilesen kurulamadi. Internet baglantisini kontrol edin.
    pause
    exit /b 1
  )
)

%PY% e1_installer.py
if errorlevel 1 pause
