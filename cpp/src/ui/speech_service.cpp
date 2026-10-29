#include "jarvis/ui/speech_service.hpp"

#include <algorithm>
#include <chrono>
#include <thread>
#include <utility>

#include <QAudioDevice>
#include <QMediaDevices>
#include <QMediaPlayer>
#include <QAudioOutput>
#include <QTimer>
#include <QUrl>

#include "jarvis/core/settings.hpp"
#include "jarvis/core/win_path.hpp"
#include "jarvis/speech/synth.hpp"
#include "jarvis/speech/text.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;
using jarvis::core::Settings;
using jarvis::speech::VoiceStyle;
using jarvis::speech::concise_for_voice;
using jarvis::speech::clean_for_speech;
using jarvis::speech::plan_chunks;
using jarvis::speech::rate_text;
using jarvis::speech::style_label;
using jarvis::speech::style_parts;
using jarvis::speech::synthesize;

namespace jarvis::ui {
namespace {

constexpr int kDrainIntervalMs = 40;

QString to_q(const std::string& text) {
    return QString::fromUtf8(text.data(), static_cast<int>(text.size()));
}

fs::path from_u8(const std::string& utf8) { return fs::path(jarvis::util::utf8_to_wide(utf8)); }

std::string to_u8(const fs::path& path) { return jarvis::util::wide_to_utf8(path.wstring()); }

}  // namespace

SpeechService::SpeechService(Settings& settings, QObject* parent)
    : QObject(parent), settings_(settings), shared_(std::make_shared<Shared>()) {
    timer_ = new QTimer(this);
    connect(timer_, &QTimer::timeout, this, [this] { drain(); });
    timer_->start(kDrainIntervalMs);
    apply_settings();
}

SpeechService::~SpeechService() {
    shutdown();
}

void SpeechService::speak(const std::string& text, std::optional<int> max_chars) {
    const std::string body = max_chars ? concise_for_voice(text, *max_chars) : clean_for_speech(text);
    std::vector<std::string> chunks = plan_chunks(body);
    if (chunks.empty()) {
        return;
    }
    ensure_player();
    halt(false);
    ++token_;
    text_ = body;
    chunks_ = chunks;
    paths_.clear();
    next_play_ = 0;
    synth_done_ = false;
    started_emitted_ = false;
    pending_play_ = false;
    fallback_tried_ = false;
    fallback_active_ = false;
    active_ = true;

    auto stop = std::make_shared<std::atomic<bool>>(false);
    {
        std::lock_guard lock(shared_->mutex);
        shared_->events.clear();
        shared_->synth_stop = stop;
    }
    emit busy_changed(true);

    const VoiceStyle& style = style_parts(settings_.voice_style());
    start_synthesis(std::move(chunks), style.voice,
                    rate_text(settings_.voice_rate(), settings_.voice_style()), style.pitch, token_);
}

void SpeechService::stop() {
    halt(true);
}

void SpeechService::shutdown() {
    halt(false);
    if (player_) player_->stop();
}

bool SpeechService::busy() const {
    return active_;
}

const std::string& SpeechService::spoken_text() const {
    return text_;
}

std::string SpeechService::voice_label() const {
    return style_label(settings_.voice_style());
}

int SpeechService::volume() const {
    return std::max(0, std::min(100, settings_.voice_volume()));
}

void SpeechService::apply_settings() {
    if (!output_) {
        return;
    }
    const QString wanted = to_q(settings_.speaker_device());
    QAudioDevice chosen = QMediaDevices::defaultAudioOutput();
    if (!wanted.isEmpty()) {
        const auto devices = QMediaDevices::audioOutputs();
        for (const QAudioDevice& device : devices) {
            if (device.description() == wanted) {
                chosen = device;
                break;
            }
        }
    }
    output_->setDevice(chosen);
    output_->setVolume(volume() / 100.0);
}

// QtMultimedia motoru ilk konuşmada kurulur; açılışı yavaşlatmasın.
void SpeechService::ensure_player() {
    if (player_) {
        return;
    }
    output_ = new QAudioOutput(this);
    player_ = new QMediaPlayer(this);
    player_->setAudioOutput(output_);
    connect(player_, &QMediaPlayer::mediaStatusChanged, this,
            [this](QMediaPlayer::MediaStatus status) {
                if (status == QMediaPlayer::EndOfMedia) {
                    pending_play_ = false;
                    QTimer::singleShot(0, this, [this] { pump(); });
                } else if (status == QMediaPlayer::InvalidMedia) {
                    pending_play_ = false;
                    if (active_ && !fallback_active_) {
                        on_player_error("Ses dosyası oynatılamadı.");
                    }
                }
            });
    connect(player_, &QMediaPlayer::errorOccurred, this,
            [this](QMediaPlayer::Error error, const QString& text) {
                if (!active_ || fallback_active_ || error == QMediaPlayer::NoError) {
                    return;
                }
                on_player_error(text.isEmpty() ? QString::fromUtf8("Ses oynatılamadı.") : text);
            });
    apply_settings();
}

void SpeechService::on_player_error(const QString& message) {
    if (!started_emitted_ && !fallback_tried_) {
        fallback_tried_ = true;
        emit error_occurred(QString::fromUtf8("Ses oynatılamadı; yerel ses kullanılıyor: ") + message);
        start_fallback();
        return;
    }
    emit error_occurred(QString::fromUtf8("Ses oynatma hatası: ") + message);
    synth_done_ = true;
    paths_.clear();
    finish();
}

void SpeechService::start_synthesis(std::vector<std::string> chunks, std::string voice,
                                    std::string rate, std::string pitch, int token) {
    std::shared_ptr<Shared> shared = shared_;
    std::shared_ptr<std::atomic<bool>> stop;
    {
        std::lock_guard lock(shared->mutex);
        stop = shared->synth_stop;
    }
    if (!stop || chunks.empty()) {
        return;
    }
    std::thread([shared, stop, chunks = std::move(chunks), voice = std::move(voice),
                 rate = std::move(rate), pitch = std::move(pitch), token]() {
        auto push = [shared, token](Kind kind, int index, const std::string& value) {
            std::lock_guard lock(shared->mutex);
            shared->events.push_back(Event{kind, token, index, value});
        };
        // İlk parça ayrı bekletilir ki ses mümkün olan en erken anda başlasın.
        fs::path first;
        try {
            first = synthesize(chunks.front(), voice, rate, pitch);
        } catch (const std::exception& error) {
            if (!stop->load()) push(Kind::SynthFailed, 0, error.what());
            return;
        }
        if (stop->load()) return;
        push(Kind::ChunkReady, 0, to_u8(first));
        for (std::size_t index = 1; index < chunks.size(); ++index) {
            if (stop->load()) return;
            fs::path next;
            try {
                next = synthesize(chunks[index], voice, rate, pitch);
            } catch (const std::exception& error) {
                if (!stop->load()) push(Kind::SynthFailed, static_cast<int>(index), error.what());
                return;
            }
            if (stop->load()) return;
            push(Kind::ChunkReady, static_cast<int>(index), to_u8(next));
        }
        push(Kind::SynthFinished, 0, std::string());
    }).detach();
}

void SpeechService::drain() {
    std::deque<Event> batch;
    {
        std::lock_guard lock(shared_->mutex);
        batch.swap(shared_->events);
    }
    for (const Event& event : batch) {
        // Eski koşudan kalan olaylar yeni koşuyu bozmaz.
        if (event.token != token_) continue;
        switch (event.kind) {
        case Kind::ChunkReady:
            on_chunk_ready(event.index, event.value);
            break;
        case Kind::SynthFinished:
            on_synth_finished();
            break;
        case Kind::SynthFailed:
            on_synth_failed(event.value);
            break;
        }
    }
    if (fallback_active_) {
        poll_fallback();
    }
}

void SpeechService::on_chunk_ready(int index, const std::string& path) {
    if (!active_) return;
    paths_[index] = from_u8(path);
    pump();
}

void SpeechService::on_synth_finished() {
    if (!active_ || fallback_active_) return;
    synth_done_ = true;
    pump();
}

void SpeechService::on_synth_failed(const std::string& message) {
    if (!active_) return;
    if (!fallback_tried_ && !started_emitted_) {
        fallback_tried_ = true;
        emit error_occurred(
            QString::fromUtf8("Doğal ses üretilemedi; yerel ses kullanılıyor: ") + to_q(message));
        start_fallback();
        return;
    }
    emit error_occurred(QString::fromUtf8("Seslendirme hatası: ") + to_q(message));
    synth_done_ = true;
    pump();
}

void SpeechService::pump() {
    if (pumping_ || !active_ || fallback_active_ || !player_) {
        return;
    }
    if (pending_play_ || player_->playbackState() == QMediaPlayer::PlayingState) {
        return;
    }
    pumping_ = true;
    auto found = paths_.find(next_play_);
    if (found == paths_.end() && synth_done_ && !paths_.empty()) {
        // Sıra atlamışsa (bir parça üretilemediyse) kalan en küçük parçadan devam et.
        next_play_ = paths_.begin()->first;
        found = paths_.find(next_play_);
    }
    if (found != paths_.end()) {
        const fs::path path = found->second;
        paths_.erase(found);
        ++next_play_;
        pending_play_ = true;
        player_->setSource(QUrl::fromLocalFile(QString::fromStdWString(path.wstring())));
        if (!started_emitted_) {
            started_emitted_ = true;
            emit started();
        }
        player_->play();
        pumping_ = false;
        return;
    }
    pumping_ = false;
    if (synth_done_ && paths_.empty()) {
        finish();
    }
}

// Nöral ses yoksa ya da çalınamıyorsa Windows'un yerel SAPI sesi devreye girer.
void SpeechService::start_fallback() {
    if (player_) player_->stop();
    paths_.clear();
    next_play_ = static_cast<int>(chunks_.size());
    synth_done_ = true;
    pending_play_ = false;
    fallback_active_ = true;
#ifdef _WIN32
    const std::wstring line = jarvis::speech::sapi_command_line(text_, volume());
    HANDLE nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                             OPEN_EXISTING, 0, nullptr);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    if (nul != INVALID_HANDLE_VALUE) {
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = nul;
        startup.hStdOutput = nul;
        startup.hStdError = nul;
    }
    PROCESS_INFORMATION process{};
    const BOOL spawned = CreateProcessW(nullptr, const_cast<wchar_t*>(line.data()), nullptr,
                                        nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr,
                                        &startup, &process);
    if (nul != INVALID_HANDLE_VALUE) CloseHandle(nul);
    if (!spawned) {
        fallback_active_ = false;
        emit error_occurred(QString::fromUtf8("Yerel ses başlatılamadı."));
        finish();
        return;
    }
    CloseHandle(process.hThread);
    fallback_process_ = process.hProcess;
    if (!started_emitted_) {
        started_emitted_ = true;
        emit started();
    }
#else
    fallback_active_ = false;
    emit error_occurred(QString::fromUtf8("Yerel ses yalnızca Windows'ta kullanılabilir."));
    finish();
#endif
}

