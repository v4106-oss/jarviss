cmake_minimum_required(VERSION 3.24)

project(jarvis_cpp
    VERSION 0.1.0
    DESCRIPTION "Jarvis masaüstü asistanının modern C++20 sürümü"
    LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---------------------------------------------------------------------------
# Derleyici uyarıları ve tutarlı karakter kümesi
# ---------------------------------------------------------------------------
if(MSVC)
    add_compile_options(/W4 /permissive- /utf-8 /EHsc /Zc:__cplusplus)
    # std::getenv bilinçli kullanılıyor (ortam değişkeni okuma); güvenli alternatif
    # _dupenv_s ham bellek yönetimi gerektirdiğinden modern C++ tercihiyle çakışır.
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
else()
    add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# windows.h'ın min/max makroları std::min/std::max çağrılarını bozuyor
# (C2589). Kod tabanı yalnızca std:: sürümlerini kullanıyor.
if(WIN32)
    add_compile_definitions(NOMINMAX)
endif()

# ---------------------------------------------------------------------------
# Dış bağımlılıklar (FetchContent ile otomatik indirilir)
# ---------------------------------------------------------------------------
include(FetchContent)

# nlohmann/json — JSON yönetimi (Modül A ve C)
set(JSON_BuildTests OFF CACHE INTERNAL "")
FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
    GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(nlohmann_json)

# SQLite amalgamation — tek .c dosyası, derleme sistemi gerektirmez (Modül C).
# Zip'te CMakeLists.txt olmadığı için içerik YALNIZCA indirilir; add_subdirectory
# kullanılamaz. Bu yüzden CMP0169 eski davranışta tutulur (FetchContent_Populate).
if(POLICY CMP0169)
    cmake_policy(SET CMP0169 OLD)
endif()
FetchContent_Declare(
    sqlite3_amalgamation
    URL https://www.sqlite.org/2024/sqlite-amalgamation-3450100.zip)
FetchContent_GetProperties(sqlite3_amalgamation)
if(NOT sqlite3_amalgamation_POPULATED)
    FetchContent_Populate(sqlite3_amalgamation)
endif()
add_library(sqlite3 STATIC ${sqlite3_amalgamation_SOURCE_DIR}/sqlite3.c)
target_include_directories(sqlite3 PUBLIC ${sqlite3_amalgamation_SOURCE_DIR})

# Modül B: cpr (libcurl tabanlı HTTP istemcisi)
# Varsayılan AÇIK: çıplak `cmake -B build` ağ katmanı olmadan eksik bir jarvis
# üretmesin. Kapalı derleme de desteklenir (cmake -DJARVIS_ENABLE_NET=OFF).
option(JARVIS_ENABLE_NET "Ağ/API katmanını derle (cpr)" ON)
if(JARVIS_ENABLE_NET)
    set(CPR_BUILD_TESTS OFF CACHE INTERNAL "")
    set(CPR_BUILD_TESTS_SSL OFF CACHE INTERNAL "")
    # libcurl sistemde yoksa kaynaktan derlenir; TLS olarak Windows'ta
    # Schannel (WinSSL) kullanılarak OpenSSL derleme yükü önlenir.
    set(CPR_USE_SYSTEM_CURL OFF CACHE INTERNAL "")
    set(CPR_FORCE_WINSSL_BACKEND ON CACHE INTERNAL "")
    FetchContent_Declare(
        cpr
        GIT_REPOSITORY https://github.com/libcpr/cpr.git
        GIT_TAG 1.11.1
        GIT_SHALLOW TRUE)
    FetchContent_MakeAvailable(cpr)
endif()

# Modül D: Qt6 arayüz katmanı
# Varsayılan AÇIK: Qt bulunamazsa find_package hata verir; bu proje Qt6'sız
# konsol derlemesini amaçlamıyor. Gerekirse -DJARVIS_ENABLE_UI=OFF.
option(JARVIS_ENABLE_UI "Qt6 arayüz katmanını derle" ON)
if(JARVIS_ENABLE_UI)
    find_package(Qt6 REQUIRED COMPONENTS Widgets Network Multimedia MultimediaWidgets WebChannel WebEngineWidgets)
    qt_standard_project_setup()
    # Çalışma zamanı eklentileri (platforms/, styles/) için Qt kökü.
    # Qt6_DIR = <kök>/lib/cmake/Qt6 olduğundan üç üst dizin Qt köküdür.
    get_filename_component(QT_ROOT_DIR "${Qt6_DIR}/../../.." ABSOLUTE)
    set(QT_PLUGINS_DIR "${QT_ROOT_DIR}/plugins")
    set(QT_BIN_DIR "${QT_ROOT_DIR}/bin")
    set(QT_RESOURCES_DIR "${QT_ROOT_DIR}/resources")
    set(QT_TRANSLATIONS_DIR "${QT_ROOT_DIR}/translations")
endif()

# ---------------------------------------------------------------------------
# Modül A — Çekirdek mantık ve veri yapıları
# ---------------------------------------------------------------------------
add_library(jarvis_core STATIC
    src/core/util.cpp
    src/core/log.cpp
    src/core/hash.cpp
    src/core/history.cpp
    src/core/settings.cpp
    src/core/home.cpp
    src/core/skill_library.cpp
    src/ai/safety.cpp
    src/ai/undo.cpp
    src/ai/agent.cpp)

target_include_directories(jarvis_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(jarvis_core PUBLIC nlohmann_json::nlohmann_json)
if(WIN32)
    # SHA-256 özetleri Windows CNG üzerinden (OpenSSL bağımlılığı yok).
    target_link_libraries(jarvis_core PUBLIC bcrypt)
endif()

# ---------------------------------------------------------------------------
# Modül C — Kalıcı depo (SQLite) ve dosya yönetimi
# ---------------------------------------------------------------------------
add_library(jarvis_storage STATIC
    src/storage/storage.cpp
    src/storage/task_session.cpp)
target_include_directories(jarvis_storage PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(jarvis_storage PUBLIC jarvis_core sqlite3)

# ---------------------------------------------------------------------------
# Modül C2 — Masaüstü eylem yürütücüsü (Python ActionExecutor)
# Somut Windows/Win32 araçları: dosya işlemleri, komut, pano, sistem, akıllı ev.
# Ağ gerektirmez; UI'ye bağlı araçlar geri çağrma kancasıyla devredilir.
# ---------------------------------------------------------------------------
add_library(jarvis_exec STATIC
    src/ai/executor.cpp
    src/tools/window_tools.cpp
    src/tools/ui_automation.cpp
    src/tools/app_launcher.cpp
    src/tools/screen_capture.cpp
    src/tools/ocr.cpp)
target_include_directories(jarvis_exec PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(jarvis_exec PUBLIC jarvis_storage)
if(WIN32)
    # UIA client API'si Windows SDK'nın kendi (uiautomationcore.dll her Windows'ta
    # var); üçüncü taraf bağımlılık eklenmiyor.
    target_link_libraries(jarvis_exec PUBLIC uiautomationcore)
    # Ekran/OCR araçları da Windows SDK ile gelir: WIC (PNG kodlama-çözme) ve
    # WindowsApp.lib (Windows.Media.Ocr için WinRT sınıf giriş noktaları).
    target_link_libraries(jarvis_exec PUBLIC windowscodecs windowsapp runtimeobject)
endif()

# ---------------------------------------------------------------------------
# Modül B — Ağ ve harici API katmanı (cpr/libcurl)
# ---------------------------------------------------------------------------
if(JARVIS_ENABLE_NET)
    add_library(jarvis_net STATIC src/net/http.cpp)
    target_include_directories(jarvis_net PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(jarvis_net PUBLIC jarvis_core cpr::cpr)

    # Zekâ katmanı: Gemini / Ollama / Token Harbor sohbeyürütme motoru + worker
    # orkestrasyonu. Ağ katmanına bağımlı olduğundan yalnızca JARVIS_ENABLE_NET ile
    # derlenir.
    add_library(jarvis_ai STATIC
        src/ai/ai.cpp
        src/ai/worker.cpp)
    target_include_directories(jarvis_ai PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(jarvis_ai PUBLIC jarvis_net jarvis_storage jarvis_exec)
endif()

# ---------------------------------------------------------------------------
# Modül E — Ses hattı kararları (metin biçimleme + mikrofon seçim mantığı)
# Qt/OS gerektirmez; oynatma ve yakalama servisleri bu kararları kullanır.
# ---------------------------------------------------------------------------
add_library(jarvis_speech STATIC
    src/speech/text.cpp
    src/speech/voice.cpp
    src/speech/pcm.cpp
    src/speech/stt.cpp
    src/speech/vad.cpp
    src/speech/synth.cpp)
target_include_directories(jarvis_speech PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(jarvis_speech PUBLIC jarvis_core)
if(WIN32)
    # SHA-256 önbellek anahtarı Windows CNG üzerinden (OpenSSL bağımlılığı yok).
    target_link_libraries(jarvis_speech PUBLIC bcrypt)
endif()

# ---------------------------------------------------------------------------
# Modül D — Qt6 arayüz katmanı
# ---------------------------------------------------------------------------
if(JARVIS_ENABLE_UI)
    add_library(jarvis_ui STATIC
        include/jarvis/ui/agent_runner.hpp
        include/jarvis/ui/mic_service.hpp
        include/jarvis/ui/speech_service.hpp
        src/ui/theme.cpp
        src/ui/titlebar.cpp
        src/ui/agent_runner.cpp
        src/ui/mic_service.cpp
        src/ui/speech_service.cpp
        src/ui/sidebar.cpp
        src/ui/orb.cpp
        src/ui/hud_widgets.cpp
        src/ui/shell_parts.cpp
        src/ui/hud_modal.cpp
        src/ui/hud_chat.cpp
        src/ui/floating_hud.cpp
        src/ui/news_stage.cpp
        src/ui/diagnostics_modal.cpp
        src/ui/home_modal.cpp
        src/ui/browser_service.cpp
        src/ui/browser_modal.cpp
        src/ui/settings_dialog.cpp
        src/ui/weather_modal.cpp
        src/ui/camera_modal.cpp
        src/ui/book_modal.cpp
        src/ui/main_window.cpp)
    target_include_directories(jarvis_ui PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(jarvis_ui PUBLIC
        jarvis_core
        jarvis_storage
        jarvis_exec
        jarvis_speech
        Qt6::Widgets
        Qt6::Network
        Qt6::Multimedia
        Qt6::MultimediaWidgets
        Qt6::WebChannel
        Qt6::WebEngineWidgets)
    if(JARVIS_ENABLE_NET)
        target_link_libraries(jarvis_ui PUBLIC jarvis_ai)
        target_compile_definitions(jarvis_ui PUBLIC JARVIS_ENABLE_NET=1)
    endif()
endif()

# ---------------------------------------------------------------------------
# Uygulama giriş noktası
# ---------------------------------------------------------------------------
add_executable(jarvis src/main.cpp)
if(JARVIS_ENABLE_UI)
    target_compile_definitions(jarvis PRIVATE JARVIS_ENABLE_UI=1)
    target_link_libraries(jarvis PRIVATE jarvis_ui)
    set_target_properties(jarvis PROPERTIES WIN32_EXECUTABLE TRUE)

    # Qt6 DLL'lerini ve platform/stil eklentilerini yürütülebilirin yanına kopyala.
    add_custom_command(TARGET jarvis POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_RUNTIME_DLLS:jarvis>
            $<TARGET_FILE_DIR:jarvis>
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_PLUGINS_DIR}/platforms"
            "$<TARGET_FILE_DIR:jarvis>/platforms"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_PLUGINS_DIR}/styles"
            "$<TARGET_FILE_DIR:jarvis>/styles"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_PLUGINS_DIR}/imageformats"
            "$<TARGET_FILE_DIR:jarvis>/imageformats"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_PLUGINS_DIR}/tls"
            "$<TARGET_FILE_DIR:jarvis>/tls"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_PLUGINS_DIR}/multimedia"
            "$<TARGET_FILE_DIR:jarvis>/multimedia"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${QT_BIN_DIR}/QtWebEngineProcess.exe"
            "$<TARGET_FILE_DIR:jarvis>/QtWebEngineProcess.exe"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_RESOURCES_DIR}"
            "$<TARGET_FILE_DIR:jarvis>/resources"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${QT_TRANSLATIONS_DIR}/qtwebengine_locales"
            "$<TARGET_FILE_DIR:jarvis>/qtwebengine_locales"
        COMMAND_EXPAND_LISTS)
else()
    target_link_libraries(jarvis PRIVATE jarvis_core)
endif()

# ---------------------------------------------------------------------------
# Testler
# ---------------------------------------------------------------------------
enable_testing()
add_executable(core_test tests/core_test.cpp)
target_link_libraries(core_test PRIVATE jarvis_core)
add_test(NAME core_test COMMAND core_test)

add_executable(storage_test tests/storage_test.cpp)
target_link_libraries(storage_test PRIVATE jarvis_storage)
add_test(NAME storage_test COMMAND storage_test)

# Güvenlik çekirdeği birim testi — salt karar mantığı, ağ/OS gerektirmez.
add_executable(safety_test tests/safety_test.cpp)
target_link_libraries(safety_test PRIVATE jarvis_core)
add_test(NAME safety_test COMMAND safety_test)

# Deterministik eylem planlayıcısı testi.
add_executable(agent_test tests/agent_test.cpp)
target_link_libraries(agent_test PRIVATE jarvis_core)
add_test(NAME agent_test COMMAND agent_test)

# Ses hattı kararları testi — Qt/OS/ağ gerektirmez, salt metin mantığı.
add_executable(speech_test tests/speech_test.cpp)
target_link_libraries(speech_test PRIVATE jarvis_speech)
add_test(NAME speech_test COMMAND speech_test)

# Masaüstü yürütücü testi — gerçek dosya işlemleri geçici klasörde, PowerShell
# komutu, geri alma ve güvenlik geçitleri. Güç/medya tuşlarına dokunmaz.
add_executable(executor_test tests/executor_test.cpp)
target_link_libraries(executor_test PRIVATE jarvis_exec)
add_test(NAME executor_test COMMAND executor_test)

# Pencere araçları testi — metin/süzme mantığı + salt okuma (list_windows).
# Hiçbir pencereyi kapatmaz veya taşımadan.
add_executable(window_tools_test tests/window_tools_test.cpp)
target_link_libraries(window_tools_test PRIVATE jarvis_exec)
add_test(NAME window_tools_test COMMAND window_tools_test)

# Uygulama başlatıcı testi — ad eşleştirme ve keşif zinciri sahte Başlat menüsü
# ve sahte winget ağacıyla çalışır; gerçek uygulama başlatmaz, PowerShell koşmaz.
add_executable(app_launcher_test tests/app_launcher_test.cpp)
target_link_libraries(app_launcher_test PRIVATE jarvis_exec)
add_test(NAME app_launcher_test COMMAND app_launcher_test)

# Arayüz otomasyonu testi — klavye ayrıştırıcı, desen dağıtım sırası (sahte
# UiTarget/InputSink ile), güvenli masaüstü ve parola geçitleri. Gerçek pencereyi
# kapatmaz; canlı inspect_ui yalnızca uygun pencere varsa koşar.
add_executable(ui_automation_test tests/ui_automation_test.cpp)
target_link_libraries(ui_automation_test PRIVATE jarvis_exec)
add_test(NAME ui_automation_test COMMAND ui_automation_test)

# Ekran araçları testi — OCR dil eşlemesi, eşleştirme/seçim sırası, ekran
# koordinatı çevirisi, WIC PNG turu ve görüntü örneklemesi. Ekranı yakmaz;
# canlı kısmı yalnızca screen_wait okumasıdır.
add_executable(screen_tools_test tests/screen_tools_test.cpp)
target_link_libraries(screen_tools_test PRIVATE jarvis_exec)
add_test(NAME screen_tools_test COMMAND screen_tools_test)

if(JARVIS_ENABLE_NET)
    add_executable(net_test tests/net_test.cpp)
    target_link_libraries(net_test PRIVATE jarvis_net)
    add_test(NAME net_test COMMAND net_test)

    # AI motoru birim testi — ağa çıkmadan salt mantığı doğrular.
    add_executable(ai_test tests/ai_test.cpp)
    target_link_libraries(ai_test PRIVATE jarvis_ai)
    add_test(NAME ai_test COMMAND ai_test)

    # Worker orkestrasyon testi — sahte yürütücüyle, ağa çıkmadan.
    add_executable(worker_test tests/worker_test.cpp)
    target_link_libraries(worker_test PRIVATE jarvis_ai)
    add_test(NAME worker_test COMMAND worker_test)

    # Canlı ağ doğrulaması; ctest'e dahil edilmez (ağ gerektirir).
    add_executable(net_smoke tests/net_smoke.cpp)
    target_link_libraries(net_smoke PRIVATE jarvis_net)

    # Uçtan uca ajan hattı teşhisi: gerçek ayarlarla AIWorker'ı konsolda yürütür.
    add_executable(agent_probe tests/agent_probe.cpp)
    target_link_libraries(agent_probe PRIVATE jarvis_ai)

    # Canlı STT ucu: C++ istek yükleyici + ayrıştırıcı gerçek uç noktaya yollar.
    add_executable(stt_probe tests/stt_probe.cpp)
    target_link_libraries(stt_probe PRIVATE jarvis_net jarvis_speech)

    # cpr/libcurl/zlib DLL'lerini yürütülebilirlerin yanına kopyala.
    foreach(net_target IN ITEMS net_test net_smoke ai_test worker_test agent_probe stt_probe)
        add_custom_command(TARGET ${net_target} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                $<TARGET_RUNTIME_DLLS:${net_target}>
                $<TARGET_FILE_DIR:${net_target}>
            COMMAND_EXPAND_LISTS)
    endforeach()
endif()

if(JARVIS_ENABLE_UI)
    # Canlı mikrofon ucu: MicService'ı gerçek aygıtta çalıştırır, seviye/cümle/hata
    # olaylarını konsola yazar. ctest'e dahil değil (aygıt ve ortam sesi ister).
    add_executable(mic_probe tests/mic_probe.cpp)
    target_link_libraries(mic_probe PRIVATE jarvis_ui)
    add_custom_command(TARGET mic_probe POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_RUNTIME_DLLS:mic_probe>
            $<TARGET_FILE_DIR:mic_probe>
        COMMAND_EXPAND_LISTS)
endif()
