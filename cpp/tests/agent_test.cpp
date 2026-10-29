// Deterministik eylem planlayıcısı birim testi.
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <string>

#include "jarvis/ai/agent.hpp"

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
bool has(const nlohmann::json& args, const std::string& key) {
    return args.is_object() && args.contains(key);
}
}  // namespace

int main() {
    namespace fs = std::filesystem;
    auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    fs::path desktop = fs::temp_directory_path() / ("jarvis_agent_test_" + std::to_string(stamp)) / "Desktop";

    ActionPlanner planner(desktop);

    // boş -> plan yok
    CHECK(!planner.plan("").has_value());
    CHECK(!planner.plan("   ").has_value());

    // salt bilgi sorusu -> plan yok
    CHECK(!planner.plan("fotosentez nasıl işler?").has_value());

    // geri al
    auto undo = planner.plan("geri al");
    CHECK(undo.has_value());
    if (undo) {
        CHECK(undo->actions.size() == 1);
        CHECK(undo->actions[0].action_type == "undo_last");
    }
    auto undo2 = planner.plan("Son işlemi geri al");
    CHECK(undo2.has_value() && undo2->actions[0].action_type == "undo_last");

    // klasör oluştur
    auto dir = planner.plan("Projeler adında bir klasör oluştur");
    CHECK(dir.has_value());
    if (dir) {
        CHECK(dir->actions.size() == 1);
        CHECK(dir->actions[0].action_type == "create_folder");
        CHECK(has(dir->actions[0].arguments, "path"));
        CHECK(dir->actions[0].arguments["path"].get<std::string>().find("Projeler") != std::string::npos);
    }

    // boş metin belgesi
    auto doc = planner.plan("Rapor adında metin belgesi oluştur");
    CHECK(doc.has_value());
    if (doc) {
        CHECK(doc->actions[0].action_type == "write_text_file");
        CHECK(doc->actions[0].arguments["path"].get<std::string>().find("Rapor.txt") != std::string::npos);
    }

    // klasör + dosya + içerik (çok adımlı)
    auto combo = planner.plan(
        "İş adında bir klasör oluştur, içine Not adında metin belgesi oluştur ve "
        "içeriğinde \"Merhaba dünya\" yaz");
    CHECK(combo.has_value());
    if (combo) {
        CHECK(combo->actions.size() == 2);
        CHECK(combo->actions[0].action_type == "create_folder");
        CHECK(combo->actions[1].action_type == "write_text_file");
        CHECK(combo->actions[1].arguments["content"] == "Merhaba dünya");
    }

    // içerikle tekrarlı yazım
    auto rep = planner.plan(
        "Kalem adında bir klasör oluştur, içine Günlük adında metin belgesi oluştur ve "
        "içeriğinde 3 kere \"bugün iyiydi\" yaz");
    CHECK(rep.has_value());
    if (rep) {
        std::string content = rep->actions[1].arguments["content"];
        int nl = 0;
        for (char c : content) if (c == '\n') ++nl;
        CHECK(nl == 2);  // 3 satır => 2 satır sonu
    }

    // not defteri + içerik -> Jarvis_Notu.txt + aç
    auto note = planner.plan("not defterini aç ve içine \"toplanti 15:00\" yaz");
    CHECK(note.has_value());
    if (note) {
        CHECK(note->actions.size() == 2);
        CHECK(note->actions[0].action_type == "write_text_file");
        CHECK(note->actions[0].arguments["overwrite"] == true);
        CHECK(note->actions[1].action_type == "open_path");
    }

    // web araması
    auto search = planner.plan("google'da hava durumu ara");
    CHECK(search.has_value());
    if (search) {
        CHECK(search->actions[0].action_type == "search_web");
        CHECK(search->actions[0].arguments["query"] == "hava durumu");
    }

    // uygulama açma
    auto app = planner.plan("not defteri aç");
    CHECK(app.has_value());
    if (app) {
        CHECK(app->actions[0].action_type == "open_application");
        CHECK(app->actions[0].arguments["application"] == "notepad");
    }

    // silme (destructive) — onay gerekir
    auto del = planner.plan("dosya 'eski.txt' sil");
    CHECK(del.has_value());
    if (del) {
        CHECK(del->actions[0].action_type == "delete_path");
        CHECK(del->actions[0].risk == "destructive");
        CHECK(del->requires_confirmation());
    }

    // summary biçimi
    if (combo) {
        std::string s = combo->summary();
        CHECK(s.find("1. ") != std::string::npos);
        CHECK(s.find("2. ") != std::string::npos);
    }

    fs::remove_all(desktop.parent_path());

    if (failures == 0) {
        std::printf("agent_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("agent_test: %d HATA\n", failures);
    return 1;
}
