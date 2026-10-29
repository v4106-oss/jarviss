@echo off
setlocal

rem Jarvis C++ derleme betiği — Ağ katmanı (Modül B) AÇIK.
rem cpr/libcurl kaynaktan derlenir (ilk sefer birkaç dakika sürebilir).

set "VSVC=C:\Program Files\Microsoft Visual Studio\18\Professional\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VSVC%" set "VSVC=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VSVC%" (
    echo [HATA] Visual Studio vcvars64.bat bulunamadı. MSVC derleyicisi kurulu olmalı.
    exit /b 1
)

call "%VSVC%" >nul
if errorlevel 1 (
    echo [HATA] vcvars64.bat yürütülemedi.
    exit /b 1
)

set "CMAKE=C:\Program Files\Microsoft Visual Studio\18\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" set "CMAKE=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" set "CMAKE=cmake"

set "NINJA=C:\Program Files\Microsoft Visual Studio\18\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
if not exist "%NINJA%" set "NINJA=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"

echo === Yapılandırma (configure, NET=ON) ===
"%CMAKE%" -S "%~dp0." -B "%~dp0build" -G Ninja -DCMAKE_MAKE_PROGRAM="%NINJA%" -DCMAKE_BUILD_TYPE=Release -DJARVIS_ENABLE_NET=ON
if errorlevel 1 exit /b 1

echo === Derleme (build) ===
"%CMAKE%" --build "%~dp0build"
if errorlevel 1 exit /b 1

echo === Testler ===
"%~dp0build\core_test.exe"
if errorlevel 1 exit /b 1
"%~dp0build\storage_test.exe"
if errorlevel 1 exit /b 1
if exist "%~dp0build\net_test.exe" (
    "%~dp0build\net_test.exe"
    if errorlevel 1 exit /b 1
)
if exist "%~dp0build\ai_test.exe" (
    "%~dp0build\ai_test.exe"
    if errorlevel 1 exit /b 1
)

echo.
echo Tamamlandı: ağ katmanı dahil tüm hedefler üretildi.
endlocal
exit /b 0
