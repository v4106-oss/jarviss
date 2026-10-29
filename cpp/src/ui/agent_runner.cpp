#include <jarvis/ui/agent_runner.hpp>

#include <atomic>
#include <thread>
#include <utility>

#include <jarvis/core/home.hpp>
#include <jarvis/core/settings.hpp>

#ifdef JARVIS_ENABLE_NET
#include <jarvis/ai/ai.hpp>
#include <jarvis/ai/executor.hpp>
#include <jarvis/ai/safety.hpp>
#include <jarvis/ai/worker.hpp>
#include <jarvis/core/skill_library.hpp>
#include <jarvis/storage/storage.hpp>
#endif

namespace jarvis::ui {
namespace {

QString to_q(const std::string& text) {
    return QString::fromUtf8(text.data(), static_cast<int>(text.size()));
}

}  // namespace

// Depo, yetenek kütüphanesi ve güvenlik servisleri pencere boyunca bir kez kurulur;
// AIWorker her soruda yeniden yaratılır, böylece önceki koşunun durumu sızmaz.
struct AgentRunner::Impl {
    Impl(core::Settings& settings, home::SmartHome& home) : settings(settings), home(home) {}

    core::Settings& settings;
    home::SmartHome& home;

#ifdef JARVIS_ENABLE_NET
    storage::Storage storage{};
    core::SkillLibrary skills{};
    ai::SafetyServices services{ai::build_services(settings)};
    std::unique_ptr<ai::DesktopExecutor> executor;
    std::unique_ptr<ai::AI> ai;
    std::unique_ptr<ai::AIWorker> worker;
    std::thread thread;
    std::atomic<bool> busy{false};
#endif
};

AgentRunner::AgentRunner(core::Settings& settings, home::SmartHome& home, QObject* parent)
    : QObject(parent), impl_(std::make_unique<Impl>(settings, home)) {
#ifdef JARVIS_ENABLE_NET
    impl_->executor = std::make_unique<ai::DesktopExecutor>(impl_->services, impl_->storage,
                                                            impl_->skills, impl_->home);
    impl_->ai = std::make_unique<ai::AI>(impl_->settings, impl_->storage, impl_->skills);
    // Tarayıcı isteği worker iş parçacığından gelir; sinyal GUI'ye kuyrukla taşır.
    impl_->executor->set_browser_listener(
        [this](const std::string& url) { emit browser_open_requested(to_q(url)); });
    // Ekran/OCR araçlarının yakaladığı PNG modelin sonraki adımına buradan eklenir.
    // Yürütücü jarvis_ai'yı tanımaz; köprü burada kurulur. AI nesnesi yalnızca
    // worker iş parçacığından dokunulur (ask() önceki turu join eder), GUI'ye
    // hiçbir sinyal bu kancadan gönderilmez.
    impl_->executor->set_screenshot_sink(
        [this](const std::string& path) { impl_->ai->add_screenshot(path); });
#endif
}

AgentRunner::~AgentRunner() {
#ifdef JARVIS_ENABLE_NET
    if (impl_->thread.joinable()) {
        impl_->worker->cancel();
        impl_->thread.join();
    }
#endif
}

bool AgentRunner::ask(const std::string& prompt) {
#ifdef JARVIS_ENABLE_NET
    if (impl_->thread.joinable()) impl_->thread.join();
    if (impl_->busy.load()) return false;

    impl_->worker = std::make_unique<ai::AIWorker>(*impl_->ai, impl_->storage, impl_->skills,
                                                   impl_->services, *impl_->executor);
    ai::WorkerCallbacks callbacks;
    callbacks.started = [this] { emit started(); };
    callbacks.chunk = [this](const std::string& text) { emit chunk_received(to_q(text)); };
    callbacks.response = [this](const std::string& text) { emit response_received(to_q(text)); };
    callbacks.progress = [this](const std::string& text) { emit progress_received(to_q(text)); };
    callbacks.error = [this](const std::string& text) { emit error_received(to_q(text)); };
    callbacks.approval_requested = [this](const std::string& text) { emit approval_received(to_q(text)); };
    callbacks.paused = [this](const std::string& text) { emit pause_state_changed(to_q(text)); };
    callbacks.resumed = [this] { emit pause_state_changed(QString()); };
    callbacks.takeover_requested = [this](const std::string& text) { emit takeover_received(to_q(text)); };
    impl_->worker->set_callbacks(std::move(callbacks));
    impl_->worker->set_prompt(prompt);

    impl_->busy.store(true);
    impl_->thread = std::thread([this] {
        try {
            impl_->worker->run();
        } catch (const std::exception& error) {
            emit error_received(to_q("Ajan hattı beklenmedik biçimde durdu: ") +
                               to_q(error.what()));
        }
        impl_->busy.store(false);
        emit finished();
    });
    return true;
#else
    (void)prompt;
    return false;
#endif
}

bool AgentRunner::busy() const {
#ifdef JARVIS_ENABLE_NET
    return impl_->busy.load();
#else
    return false;
#endif
}

void AgentRunner::cancel() {
#ifdef JARVIS_ENABLE_NET
    if (impl_->worker) impl_->worker->cancel();
#endif
}

void AgentRunner::pause() {
#ifdef JARVIS_ENABLE_NET
    if (impl_->worker) impl_->worker->pause();
#endif
}

void AgentRunner::resume() {
#ifdef JARVIS_ENABLE_NET
    if (impl_->worker) impl_->worker->resume();
#endif
}

void AgentRunner::set_approval(bool approved) {
#ifdef JARVIS_ENABLE_NET
    if (impl_->worker) impl_->worker->set_approval(approved);
#endif
}

}  // namespace jarvis::ui
