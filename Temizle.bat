@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Jarvis temizlik araci
echo.
findstr /c:"plugin_manager" "app\ui\main_window.py" >nul 2>&1
if not errorlevel 1 (
    echo HATA: Once Jarvis_guncelleme_5.zip dosyasini bu klasore cikarmalisin.
    echo Eski kod hala plugin sistemini kullaniyor, bu yuzden HICBIR SEY SILINMEDI.
    pause
    exit /b 1
)
echo Kullanilmayan dosyalar siliniyor...
if exist "plugins" rd /s /q "plugins"
for %%f in ("app\core\plugin_manager.py" "app\core\reasoning_agent.py" "tests\test_reasoning_agent.py" "tests\test_storage_agent_runs.py" "test.json" "test_file.txt") do if exist %%f del /q %%f
if exist "build" rd /s /q "build"
if exist "__pycache__" rd /s /q "__pycache__"
for /d /r "app" %%d in (__pycache__) do if exist "%%d" rd /s /q "%%d"
for /d /r "tests" %%d in (__pycache__) do if exist "%%d" rd /s /q "%%d"
echo Bos klasorler siliniyor (dolu olanlara dokunulmaz)...
for %%d in (models modules config logs assets test_folder) do if exist "%%d" rd "%%d" 2>nul
for /d %%d in (olu*) do rd "%%d" 2>nul
echo.
echo Bitti. Jarvis'i normal sekilde baslatabilirsin.
pause
