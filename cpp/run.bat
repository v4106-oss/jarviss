@echo off
setlocal

rem Jarvis C++ baslatma betigi. Derlenmis urunu (cpp\build\jarvis.exe) calistirir.
rem Kullanim:
rem   cpp\run.bat            grafik pencereyi acip betigi serbest birakir
rem   cpp\run.bat -c         pencereyi bu konsola baglar (log/cokme kodu izlemek icin)
rem   cpp\run.bat -h         yardim
rem Not: jarvis.exe Qt DLL'leriyle birlikte cpp\build icinde durur; bu yuzden
rem calisma klasoru olarak build kullanilir.

if /i "%~1"=="-h" (
    echo Kullanim: run.bat [-c] [ek argumanlar]
    echo   -c  pencereyi bu konsola baglar; kapanista cikis kodunu gorursunuz.
    echo Derleme eksikse: cpp\build.bat
    exit /b 0
)

set "ATTACH=0"
if /i "%~1"=="-c" set "ATTACH=1"
if "%ATTACH%"=="1" shift

set "BUILD=%~dp0build"
if not exist "%BUILD%\jarvis.exe" (
    echo [HATA] %BUILD%\jarvis.exe bulunamadi. Once cpp\build.bat calistirin.
    exit /b 1
)

rem Qt kurulumu varsa PATH'e eklenir: build kopyasinda bulunmayan bir eklenti
rem yine de kuruludan gelir.
set "QT_ROOT=C:\Qt\6.8.3\msvc2022_64"
if exist "%QT_ROOT%\bin" set "PATH=%QT_ROOT%\bin;%PATH%"

tasklist /FI "IMAGENAME eq jarvis.exe" 2>nul | find /i "jarvis.exe" >nul
if not errorlevel 1 echo [BILGI] jarvis.exe zaten calisiyor; ikinci kopya aciliyor.

pushd "%BUILD%"
if "%ATTACH%"=="1" (
    jarvis.exe %*
    set "RC=%ERRORLEVEL%"
    popd
    echo [BILGI] jarvis.exe kapandi, cikis kodu=%RC%
    endlocal & exit /b %RC%
)
start "" jarvis.exe %*
popd
endlocal
exit /b 0
