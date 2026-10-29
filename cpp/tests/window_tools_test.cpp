// WindowTools birim testi — salt metin/süzme mantığı masaüstünden bağımsız
// doğrulanır; canlı kısımda yalnızca okuma (list_windows) yapılır. Pencere
// kapatma/taşıma gibi gerçek etkiler test edilmez.
#include <cstdio>
#include <string>
#include <vector>

#include "jarvis/ai/safety.hpp"
#include "jarvis/tools/window_tools.hpp"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

using jarvis::ai::ActionForbidden;
using jarvis::tools::WindowInfo;
using jarvis::tools::WindowTools;
using nlohmann::json;

static void expect_forbidden(const json& args, const std::string& fragment) {
    try {
        const std::string result = WindowTools().run(args);
        std::printf("FAIL beklenen red gelmedi: %s\n", result.c_str());
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
    // --- normalize_text (Python screen_agent.normalize_text) ---
    CHECK(jarvis::tools::normalize_text("İ  I   ı") == "i i i");
    CHECK(jarvis::tools::normalize_text("  École   Café  ") == "ecole cafe");
    CHECK(jarvis::tools::normalize_text("Not Defteri — Taslak") == "not defteri — taslak");
    CHECK(jarvis::tools::normalize_text("HESAP\tMAKİNESİ") == "hesap makinesi");
    CHECK(jarvis::tools::normalize_text("  Not Defteri \t \n") == "not defteri");
    CHECK(jarvis::tools::code_points("Éa😀").size() == 3);

    // --- eylem takma adları ve kritik süreçler ---
    CHECK(jarvis::tools::resolve_action("  List_Windows ") == "list");
    CHECK(jarvis::tools::resolve_action("close_app") == "close");
    CHECK(!jarvis::tools::resolve_action("kill_process").has_value());
    CHECK(jarvis::tools::is_critical_process("LSASS.EXE"));
    CHECK(!jarvis::tools::is_critical_process("notepad.exe"));

    // --- güvenli masaüstü / güvenlik gerekçesi ---
    CHECK(jarvis::tools::secure_desktop_reason("Herhangi", "consent.exe").has_value());
    CHECK(!jarvis::tools::secure_desktop_reason("Herhangi", "notepad.exe").has_value());
    CHECK(jarvis::tools::secure_desktop_reason("User Account Control", "notepad.exe").has_value());
    CHECK(jarvis::tools::safety_reason("X", "explorer.exe", "close", 1).has_value());
    CHECK(!jarvis::tools::safety_reason("X", "notepad.exe", "focus", 1).has_value());

    // --- checked_int (Python _int_arg) ---
    if (const auto missing = jarvis::tools::checked_int(json::object(), "x", -30000, 30000);
        !std::holds_alternative<std::string>(missing)) {
        std::printf("FAIL eksik alan reddedilmedi\n");
        ++failures;
    }
    CHECK(std::get<long long>(jarvis::tools::checked_int(json{{"x", 12}}, "x", -30000, 30000)) == 12);
    CHECK(std::get<long long>(jarvis::tools::checked_int(json{{"x", "34"}}, "x", -30000, 30000)) == 34);
    if (const auto far = jarvis::tools::checked_int(json{{"x", 999999}}, "x", -30000, 30000);
        !std::holds_alternative<std::string>(far)) {
        std::printf("FAIL aralık dışı değer reddedilmedi\n");
        ++failures;
    }

    // --- pencere adı eşleşmesi (WindowsUI._window) ---
    const std::vector<std::string> titles{"Not Defteri (taslak.txt)", "Qoder", "Diğer"};
    CHECK(std::holds_alternative<std::size_t>(
              jarvis::tools::select_title_match(titles, "not defteri")));
    CHECK(std::get<std::size_t>(jarvis::tools::select_title_match(titles, "Qoder")) == 1);
    CHECK(std::holds_alternative<std::string>(
              jarvis::tools::select_title_match(titles, "yok böyle bir pencere")));
    const std::vector<std::string> twins{"Edge", "edge - Yeni sekme"};
    CHECK(std::get<std::size_t>(jarvis::tools::select_title_match(twins, "edge")) == 0);
    const std::vector<std::string> duplicates{"Steam", "Steam"};
    CHECK(std::holds_alternative<std::string>(jarvis::tools::select_title_match(duplicates, "steam")));

    // --- pencere satırı JSON alanları (Python _describe sözlüğü) ---
    WindowInfo info;
    info.handle = 1234;
    info.title = "Not Defteri";
    info.process = "notepad.exe";
    info.process_id = 42;
    info.rect = {0, 0, 800, 600};
    const json row = info.to_json();
    CHECK(row["handle"] == 1234);
    CHECK(row["title"] == "Not Defteri");
    CHECK(row["rect"].size() == 4);
    CHECK(!row.contains("window_class"));  // süzme alanı modele açıklanmaz

    // --- canlı masaüstü: yalnızca okuma ---
    const json listed = json::parse(WindowTools().run(json{{"action", "list"}, {"limit", 5}}));
    CHECK(listed["count"].get<long long>() <= 5);
    CHECK(listed.contains("note"));
    for (const json& window : listed["windows"]) {
        CHECK(window["handle"].get<long long>() > 0);
        CHECK(!window["title"].get<std::string>().empty());
    }

    // --- reddedilen girdiler ---
    expect_forbidden(json{{"action", "kill_process"}}, "window_control action");
    expect_forbidden(json{{"action", "focus"}}, "title veya handle gerekli");
    expect_forbidden(json{{"action", "focus"}, {"title", "zz yok zz pencere"}},
                     "Açık pencere bulunamadı");
    expect_forbidden(json{{"action", "focus"}, {"handle", "abc"}}, "handle sayı olmalı");
    expect_forbidden(json{{"action", "close"}, {"handle", "1"}}, "ile açık pencere bulunamadı");

    std::printf(failures ? "window_tools_test: %d BAŞARISIZ\n" : "window_tools_test: tamam\n",
                failures);
    return failures ? 1 : 0;
}
