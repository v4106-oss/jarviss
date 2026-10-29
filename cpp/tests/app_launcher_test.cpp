// AppLauncher birim testi — ad eşleştirme/keşif mantığı sahte Başlat menüsü ve
// sahte winget ağacıyla doğrulanır. Gerçek uygulama başlatılmaz, kayıt defterine
// veya PowerShell'e dokunulmaz.
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <functional>
#include <random>
#include <string>
#include <vector>

#include "jarvis/ai/safety.hpp"
#include "jarvis/tools/app_launcher.hpp"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

namespace fs = std::filesystem;
using jarvis::ai::ActionForbidden;
using jarvis::tools::AppLauncher;

static void touch(const fs::path& path) {
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    out << "test";
}

static void expect_forbidden(const std::function<void()>& call, const std::string& fragment) {
    try {
        call();
        std::printf("FAIL beklenen red gelmedi: %s\n", fragment.c_str());
        ++failures;
    } catch (const ActionForbidden& error) {
        const std::string message = error.what();
        if (message.find(fragment) == std::string::npos) {
            std::printf("FAIL red metninde '%s' yok: %s\n", fragment.c_str(), message.c_str());
            ++failures;
        }
    }
}

int main() {
    // --- normalize / skor ---
    // Python: tire boşluğa döner, kenar ve ardışık boşluklar temizlenir.
    CHECK(AppLauncher::normalize("  Micro-Soft   Edge ") == "micro soft edge");
    CHECK(AppLauncher::normalize("HESAP  MAKİNESİ") == "hesap makinesi");
    CHECK(AppLauncher::score("notepad", "notepad") == 1.0);
    CHECK(AppLauncher::score("note", "Notepad") == 0.9);
    CHECK(AppLauncher::score("chrome", "Google Chrome") == 0.85);
    // İlk kelime eşleşmeyen benzer son ek yeterli değil (Python'ın OBS düzeltmesi).
    CHECK(AppLauncher::score("obs studio", "Roblox Studio") == 0.0);
    CHECK(AppLauncher::similarity("abc", "abc") == 1.0);
    CHECK(AppLauncher::similarity("abc", "") == 0.0);

    // --- kelime damarları ---
    const std::vector<std::string> edge_tokens = AppLauncher::app_tokens("Microsoft Edge");
    CHECK(edge_tokens.size() == 1 && edge_tokens[0] == "edge");
    CHECK(AppLauncher::tokens("Çöz-Um 2").size() == 3);
    CHECK(AppLauncher::window_matches("chrome", "Google Chrome - Yeni sekme"));
    CHECK(AppLauncher::window_matches("Microsoft Edge", "edge"));
    CHECK(!AppLauncher::window_matches("Visual Studio Code", "Code"));
    CHECK(!AppLauncher::window_matches("notepad", "Not Defteri"));

    // --- etiketler veSYSTEM_APPS ---
    CHECK(AppLauncher::is_play_label("Oyna"));
    CHECK(AppLauncher::is_play_label("Play Now"));
    CHECK(!AppLauncher::is_play_label("Kaydet"));
    CHECK(AppLauncher::is_ignored_shortcut("obs uninstall"));
    CHECK(!AppLauncher::is_ignored_shortcut("obs studio"));
    CHECK(AppLauncher::system_command("Not Defteri") == "notepad.exe");
    CHECK(AppLauncher::system_command("Hesap Makinesi") == "calc.exe");
    CHECK(AppLauncher::system_command("Chrome").empty());

    // --- ad doğrulama ---
    CHECK(AppLauncher::validate("  Discord ") == "Discord");
    expect_forbidden([] { AppLauncher::validate("  "); }, "Geçerli bir uygulama adı gerekli.");
    expect_forbidden([] { AppLauncher::validate("C:\\Windows\\notepad.exe"); },
                     "Uygulama yolu yerine yalnızca uygulama adı kullanılmalı.");
    expect_forbidden([] { AppLauncher::validate("Not Defteri.lnk"); },
                     "Uygulama yolu yerine yalnızca uygulama adı kullanılmalı.");

    // --- sahte Başlat menüsü: kısayol keşfi ---
    std::random_device entropy;
    const fs::path root = fs::temp_directory_path() /
                          ("jarvis_launcher_test_" + std::to_string(entropy()));
    std::error_code cleanup;
    fs::remove_all(root, cleanup);
    const fs::path menu = root / "Programs";
    touch(menu / "Google Chrome.lnk");
    touch(menu / "Not Defteri.lnk");
    touch(menu / "Alt Klasör" / "OBS Studio.lnk");
    touch(menu / "OBS Studio Uninstall.lnk");
    touch(menu / "Visual Studio Code.lnk");
    touch(menu / "Visual Studio Installer.lnk");

    const AppLauncher launcher({menu}, root / "winget");
    CHECK(launcher.find_shortcut("chrome").has_value());
    CHECK(launcher.find_shortcut("chrome")->filename() == "Google Chrome.lnk");
    CHECK(launcher.find_shortcut("not defteri").has_value());
    CHECK(!launcher.find_shortcut("obs").has_value() ||
          launcher.find_shortcut("obs")->filename() == "OBS Studio.lnk");
    CHECK(!launcher.find_shortcut("yok boyle bir sey").has_value());
    // Belirsizlik kırmızı çizgi: iki yakın adayda model yönlendirilmeli.
    expect_forbidden([&] { launcher.find_shortcut("visual studio"); }, "Uygulama adı belirsiz");
    // "Uninstall" kısayolu aday olamaz; ön ek eşleşmesi gerçek kısayola gider.
    CHECK(launcher.find_shortcut("obs studio uninstall")->filename() == "OBS Studio.lnk");
    CHECK(!launcher.find_shortcut("uninstall").has_value());
    // "studio" hem sahte kısayollarda hem kayıtlı uygulama adlarında geçer;
    // nearest_candidates gerçek kayıt defterini de taradığı için yalnız sıra
    // sınırı ve boş olmama garantilenir.
    const std::vector<std::string> near = launcher.nearest_candidates("studio", 3);
    CHECK(!near.empty());
    CHECK(near.size() <= 3);
    // Sahte .lnk gerçek bir bağlantı değil: hedef okunamaz ama çökmez.
    CHECK(AppLauncher::shortcut_target(menu / "Google Chrome.lnk").empty());

    // --- sahte winget ağacı ---
    const fs::path package = root / "winget" / "Microsoft.VisualStudioCode_8wekyb3d8bbwe";
    touch(package / "bin" / "x64" / "Code.exe");
    touch(package / "bin" / "x64" / "unins000.exe");
    AppLauncher winget_launcher({menu}, root / "winget");
    const auto found = winget_launcher.find_winget_app("visual studio code");
    CHECK(found.has_value());
    if (found) {
        CHECK(found->second == "visualstudiocode");
        CHECK(found->first.find("Code.exe") != std::string::npos);
        CHECK(found->first.find("unins000.exe") == std::string::npos);
    }
    CHECK(!winget_launcher.find_winget_app("ci").has_value());  // kısaltma 3 harften kısa
    CHECK(!winget_launcher.find_winget_app("yok boyle bir sey").has_value());
    // Açılış yolu doğrulanamayan bir ad: dürüst red, sahte başarı yok.
    expect_forbidden([&] { winget_launcher.open_url("Chrome", "ftp://example.com/x"); },
                     "Yalnızca http/https adresleri açılabilir.");
    expect_forbidden([&] { winget_launcher.open_url("", "https://example.com"); },
                     "Geçerli bir uygulama adı gerekli.");

    fs::remove_all(root, cleanup);
    std::printf(failures ? "app_launcher_test: %d BAŞARISIZ\n" : "app_launcher_test: tamam\n",
                failures);
    return failures ? 1 : 0;
}
