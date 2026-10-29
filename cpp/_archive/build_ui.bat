@echo off
setlocal

rem Jarvis C++ tam derleme betiği — Ağ (Modül B) + Qt6 arayüz (Modül D) AÇIK.
rem Qt6 önceden kurulu olmalı: C:\Qt\6.8.3\msvc2022_64

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

set "QT_ROOT=C:\Qt\6.8.3\msvc2022_64"
if not exist "%QT_ROOT%\lib\cmake\Qt6\Qt6Config.cmake" (
    echo [HATA] Qt6 bulunamadı: %QT_ROOT%
    echo         aqt install-qt windows desktop 6.8.3 win64_msvc2022_64 -O C:\Qt
    exit /b 1
)

echo === Yapılandırma (configure, NET+UI=ON) ===
"%CMAKE%" -S "%~dp0." -B "%~dp0build" -G Ninja -DCMAKE_MAKE_PROGRAM="%NINJA%" -DCMAKE_BUILD_TYPE=Release -DJARVIS_ENABLE_NET=ON -DJARVIS_ENABLE_UI=ON -DCMAKE_PREFIX_PATH="%QT_ROOT%"
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
for %%T in (safety_test agent_test worker_test ai_test speech_test executor_test) do (
    if exist "%~dp0build\%%T.exe" (
        "%~dp0build\%%T.exe"
        if errorlevel 1 exit /b 1
    )
)

echo.
echo Tamamlandı: jarvis.exe (Qt6 arayüz) dahil tüm hedefler üretildi.
echo Çalıştırmak: build\jarvis.exe
endlocal
exit /b 0
