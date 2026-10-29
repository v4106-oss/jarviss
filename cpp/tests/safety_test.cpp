// Güvenlik çekirdeği birim testi — salt karar mantığı (ağ/OS gerektirmez).
#include <chrono>
#include <cstdio>
#include <exception>
#include <filesystem>
#include <functional>
#include <string>

#include "jarvis/ai/safety.hpp"

static int failures = 0;
#define CHECK(cond)                                                         \
    do {                                                                    \
        if (!(cond)) {                                                      \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);     \
            ++failures;                                                     \
        }                                                                   \
    } while (0)

using namespace jarvis::ai;

namespace {
PermissionGate gate(const std::string& mode, bool strict = true) {
    return PermissionGate([mode]() { return mode; }, [strict]() { return strict; });
}

bool throws_safety(const std::function<Risk()>& f) {
    try {
        f();
        return false;
    } catch (const SafetyError&) {
        return true;
    }
}
}  // namespace

static void run_checks() {
    // ---- PermissionGate ----
    CHECK(gate("approval").decide(Risk::Read).action == "allow");
    CHECK(gate("approval").decide(Risk::Create).action == "allow");
    CHECK(gate("approval").decide(Risk::Modify).action == "ask");
    CHECK(gate("approval").decide(Risk::Dangerous).action == "ask");
    CHECK(gate("approval").decide(Risk::Forbidden).action == "deny");
    // tainted => create bile sorar
    CHECK(gate("approval").decide(Risk::Create, true).action == "ask");
    // step modu: read hariç her şey sorar
    CHECK(gate("step").decide(Risk::Read).action == "allow");
    CHECK(gate("step").decide(Risk::Create).action == "ask");
    // full modu: allow, ama modify+tainted+strict => ask
    CHECK(gate("full").decide(Risk::Dangerous).action == "allow");
    CHECK(gate("full").decide(Risk::Modify, true).action == "ask");
    CHECK(gate("full", false).decide(Risk::Modify, true).action == "allow");
    CHECK(gate("full").decide(Risk::Forbidden).action == "deny");
    // bilinmeyen mod approval'a normalize edilir
    CHECK(gate("bozuk").decide(Risk::Modify).action == "ask");

    // ---- Risk etiketleri / AccessMode normalize ----
    CHECK(std::string(risk_label(Risk::Dangerous)) == "riskli işlem");
    CHECK(access_mode::normalize("step") == "step");
    CHECK(access_mode::normalize("???") == "approval");

    // ---- KillSwitch ----
    KillSwitch ks;
    CHECK(!ks.is_set());
    ks.request();
    CHECK(ks.is_set());
    ks.clear();
    CHECK(!ks.is_set());

    // ---- PathPolicy ----
    namespace fs = std::filesystem;
    auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    fs::path root = fs::temp_directory_path() / ("jarvis_safety_test_" + std::to_string(stamp));
    fs::create_directories(root);
    PathPolicy policy({root}, {});

    CHECK(is_within(root / "a" / "b.txt", root));
    CHECK(is_within(root, root));
    CHECK(!is_within(fs::path("C:/Windows/notepad.exe"), root));
    CHECK(policy.is_executable(root / "x.exe"));
    CHECK(!policy.is_executable(root / "x.txt"));

    // güvenli yol kabul edilir
    fs::path good = policy.resolve((root / "not.txt").string());
    CHECK(!good.empty());

    // reddedilenler
    bool rejected = false;
    try { policy.resolve("\\\\server\\share"); } catch (const PathNotAllowed&) { rejected = true; }
    CHECK(rejected);
    rejected = false;
    try { policy.resolve((root / "a:b.txt").string()); } catch (const PathNotAllowed&) { rejected = true; }
    CHECK(rejected);
    rejected = false;
    try { policy.resolve("/etc/passwd"); } catch (const PathNotAllowed&) { rejected = true; }  // kök dışı
    CHECK(rejected);
    rejected = false;
    try { policy.resolve((root / "id_rsa").string()); } catch (const PathNotAllowed&) { rejected = true; }
    CHECK(rejected);

    // ---- classify_action ----
    nlohmann::json args;
    fs::path newfile = root / "yeni.txt";
    fs::path exefile = root / "app.exe";
    args = {{"path", newfile.string()}};
    CHECK(classify_action("create_folder", {{"path", (root / "klasor").string()}}, policy) == Risk::Create);
    CHECK(classify_action("write_text_file", args, policy) == Risk::Create);  // yok => Create
    CHECK(classify_action("open_path", {{"path", newfile.string()}}, policy) == Risk::Create);
    CHECK(throws_safety([&] { return classify_action("open_path", {{"path", exefile.string()}}, policy); }));
    // silme: kök klasörü korumalı
    CHECK(throws_safety([&] { return classify_action("delete_path", {{"path", root.string()}}, policy); }));
    CHECK(classify_action("delete_path", args, policy) == Risk::Dangerous);
    // read kümesi
    CHECK(classify_action("read_page", {}, policy) == Risk::Read);
    CHECK(classify_action("weather", {}, policy) == Risk::Read);
    // memory/skill/personal dalları
    CHECK(classify_action("memory", {{"action", "list"}}, policy) == Risk::Read);
    CHECK(classify_action("memory", {{"action", "add"}}, policy) == Risk::Create);
    CHECK(classify_action("skill", {{"action", "create"}}, policy) == Risk::Modify);
    CHECK(throws_safety([&] { return classify_action("memory", {{"action", "bogus"}}, policy); }));
    CHECK(classify_action("personal", {{"resource", "note"}, {"action", "add"}}, policy) == Risk::Create);
    CHECK(throws_safety(
        [&] { return classify_action("personal", {{"resource", "note"}, {"action", "complete"}}, policy); }));
    // system_control confirm
    CHECK(throws_safety([&] { return classify_action("system_control", {{"action", "shutdown"}}, policy); }));
    CHECK(classify_action("system_control", {{"action", "shutdown"}, {"confirm", true}}, policy) == Risk::Dangerous);
    CHECK(classify_action("system_control", {{"action", "volume_up"}}, policy) == Risk::Create);
    // window_control
    CHECK(classify_action("window_control", {{"action", "list"}}, policy) == Risk::Read);
    CHECK(throws_safety([&] { return classify_action("window_control", {{"action", "close"}}, policy); }));  // title yok
    CHECK(classify_action("window_control", {{"action", "close"}, {"title", "Not Defteri"}}, policy) == Risk::Dangerous);
    // browser_open
    CHECK(classify_action("browser_open", {{"url", "https://example.com"}}, policy) == Risk::Create);
    CHECK(throws_safety([&] { return classify_action("browser_open", {{"url", "ftp://x"}}, policy); }));
    // ekran araçları
    CHECK(classify_action("find_text_on_screen", {{"click", true}}, policy) == Risk::Dangerous);
    CHECK(classify_action("screen_act", {{"ref", "s1:e2"}, {"action", "click"}}, policy) == Risk::Dangerous);
    CHECK(throws_safety([&] { return classify_action("screen_act", {{"ref", "kötü"}}, policy); }));
    // eksik yol
    CHECK(throws_safety([&] { return classify_action("write_text_file", {}, policy); }));
    // tanınmayan
    CHECK(throws_safety([&] { return classify_action("uzay_araqi", {}, policy); }));

    // ---- AuditLog ----
    AuditLog audit(root / "logs" / "audit.jsonl");
    audit.record("write_text_file", {{"path", "C:/x"}, {"content", "gizli metin"}, {"api_key", "SECRET"}});
    auto tail = audit.tail(1);
    CHECK(tail.size() == 1);
    if (!tail.empty()) {
        const nlohmann::json& e = tail[0];
        CHECK(e["tool"] == "write_text_file");
        CHECK(e["args"]["api_key"] == "***");               // hassas anahtar maskelendi
        CHECK(e["args"]["content"].contains("chars"));      // içerik uzunluğa indirgendi
    }

    fs::remove_all(root);
}

int main() {
    try {
        run_checks();
    } catch (const std::exception& e) {
        std::printf("FAIL beklenmeyen istisna: %s\n", e.what());
        ++failures;
    } catch (...) {
        std::printf("FAIL beklenmeyen istisna (bilinmeyen tür)\n");
        ++failures;
    }

    if (failures == 0) {
        std::printf("safety_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("safety_test: %d HATA\n", failures);
    return 1;
}
