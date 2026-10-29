// DesktopExecutor birim testi — dosya araçları geçici klasörde gerçek çalışır,
// geri alma turu, güvenlik geçitleri ve run_command (PowerShell) doğrulanır.
// Bilinçli olarak güç/medya tuşlarına ve Geri Dönüşüm Kutusuna dokunmaz.
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include "jarvis/ai/executor.hpp"
#include "jarvis/core/home.hpp"
#include "jarvis/core/skill_library.hpp"
#include "jarvis/storage/storage.hpp"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

namespace fs = std::filesystem;
using jarvis::ai::ActionPlan;
using jarvis::ai::DesktopExecutor;
using jarvis::ai::KillSwitch;
using jarvis::ai::PathPolicy;
using jarvis::ai::PermissionGate;
using jarvis::ai::PlannedAction;
using jarvis::ai::Risk;
using jarvis::ai::SafetyServices;
using jarvis::ai::UndoStore;
using jarvis::core::SkillLibrary;
using jarvis::home::SmartHome;
using jarvis::storage::Storage;
using nlohmann::json;

namespace {

std::string u8(const fs::path& path) {
    std::u8string raw = path.u8string();
    std::string out;
    out.reserve(raw.size());
    for (char8_t c : raw) out.push_back(static_cast<char>(c));
    return out;
}

bool has(const std::string& hay, const std::string& needle) {
    return hay.find(needle) != std::string::npos;
}

std::string read_file(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    return std::string{std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

PlannedAction act(const std::string& type, json arguments, const std::string& description = {}) {
    PlannedAction action;
    action.action_type = type;
    action.arguments = std::move(arguments);
    action.description = description.empty() ? type : description;
    return action;
}

}  // namespace

int main() {
    const fs::path root = fs::temp_directory_path() /
                          ("jarvis_exec_test_" +
                           std::to_string(
                               std::chrono::steady_clock::now().time_since_epoch().count()));
    std::error_code ec;
    fs::create_directories(root, ec);
    if (ec) {
        std::printf("FAIL test kok klasoru olusamadi\n");
        return 1;
    }

    KillSwitch kill;
    SafetyServices services;
    services.policy = PathPolicy({root}, {{root / "korumali", "test korumali alan"}}, root);
    services.gate = PermissionGate([] { return std::string("full"); }, [] { return false; });
    services.audit = jarvis::ai::AuditLog(root / "logs" / "audit.jsonl");
    services.undo = std::make_shared<UndoStore>(root / "undo");
    services.kill = &kill;

    Storage storage{u8(root / "jarvis_test.db")};
    SkillLibrary skills{root / "skills", true};
    SmartHome home;
    DesktopExecutor executor(services, storage, skills, home);

    // --- dosya yazma + üzerine yazma + geri alma turu ---
    const fs::path note = root / "notlar" / "deneme.txt";
    std::string result = executor.execute(act("write_text_file", {{"path", u8(note)},
                                                                  {"content", "birinci satir"}}));
    CHECK(has(result, "✅ Dosya yazıldı:"));
    CHECK(read_file(note) == "birinci satir");

    result = executor.execute(act("write_text_file", {{"path", u8(note)}, {"content", "ikinci"}}));
    CHECK(has(result, "❌ Dosya zaten var, üzerine yazılmadı:"));
    CHECK(read_file(note) == "birinci satir");

    result = executor.execute(act("write_text_file", {{"path", u8(note)},
                                                      {"content", "ikinci"},
                                                      {"overwrite", true}}));
    CHECK(has(result, "✅ Dosya yazıldı:"));
    CHECK(read_file(note) == "ikinci");

    result = executor.execute(act("undo_last", json::object()));
    CHECK(has(result, "✅ Son değişiklik geri alındı"));
    CHECK(read_file(note) == "birinci satir");

    // Olusturulan dosyada eksik ust klasorler de geri alinir.
    const fs::path deep = root / "derin" / "alt" / "dosya.txt";
    CHECK(has(executor.execute(act("write_text_file", {{"path", u8(deep)}, {"content", "x"}})),
              "✅ Dosya yazıldı:"));
    CHECK(fs::exists(deep));
    CHECK(has(executor.execute(act("undo_last", json::object())), "✅ Son değişiklik geri alındı"));
    CHECK(!fs::exists(deep));
    CHECK(!fs::exists(root / "derin"));

    // --- klasor, kopyalama, tasiama ---
    const fs::path folder = root / "klasor";
    CHECK(has(executor.execute(act("create_folder", {{"path", u8(folder)}})),
              "✅ Klasör oluşturuldu:"));
    CHECK(fs::is_directory(folder));
    CHECK(has(executor.execute(act("create_folder", {{"path", u8(folder)}})),
              "❌ Hedef zaten var:"));

    const fs::path copied = folder / "kopya.txt";
    CHECK(has(executor.execute(act("copy_path", {{"source", u8(note)}, {"destination", u8(copied)}})),
              "✅ Kopyalandı:"));
    CHECK(fs::exists(copied));
    CHECK(has(executor.execute(act("copy_path", {{"source", u8(note)}, {"destination", u8(copied)}})),
              "❌ Hedef zaten var, üzerine yazılmadı:"));

    const fs::path moved = folder / "tasindi.txt";
    CHECK(has(executor.execute(act("move_path",
                                   {{"source", u8(copied)}, {"destination", u8(moved)}})),
              "✅ Taşındı:"));
    CHECK(fs::exists(moved));
    CHECK(!fs::exists(copied));

    // klasore yazilamaz / olmayan kaynak tasinamaz
    CHECK(has(executor.execute(act("write_text_file", {{"path", u8(folder)}, {"content", "x"}})),
              "❌ Bu bir klasör, dosya yazılamaz:"));
    CHECK(has(executor.execute(act("copy_path",
                                   {{"source", u8(root / "yok.txt")},
                                    {"destination", u8(root / "hedef.txt")}})),
              "öğe bulunamadı:"));

    // --- guvenlik gecitleri ---
    const std::string disari = "C:/Windows/System32/icermik.txt";
    CHECK(has(executor.execute(act("write_text_file", {{"path", disari}, {"content", "x"}})), "❌"));
    CHECK(!fs::exists(disari));

    ActionPlan disari_plan;
    disari_plan.actions = {act("write_text_file", {{"path", disari}, {"content", "x"}})};
    CHECK(executor.assess(disari_plan, false).decision == "deny");

    CHECK(has(executor.execute(act("write_text_file",
                                   {{"path", u8(root / "korumali" / "gizli.txt")},
                                    {"content", "x"}})),
              "❌"));
    CHECK(!fs::exists(root / "korumali" / "gizli.txt"));

    // --- bağlı olmayan araç ve yeni bağlanan pencere/uygulama araçları ---
    CHECK(has(executor.execute(act("screen_map", json::object())), "henüz bağlı değil"));
    // window_control artık gerçek Win32 okuması yapar; yalnızca salt listeleme denenir.
    const json windows = json::parse(
        executor.execute(act("window_control", {{"action", "list"}, {"limit", 3}})));
    CHECK(windows.contains("windows"));
    CHECK(windows["count"].get<long long>() <= 3);
    // Eksik ya da yanlış hedefli açılışlar hiçbir süreç başlatmadan reddedilir.
    CHECK(has(executor.execute(act("open_application", json::object())),
              "open_application için application gerekli."));
    CHECK(has(executor.execute(act("open_web_page", {{"application", "Chrome"}})),
              "open_web_page için application ve url gerekli."));
    CHECK(has(executor.execute(act("open_web_page",
                                   {{"application", "Chrome"}, {"url", "file:///C:/x.txt"}})),
              "Yalnızca http/https"));

    // --- run_command (PowerShell) ---
    result = executor.execute(act("run_command",
                                  {{"language", "powershell"},
                                   {"code", "Write-Output 'merhaba-cpp'"},
                                   {"working_directory", u8(root)}}));
    CHECK(has(result, "✅ Komut çıkış kodu: 0"));
    CHECK(has(result, "merhaba-cpp"));

    result = executor.execute(act("run_command",
                                  {{"language", "powershell"},
                                   {"code", "exit 3"},
                                   {"working_directory", u8(root)}}));
    CHECK(has(result, "❌ Komut çıkış kodu: 3"));

    result = executor.execute(act("run_command",
                                  {{"language", "powershell"},
                                   {"code", "Start-Sleep -Seconds 20"},
                                   {"timeout_seconds", 1},
                                   {"working_directory", u8(root)}}));
    CHECK(has(result, "saniyede zaman aşımına uğradı"));

    // Yasak komut kalibi ve korumali alan yolu: surec baslatilmadan reddedilir.
    CHECK(has(executor.execute(act("run_command",
                                   {{"language", "powershell"}, {"code", "regedit /s deneme.reg"},
                                    {"working_directory", u8(root)}})),
              "kayıt defteri"));
    CHECK(has(executor.execute(act("run_command",
                                   {{"language", "powershell"},
                                    {"code",
                                     "Get-Content '" + u8(root / "korumali" / "x.txt") + "'"},
                                    {"working_directory", u8(root)}})),
              "korumalı alanına"));
    CHECK(has(executor.execute(act("run_command",
                                   {{"language", "powershell"},
                                    {"code", "pyautogui.click(1, 2)"},
                                    {"working_directory", u8(root)}})),
              "otomasyonu"));
    CHECK(has(executor.execute(act("run_command", {{"language", "batch"}, {"code", "dir"}})), "❌"));

    // Durdur dendigi komut calismaz
    kill.request();
    CHECK(has(executor.execute(act("run_command",
                                   {{"language", "powershell"},
                                    {"code", "Write-Output 'x'"},
                                    {"working_directory", u8(root)}})),
              "Durdur düğmesiyle iptal edildi"));
    kill.clear();

    // --- plan calistirma + ilerleme + hata where durma ---
    ActionPlan plan;
    plan.actions = {act("create_folder", {{"path", u8(root / "plan1")}}, "klasor 1"),
                    act("write_text_file",
                        {{"path", u8(root / "plan1" / "a.txt")}, {"content", "merhaba"}},
                        "dosya yaz"),
                    act("write_text_file", {{"path", u8(root / "korumali" / "b.txt")},
                                            {"content", "merhaba"}},
                        "korumali alana yaz")};
    std::vector<std::string> progress;
    const std::vector<std::string> results = executor.execute_plan(
        plan, [&progress](const std::string& line) { progress.push_back(line); });
    CHECK(results.size() == 3);
    CHECK(has(results[0], "✅ Klasör oluşturuldu:"));
    CHECK(has(results[1], "✅ Dosya yazıldı:"));
    CHECK(has(results[2], "❌"));
    CHECK(progress.size() == 3);
    CHECK(progress[0].rfind("1/3:", 0) == 0);
    CHECK(progress[2].rfind("3/3:", 0) == 0);

    kill.request();
    const std::vector<std::string> stopped =
        executor.execute_plan(ActionPlan{{act("create_folder", {{"path", u8(root / "plan2")}}, "x"),
                                          act("create_folder", {{"path", u8(root / "plan3")}}, "y")}},
                              {});
    kill.clear();
    CHECK(stopped.size() == 1);
    CHECK(has(stopped[0], "Durduruldu: kalan adımlar yapılmadı"));
    CHECK(!fs::exists(root / "plan3"));

    // --- kalici hafiza ---
    CHECK(has(executor.execute(act("memory", {{"action", "list"}})), "not yok."));
    CHECK(has(executor.execute(act("memory", {{"action", "add"},
                                              {"key", "deneme"},
                                              {"value", "değer 1"}})),
              "✅ Kalıcı hafızaya kaydedildi:"));
    CHECK(has(executor.execute(act("memory", {{"action", "list"}})), "deneme"));
    CHECK(has(executor.execute(act("memory", {{"action", "delete"}, {"key", "deneme"}})),
              "✅ Kalıcı not silindi:"));
    CHECK(has(executor.execute(act("memory", {{"action", "add"}, {"value", ""}})), "❌"));

    // --- yetenek kütüphanesi ---
    CHECK(has(executor.execute(act("skill", {{"action", "list"}})), "✅"));
    CHECK(has(executor.execute(act("skill", {{"action", "read"}})), "Okunacak yetenek için"));

    // --- akilli ev (simülasyon) ---
    CHECK(has(executor.execute(act("home", {{"action", "status"}})), "Akıllı ev durumu"));
    CHECK(has(executor.execute(act("home", {{"action", "light"},
                                            {"room", "salon"},
                                            {"on", true},
                                            {"brightness", 80},
                                            {"tone", "soguk"}})),
              "simülasyon"));
    CHECK(has(executor.execute(act("home", {{"action", "light"}, {"room", "salon"}})),
          "Işık için on"));
    CHECK(has(executor.execute(act("home", {{"action", "coffee_brew"},
                                            {"kind", "turk"},
                                            {"foam", "bol"},
                                            {"sugar", "az"}})),
              "başladı"));

    // --- pano okuma (kullanici panosuna dokunmadan) ---
    const std::string clip = executor.execute(act("clipboard", {{"action", "read"}}));
    CHECK(has(clip, "✅ Pano içeriği") || has(clip, "ℹ️ Pano boş."));

    // --- denetim günlüğü kaydi ---
    const std::vector<json> audit = services.audit.tail(5);
    CHECK(audit.size() >= 5);
    CHECK(audit.front().contains("tool"));

    fs::remove_all(root, ec);
    std::printf("executor_test: %d hata\n", failures);
    return failures == 0 ? 0 : 1;
}
