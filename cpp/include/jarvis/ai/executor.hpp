#pragma once

// DesktopExecutor — Python app/core/agent.py ActionExecutor'ın işletim sistemi
// yerel alt kümesinin C++20 karşılığı.
//
// Bu katman dosya/geri-alma/komut/pano/sistem/hafıza/yetenek/akıllı ev araçlarını
// doğrudan Windows API'siyle yürütür. Pencere yönetimi, uygulama başlatma ve
// arayüz otomasyonu (inspect_ui, ui_control, send_keys) buraya bağlıdır
// (tools/window_tools, tools/app_launcher, tools/ui_automation). Ekran yakalama,
// OCR ile metin bulma, bekleme ve görsel inceleme (capture_screen,
// find_text_on_screen, screen_wait, inspect_image) tools/screen_capture +
// tools/ocr üzerinden bağlıdır.
//
// Yakalanan görüntü yürütücüde kalmaz: set_screenshot_sink ile verilen geri çağrma
// yoluyla modelin sonraki adımına teslim edilir. Yürütücü jarvis_ai'ya bağımlı
// değildir, yalnızca std::function bilir; köprüyü kuran AIWorker'dır.
//
// screen_map, screen_act, mouse_control ve draw_shape henüz port edilmedi; model
// onlar için dürüst bir "bağlı değil" yanıtı alır ve döngü çökmeden devam eder.
//
// Uygulama içi tarayıcı (QtWebEngine) browser_listener kancasıyla devredilir;
// kanca yoksa bağlantılar varsayılan tarayıcıya açılır.
//
// Ağ servisleri (haber, hava, wiki, GitHub, harita, kitap…) bu sınıfta değildir;
// jarvis_net üzerinden ayrı yürütücülerle bağlanır.

#include <cstddef>
#include <filesystem>
#include <functional>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "jarvis/ai/action_executor.hpp"
#include "jarvis/ai/safety.hpp"
#include "jarvis/ai/undo.hpp"
#include "jarvis/core/home.hpp"
#include "jarvis/core/skill_library.hpp"
#include "jarvis/storage/storage.hpp"
#include "jarvis/tools/app_launcher.hpp"
#include "jarvis/tools/ui_automation.hpp"

namespace jarvis::ai {

class DesktopExecutor : public IActionExecutor {
public:
    DesktopExecutor(SafetyServices& services, storage::Storage& storage, core::SkillLibrary& skills,
                    home::SmartHome& home);

    // Uygulama içi tarayıcı (QtWebEngine) açma kancağı; yoksa bağlantılar
    // varsayılan tarayıcıya teslim edilir.
    using BrowserListener = std::function<void(const std::string& url)>;

    // Modelin görebilmesi için bir PNG yolunun teslim ediliş biçimi.
    using ScreenshotSink = std::function<void(const std::string& path)>;

    void set_browser_listener(BrowserListener listener) {
        browser_listener_ = std::move(listener);
    }

    // Görüntü köprüsü: capture_screen/find_text_on_screen/inspect_image tarafından
    // üretilen PNG yolu modelin sonraki adımına buradan verilir.
    void set_screenshot_sink(ScreenshotSink sink) override {
        screenshot_sink_ = std::move(sink);
    }

    PlanAssessment assess(const ActionPlan& plan, bool tainted) override;
    std::string execute(const PlannedAction& action) override;
    std::vector<std::string> execute_plan(
        const ActionPlan& plan, const std::function<void(const std::string&)>& on_progress) override;

    // Yol çözümleme ve yazma sınırları testlerden de erişilebilsin diye dışa açık.
    static constexpr std::size_t kMaxWriteChars = 5000000;

private:
    std::string run(const PlannedAction& action);

    // --- dosya araçları (ChangeSet ile geri alınabilir) ---
    std::string create_folder(const nlohmann::json& args);
    std::string write_or_append(const std::string& kind, const nlohmann::json& args);
    std::string copy_or_move(const std::string& kind, const nlohmann::json& args);
    std::string delete_path(const nlohmann::json& args);
    std::string open_path(const nlohmann::json& args);
    std::string undo_last();

    // --- çalıştırma ve açılış ---
    std::string run_command(const nlohmann::json& args);
    std::string open_system_uri(const nlohmann::json& args);
    std::string open_link(const std::string& tool, const nlohmann::json& args);

    // --- pencere ve uygulama araçları (Win32/COM) ---
    std::string open_application(const nlohmann::json& args);
    std::string open_web_page(const nlohmann::json& args);
    std::string window_control(const nlohmann::json& args);

    // --- arayüz otomasyonu (UIA + SendInput) ---
    std::string inspect_ui(const nlohmann::json& args);
    std::string ui_control(const nlohmann::json& args);
    std::string send_keys(const nlohmann::json& args);

    // --- ekran ve görüntü araçları (GDI + WIC + WinRT OCR) ---
    std::string capture_screen(const nlohmann::json& args);
    std::string find_text_on_screen(const nlohmann::json& args);
    std::string screen_wait(const nlohmann::json& args);
    std::string inspect_image(const nlohmann::json& args);

    // --- masaüstü servisleri ---
    std::string clipboard(const nlohmann::json& args);
    std::string system_control(const nlohmann::json& args);
    std::string memory(const nlohmann::json& args);
    std::string skill(const nlohmann::json& args);
    std::string home_tool(const nlohmann::json& args);

    // browser_open/search_web gibi bağlantı araçları: kanca yoksa varsayılan tarayıcı.
    bool open_in_browser(const std::string& url, std::string& message, const std::string& label);

    SafetyServices& services_;
    storage::Storage& storage_;
    core::SkillLibrary& skills_;
    home::SmartHome& home_;
    tools::AppLauncher applications_;
    tools::UiAutomation ui_automation_;
    BrowserListener browser_listener_;
    ScreenshotSink screenshot_sink_;
};

// Eksik üst klasörleri oluşturur ve geri alınabilmeleri için değiştirme setine yazar.
void make_parents(const std::filesystem::path& directory, ChangeSet& changes);

}  // namespace jarvis::ai
