@echo off
setlocal

rem Jarvis C++ tek komut derleme betigi (MSVC + Ninja + CMake + Qt6).
rem Yapilandirma + tum hedeflerin derlenmesi + sirali testler.
rem Calistirmak icin: cpp\build.bat
rem Araclar: build_target.bat <hedef>  (secici derleme), build_sym.bat (PDB'li teshis derlemesi)

set "VSVC=C:\Program Files\Microsoft Visual Studio\18\Professional\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VSVC%" set "VSVC=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VSVC%" (
    echo [HATA] Visual Studio vcvars64.bat bulunamadi. MSVC derleyicisi kurulu olmali.
    exit /b 1
)

call "%VSVC%" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [HATA] vcvars64.bat yurutulemedi.
    exit /b 1
)

set "CMAKE=C:\Program Files\Microsoft Visual Studio\18\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" set "CMAKE=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" set "CMAKE=cmake"

set "NINJA=C:\Program Files\Microsoft Visual Studio\18\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
if not exist "%NINJA%" set "NINJA=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"

set "QT_ROOT=C:\Qt\6.8.3\msvc2022_64"
if not exist "%QT_ROOT%\lib\cmake\Qt6\Qt6Config.cmake" (
    echo [HATA] Qt6 bulunamadi: %QT_ROOT%
    echo         aqt install-qt windows desktop 6.8.3 win64_msvc2022_64 -O C:\Qt
    exit /b 1
)

echo === Yapilandirma (configure: NET + UI acik) ===
rem Linker/derleyici hatalari bazi araçlarda -1 doner; "if errorlevel 1" bunu gormez.
"%CMAKE%" -S "%~dp0." -B "%~dp0build" -G Ninja -DCMAKE_MAKE_PROGRAM="%NINJA%" -DCMAKE_BUILD_TYPE=Release -DJARVIS_ENABLE_NET=ON -DJARVIS_ENABLE_UI=ON -DCMAKE_PREFIX_PATH="%QT_ROOT%"
if %ERRORLEVEL% NEQ 0 (
    echo [HATA] Yapilandirma basarisiz, kod=%ERRORLEVEL%
    exit /b 1
)

echo === Derleme (build) ===
"%CMAKE%" --build "%~dp0build"
if %ERRORLEVEL% NEQ 0 (
    echo [HATA] Derleme basarisiz, kod=%ERRORLEVEL% - jarvis.exe calisiyorsa kapatip tekrar deneyin
    exit /b 1
)

echo === Testler ===
rem net_smoke ve agent_probe canli teshis araclaridir; test listesinde degil.
for %%T in (core_test storage_test net_test speech_test safety_test agent_test worker_test ai_test executor_test window_tools_test app_launcher_test ui_automation_test screen_tools_test) do (
    if not exist "%~dp0build\%%T.exe" (
        echo [HATA] %%T.exe uretilmedi.
        exit /b 1
    )
    echo --- %%T
    "%~dp0build\%%T.exe" || (
        echo [HATA] %%T basarisiz.
        exit /b 1
    )
)

echo.
echo Tamamlandi: 13/13 test gecti. Urun: cpp\build\jarvis.exe
endlocal
exit /b 0
