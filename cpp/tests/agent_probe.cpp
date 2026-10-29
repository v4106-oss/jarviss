// Geçici teşhis aracı: gerçek ayarlarla AIWorker'ı konsolda yürütür.
#include <cstdio>
#include <string>

#include <jarvis/ai/ai.hpp>
#include <jarvis/ai/executor.hpp>
#include <jarvis/ai/safety.hpp>
#include <jarvis/ai/worker.hpp>
#include <jarvis/core/home.hpp>
#include <jarvis/core/settings.hpp>
#include <jarvis/core/skill_library.hpp>
#include <jarvis/core/util.hpp>
#include <jarvis/storage/storage.hpp>
#include <jarvis/tools/screen_capture.hpp>

namespace {
void log(const char* stage) {
    std::fprintf(stderr, "[probe] %s\n", stage);
    std::fflush(stderr);
}
}  // namespace

int main(int argc, char** argv) {
    // Windows'ta argv ANSI (cp1254) baytları taşır; komut satırını geniş hâlden oku.
    const std::vector<std::string> args = jarvis::util::utf8_arguments(argc, argv);
    const std::string prompt =
        args.size() > 1 ? args[1]
                        : "Tek cümleyle cevap ver: Python mu yoksa C++ mi daha hızlı?";
    try {
        // Ölçümlerin hepsi fiziksel pikselden gelsin: DPI farkındalığı ilk iş.
        jarvis::tools::make_process_dpi_aware();
        jarvis::core::Settings settings;
        jarvis::storage::Storage storage;
        jarvis::core::SkillLibrary skills;
        jarvis::home::SmartHome home;
        log("services");
        auto services = jarvis::ai::build_services(settings);
        jarvis::ai::DesktopExecutor executor(services, storage, skills, home);
        jarvis::ai::AI ai(settings, storage, skills);
        // Ekran/OCR araçlarının yakaladığı görüntü modelin sonraki adımına eklenir.
        executor.set_screenshot_sink([&ai](const std::string& path) {
            std::fprintf(stderr, "[shot] %s\n", path.c_str());
            std::fflush(stderr);
            ai.add_screenshot(path);
        });
        log("worker");
        jarvis::ai::AIWorker worker(ai, storage, skills, services, executor);
        jarvis::ai::WorkerCallbacks cb;
        cb.started = [] { log("started"); };
        cb.chunk = [](const std::string& piece) {
            std::fprintf(stderr, "[chunk %zu]\n", piece.size());
            std::fflush(stderr);
        };
        cb.progress = [](const std::string& line) {
            std::fprintf(stderr, "[prog] %s\n", line.substr(0, 160).c_str());
            std::fflush(stderr);
        };
        cb.response = [](const std::string& text) {
            std::fprintf(stderr, "[resp %zu] %s\n", text.size(), text.substr(0, 1200).c_str());
            std::fflush(stderr);
        };
        cb.error = [](const std::string& text) {
            std::fprintf(stderr, "[err] %s\n", text.substr(0, 200).c_str());
            std::fflush(stderr);
        };
        worker.set_callbacks(std::move(cb));
        worker.set_prompt(prompt);
        log("run");
        worker.run();
        log("done");
    } catch (const std::bad_alloc& error) {
        std::fprintf(stderr, "[probe] BAD_ALLOC: %s\n", error.what());
        return 2;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "[probe] EXCEPTION: %s\n", error.what());
        return 3;
    }
    return 0;
}
