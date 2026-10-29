#include <chrono>
#include <cstdio>
#include <filesystem>
#include <string>

#include <jarvis/core/skill_library.hpp>
#include <jarvis/core/task_session.hpp>
#include <jarvis/storage/storage.hpp>

static int failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);      \
            ++failures;                                                      \
        }                                                                    \
    } while (0)

int main() {
    using jarvis::storage::Storage;
    namespace fs = std::filesystem;

    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path dir = fs::temp_directory_path() / ("jarvis_cpp_storage_" + std::to_string(stamp));
    fs::create_directories(dir);

    {
        Storage store((dir / "jarvis.db").string());

        // --- iletiler ---
        store.add_message("user", "merhaba");
        store.add_message("assistant", "selam sana", "chat",
                          nlohmann::json{{"konu", "selamlama"}});
        auto recent = store.get_recent_messages(10);
        CHECK(recent.size() == 2);
        CHECK(recent[0].role == "user");
        CHECK(recent[1].role == "assistant");
        CHECK(recent[1].metadata.has_value());
        CHECK(recent[1].metadata->at("konu") == "selamlama");

        // --- arama ---
        auto found = store.search_messages("selam", 10);
        CHECK(found.size() == 1);
        CHECK(found[0].role == "assistant");

        // --- konuşma geçmişi ---
        auto hist = store.get_conversation_history(10, true);
        CHECK(hist.size() == 1);
        CHECK(hist[0].role == "user");

        // --- bozuk UTF-8 yazma sınırında temizlenir ---
        // ANSI (cp1254) kod sayfalarından sızmış tek baytlar kayıt altına alınırsa
        // sonraki JSON dökümlerini çökertir; depolama katmanı bunları engellemeli.
        store.add_message("user", std::string("T") + static_cast<char>(0xFC) + "rkiye");
        const auto sanitized = store.get_recent_messages(1);
        CHECK(sanitized.size() == 1);
        CHECK(sanitized[0].content.find('\xFC') == std::string::npos);
        CHECK(sanitized[0].content.find("\xEF\xBF\xBD") != std::string::npos);
        CHECK(store.get_conversation_history(10).back().content.find('\xFC') == std::string::npos);

        // --- ajan koşuları ---
        auto run = store.start_agent_run("deneme görevi");
        CHECK(run > 0);
        store.log_agent_event(run, "adım", nlohmann::json{{"n", 1}});
        store.finish_agent_run(run, "completed", "bitti");
        auto runs = store.get_agent_runs(10);
        CHECK(runs.size() == 1);
        CHECK(runs[0].at("status") == "completed");
        CHECK(runs[0].at("summary") == "bitti");
        auto events = store.get_agent_events(run, 10);
        CHECK(events.size() == 1);
        CHECK(events[0].at("event_type") == "adım");

        // --- görev oturumları ---
        auto session = store.start_task_session("kod yaz");
        CHECK(session > 0);
        auto active = store.get_active_task_session();
        CHECK(active.has_value());
        CHECK(active->at("goal") == "kod yaz");
        store.add_task_step(session, 1, "edit", "aç", "ok", "done");
        auto steps = store.get_task_steps(session, 10);
        CHECK(steps.size() == 1);
        CHECK(steps[0].at("tool") == "edit");
        store.set_task_session_state(session, "completed", std::nullopt, "tamamlandı");
        auto done = store.get_task_session(session);
        CHECK(done.has_value());
        CHECK(done->at("status") == "completed");
        CHECK(store.get_active_task_session() == std::nullopt);

        // --- anahtar-değer bellek ---
        store.save_memory("anahtar", "değer");
        store.save_memory("anahtar", "yeni");
        CHECK(store.get_memory("anahtar") == std::string("yeni"));
        auto all = store.get_all_memory();
        CHECK(all.size() == 1);
        store.delete_memory("anahtar");
        CHECK(store.get_memory("anahtar") == std::nullopt);
    }

    // --- TaskSessionManager ---
    {
        using namespace jarvis::core;
        Storage store((dir / "session.db").string());
        SkillLibrary skills(dir / "skills2", /*seed=*/false);
        TaskSessionManager manager(store, skills, dir);

        // summarize_arguments gizlilik + özet
        const nlohmann::json args = {{"path", "C:\\a.txt"}, {"content", "sır metni"},
                                     {"n", 5}, {"items", {1, 2, 3}}};
        const std::string summary = summarize_arguments(args);
        CHECK(summary.find("content=<gizli>") != std::string::npos);
        CHECK(summary.find("path=") != std::string::npos);
        CHECK(summary.find("<list:3>") != std::string::npos);

        // extract_screenshot
        CHECK(extract_screenshot(R"({"screenshot":"C:\\x\\shot.png"})") == std::string("C:\\x\\shot.png"));
        CHECK(extract_screenshot("no image here") == std::nullopt);

        const std::int64_t sid = manager.start("toplu görev", 1);
        CHECK(sid > 0);
        manager.record_step("read_page", nlohmann::json{{"url", "http://a"}}, "içerik...");
        manager.record_step("click", nlohmann::json{{"x", 1}}, "❌ başarısız");
        CHECK(manager.note("gözlem notu").find("kaydedildi") != std::string::npos);

        const std::string desc = manager.describe(12);
        CHECK(desc.find("Görev #") != std::string::npos);
        CHECK(desc.find("read_page") != std::string::npos);
        CHECK(desc.find("✗") != std::string::npos);

        CHECK(manager.pause("test").find("duraklatıldı") != std::string::npos);
        CHECK(manager.resume().find("devam") != std::string::npos);
        CHECK(manager.takeover("ben yapacağım").find("Kontrol kullanıcıda") != std::string::npos);

        const std::string exported = manager.export_session();
        CHECK(exported.find("dışa aktarıldı") != std::string::npos);
        CHECK(fs::exists(dir / "trajectories" / ("gorev-" + std::to_string(sid) + ".md")));

        // build_skill: 2+ araç adımı gerekir → başarılı.
        manager.finish("completed", "özet");
        const std::string skill_msg = manager.build_skill("Toplu Görev", "Açıklama");
        CHECK(skill_msg.find("yetenek oluşturuldu") != std::string::npos);
        CHECK(skills.get("toplu-gorev").has_value());

        // finish sonrası pause hata fırlatmalı.
        bool paused_err = false;
        manager.start("ikinci", std::nullopt);
        manager.finish("completed", "");
        try {
            manager.pause("x");
        } catch (const TaskSessionError&) {
            paused_err = true;
        }
        CHECK(paused_err);
    }

    fs::remove_all(dir);

    if (failures == 0) {
        std::printf("storage_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("storage_test: %d HATA\n", failures);
    return 1;
}