void SpeechService::poll_fallback() {
#ifdef _WIN32
    auto* handle = static_cast<HANDLE>(fallback_process_);
    if (handle == nullptr) {
        fallback_active_ = false;
        if (active_) finish();
        return;
    }
    if (WaitForSingleObject(handle, 0) != WAIT_OBJECT_0) {
        return;
    }
    CloseHandle(handle);
    fallback_process_ = nullptr;
    fallback_active_ = false;
    if (active_) {
        finish();
    }
#endif
}

void SpeechService::stop_fallback() {
#ifdef _WIN32
    if (fallback_process_ != nullptr) {
        auto* handle = static_cast<HANDLE>(fallback_process_);
        TerminateProcess(handle, 1);
        CloseHandle(handle);
        fallback_process_ = nullptr;
    }
#endif
    fallback_active_ = false;
}

void SpeechService::finish() {
    if (!active_) {
        return;
    }
    active_ = false;
    pending_play_ = false;
    paths_.clear();
    {
        std::lock_guard lock(shared_->mutex);
        if (shared_->synth_stop) {
            shared_->synth_stop->store(true);
            shared_->synth_stop.reset();
        }
    }
    if (player_) {
        player_->stop();
    }
    emit busy_changed(false);
    emit finished();
}

// Koşuyu kökten durdurur: sıradaki token'ı ilerletir, iş parçacığını bilgilendirir.
void SpeechService::halt(bool notify) {
    const bool was_active = active_;
    ++token_;
    active_ = false;
    {
        std::lock_guard lock(shared_->mutex);
        if (shared_->synth_stop) shared_->synth_stop->store(true);
        shared_->events.clear();
    }
    stop_fallback();
    if (player_) {
        player_->stop();
    }
    paths_.clear();
    next_play_ = 0;
    synth_done_ = false;
    pending_play_ = false;
    fallback_tried_ = false;
    if (notify && was_active) {
        emit busy_changed(false);
        emit finished();
    }
}

}  // namespace jarvis::ui
