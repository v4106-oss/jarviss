@echo off
setlocal
rem Yardımcı: sembolü (PDB) olan Release derleme — yalnızca teşhis için ayrı build dizini.
rem Kullanım: build_sym.bat agent_probe

set "VSVC=C:\Program Files\Microsoft Visual Studio\18\Professional\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VSVC%" set "VSVC=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
call "%VSVC%" >nul

set "CMAKE=C:\Program Files\Microsoft Visual Studio\18\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" set "CMAKE=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "NINJA=C:\Program Files\Microsoft Visual Studio\18\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
if not exist "%NINJA%" set "NINJA=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"

if not exist "%~dp0build_sym\build.ninja" (
    "%CMAKE%" -S "%~dp0." -B "%~dp0build_sym" -G Ninja -DCMAKE_MAKE_PROGRAM="%NINJA%" ^
        -DCMAKE_BUILD_TYPE=Release -DJARVIS_ENABLE_NET=ON -DJARVIS_ENABLE_UI=OFF ^
        -DCMAKE_CXX_FLAGS="/Zi" -DCMAKE_EXE_LINKER_FLAGS="/DEBUG /INCREMENTAL:NO"
    if errorlevel 1 exit /b 1
)
rem Argüman yoksa tümünü derle; "--target" boş bırakılırsa CMake hata veriyor.
if "%*"=="" (
    "%CMAKE%" --build "%~dp0build_sym"
) else (
    "%CMAKE%" --build "%~dp0build_sym" --target %*
)
if errorlevel 1 exit /b 1
endlocal
exit /b 0
