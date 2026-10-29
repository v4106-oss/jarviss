@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Jarvis kurulumu basliyor...
if exist ".venv\Scripts\python.exe" goto install
py -3.12 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto install
python -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto install
echo.
echo HATA: Python bulunamadi. python.org adresinden Python 3.12 kurun ve "Add python.exe to PATH" kutusunu isaretleyin.
pause
exit /b 1
:install
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install "PySide6>=6.8" "requests>=2.31" "pypdf>=6.0" "Send2Trash>=1.8" "pyttsx3>=2.99" "SpeechRecognition>=3.17"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install "PyAudio>=0.2.14"
if errorlevel 1 echo UYARI: PyAudio kurulamadi. Sesli giris calismaz ama uygulama acilir.
".venv\Scripts\python.exe" -c "import PySide6, requests; print('Kurulum tamam.')"
if errorlevel 1 goto fail
echo.
echo Tamam. Simdi "Jarvis Baslat.vbs" ile acabilirsiniz.
pause
exit /b 0
:fail
echo.
echo HATA: Kurulum tamamlanamadi. Yukaridaki hata mesajini bana gonderin.
pause
exit /b 1
