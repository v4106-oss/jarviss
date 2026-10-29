#include <jarvis/ui/mic_service.hpp>

#include <QAudioDevice>
#include <QAudioFormat>
#include <QAudioSource>
#include <QIODevice>
#include <QMediaDevices>
#include <QString>
#include <QThread>
#include <QTimer>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iterator>
#include <optional>
#include <thread>
#include <utility>

#include <jarvis/core/settings.hpp>
#include <jarvis/speech/stt.hpp>
#include <jarvis/speech/voice.hpp>

#ifdef JARVIS_ENABLE_NET
#include <jarvis/net/http.hpp>
#endif

namespace jarvis::ui {
namespace {

constexpr int kTargetRate = 16000;
constexpr int kChunkSamples = 1024;  // PortAudio CHUNK varsayılanı (~64 ms)
constexpr int kChunkBytes = kChunkSamples * 2;
constexpr double kChunkSeconds = double(kChunkSamples) / double(kTargetRate);
constexpr int kPumpIntervalMs = 20;
constexpr double kAmbientSeconds = 0.45;    // adjust_for_ambient_noise(duration=0.45)
constexpr double kAmbientMultiplier = 1.4;  // speech_recognition AMBIENT_ENERGY_MULTIPLIER
constexpr qint64 kMaxBurstBytes = 65536;    // tek seferde okunan en büyük yığın

long long now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

double seconds_since(long long stamp_ns) {
    if (stamp_ns < 0) return 1e9;
    return double(now_ns() - stamp_ns) / 1e9;
}

// Qt'nin örnek biçimi saf dönüştürücünün enum'ına çevrilir.
jarvis::speech::PcmSample sample_of(QAudioFormat::SampleFormat format) {
    switch (format) {
    case QAudioFormat::UInt8:
        return jarvis::speech::PcmSample::UInt8;
    case QAudioFormat::Int32:
        return jarvis::speech::PcmSample::Int32;
    case QAudioFormat::Float:
        return jarvis::speech::PcmSample::Float;
    case QAudioFormat::Int16:
    default:
        return jarvis::speech::PcmSample::Int16;
    }
}

}  // namespace

MicService::MicService(core::Settings& settings, QObject* parent)
    : QObject(parent), settings_(settings), shared_(std::make_shared<Shared>()),
      cancel_(std::make_shared<std::atomic<bool>>(false)) {}

MicService::~MicService() {
    cancel_->store(true);
    if (timer_ != nullptr) timer_->stop();
    if (source_ != nullptr) source_->stop();
}

void MicService::set_speaking(bool speaking, const QString& spoken_text) {
    if (speaking) {
        const std::vector<std::string> words =
            jarvis::speech::words_of(spoken_text.toUtf8().toStdString());
        {
            std::lock_guard<std::mutex> lock(words_mutex_);
            spoken_words_ = std::set<std::string>(words.begin(), words.end());
        }
        speaking_since_ns_.store(now_ns());
        speaking_.store(true);
        return;
    }
    speaking_.store(false);
    {
        std::lock_guard<std::mutex> lock(words_mutex_);
        spoken_words_.clear();
    }
    mute_until_ns_.store(now_ns() + long long(jarvis::speech::kPostSpeechMute * 1e9));
}

void MicService::set_paused(bool paused) { paused_.store(paused); }

void MicService::request_stop() {
    stop_.store(true);
    // Kapanış aygıtı yakalayan iş parçacığında olmalı; GUI yalnızca bayrak yazar.
    QMetaObject::invokeMethod(
        this, [this] { if (capturing_.load()) teardown(); }, Qt::QueuedConnection);
}

void MicService::shutdown() {
    stop_.store(true);
    cancel_->store(true);
    if (capturing_.load()) teardown();
}

void MicService::begin() {
    if (capturing_.load()) return;
    // Yeni oturum önceki durdurma isteğini geride bırakır; arayüz mikrofonu
    // kapalıyken begin() kuyruğa koymaz, bu yüzden burada sıfırlamak güvenli.
    stop_.store(false);
#ifndef JARVIS_ENABLE_NET
    error_occurred(QString::fromUtf8("Ses tanıma desteği bu derlemede yok."));
    finished();
    return;
#else
    failures_ = 0;
    retry_after_ns_ = 0;
    request_in_flight_.store(false);
    cancel_ = std::make_shared<std::atomic<bool>>(false);
    {
        std::lock_guard<std::mutex> lock(shared_->mutex);
        shared_->events.clear();
    }
    // Yeni oturum taze bir gürültü tabanıyla başlar; Python işçiyi her turda
    // yeniden kuruyordu, burada VAD'i sıfırlamak aynı davranışı veriyor.
    vad_ = jarvis::speech::Vad();
    reset_capture_state();

    if (!open_device()) return;

    base_energy_ = measure_ambient();
    barge_energy_ = std::min(8000.0, base_energy_ * 2.4 + 380.0);
    capturing_.store(true);

    if (timer_ == nullptr) {
        timer_ = new QTimer(this);
        connect(timer_, &QTimer::timeout, this, &MicService::pump);
    }
    timer_->start(kPumpIntervalMs);
    start_new_turn();
#endif
}

bool MicService::open_device() {
    const QList<QAudioDevice> inputs = QMediaDevices::audioInputs();
    if (inputs.isEmpty()) {
        error_occurred(QString::fromUtf8("Mikrofon hatası: giriş aygıtı bulunamadı."));
        finished();
        return false;
    }

    // Kayıtlı ad → konum indeksi. Python'ın resolve_microphone_index eşleştirmesi
    // kullanılır (tam, sonra ön ek); boş ad ya da bulunamayan ayar => varsayılan.
    std::vector<std::pair<int, std::string>> named;
    named.reserve(static_cast<std::size_t>(inputs.size()));
    for (int i = 0; i < inputs.size(); ++i) {
        named.emplace_back(i, inputs[i].description().toStdString());
    }
    const std::optional<int> chosen =
        jarvis::speech::resolve_microphone_index(named, settings_.mic_device());

    QAudioDevice device = (chosen.has_value() && *chosen >= 0 && *chosen < inputs.size())
                              ? inputs[static_cast<int>(*chosen)]
                              : QMediaDevices::defaultAudioInput();
    if (device.isNull()) device = QMediaDevices::defaultAudioInput();

    QAudioFormat wanted;
    wanted.setSampleRate(kTargetRate);
    wanted.setChannelCount(1);
    wanted.setSampleFormat(QAudioFormat::Int16);
    if (!device.isFormatSupported(wanted)) {
        // Aygıt 16 kHz mono veremiyorsa tercih ettiği biçimi al, çevirimi biz yap.
        wanted = device.preferredFormat();
    }
    if (!wanted.isValid() || !device.isFormatSupported(wanted)) {
        error_occurred(QString::fromUtf8("Mikrofon hatası: aygıt biçimi desteklenmiyor."));
        finished();
        return false;
    }

    format_ = wanted;
    converter_ = jarvis::speech::PcmConverter(format_.sampleRate(), format_.channelCount(),
                                             sample_of(format_.sampleFormat()), kTargetRate);

    source_ = new QAudioSource(device, format_, this);
    io_ = source_->start();
    if (io_ == nullptr) {
        error_occurred(QString::fromUtf8("Mikrofon hatası: ses akışı açılamadı (") +
                       QString::number(static_cast<int>(source_->error())) + ")");
        delete source_;
        source_ = nullptr;
        io_ = nullptr;
        finished();
        return false;
    }
    return true;
}

void MicService::reset_capture_state() {
    pcm_.clear();
    if (converter_) converter_->reset();
}

double MicService::measure_ambient() {
    // Python: recognizer.adjust_for_ambient_noise(source, duration=0.45)
    const long long deadline = now_ns() + long long(kAmbientSeconds * 1e9);
    double total = 0.0;
    std::size_t samples = 0;
    while (now_ns() < deadline && !stop_.load()) {
        QThread::msleep(10);
        if (io_ == nullptr) break;
        const QByteArray raw = io_->readAll();
        if (raw.isEmpty()) continue;
        append_converted(raw);
        while (pcm_.size() >= std::size_t(kChunkBytes)) {
            total += jarvis::speech::rms16(pcm_.data(), std::size_t(kChunkBytes));
            ++samples;
            pcm_.erase(pcm_.begin(), pcm_.begin() + kChunkBytes);
        }
    }
    reset_capture_state();
    if (samples == 0) return 140.0;
    const double energy = (total / double(samples)) * kAmbientMultiplier;
    // voice.py: base_energy = max(140, min(energy_threshold, 1200))
    return std::max(140.0, std::min(energy, 1200.0));
}

void MicService::append_converted(const QByteArray& raw) {
    if (!converter_ || raw.isEmpty()) return;
    converter_->append(raw.constData(), std::size_t(raw.size()), pcm_);
}

void MicService::start_new_turn() {
    if (now_ns() < retry_after_ns_) {
        turn_active_ = false;
        return;
    }
    jarvis::speech::Vad::Turn turn;
    const bool speaking = speaking_.load();
    turn.threshold = speaking ? barge_energy_ : base_energy_;
    turn.timeout = jarvis::speech::kListenTimeout;
    turn.phrase_limit =
        speaking ? jarvis::speech::kBargePhraseLimit : jarvis::speech::kPhraseLimit;
    turn.pause_threshold = speaking ? 0.4 : 0.45;
    turn.chunk_seconds = kChunkSeconds;
    turn.speaking = speaking;
    vad_.start_turn(turn);
    turn_active_ = true;
}

void MicService::feed_chunk(const std::string& bytes) {
    const double rms = jarvis::speech::rms16(bytes.data(), bytes.size());
    level(jarvis::speech::level_from_rms(rms));
    vad_.set_speaking(speaking_.load());
    handle_event(vad_.feed(rms, kChunkSeconds, bytes));
}

void MicService::handle_event(jarvis::speech::VadEvent event) {
    if (event == jarvis::speech::VadEvent::Idle ||
        event == jarvis::speech::VadEvent::SpeechStarted) {
        return;
    }

    if (event == jarvis::speech::VadEvent::Timeout ||
        event == jarvis::speech::VadEvent::AmbientOnly) {
        // Sessizlik hata değildir: Python da turu baştan başlatır.
        vad_.clear_audio();
        start_new_turn();
        return;
    }

    level(0.0);
    // Dinleme biterken Jarvis konuşmaya başlamış olabilir; bayat duruma göre
    // kendi sesini kullanıcı sanıp yanıt döngüsü açma.
    const bool speaking_now = speaking_.load();
    const double seconds = vad_.audio_seconds(kTargetRate);
    std::string audio = vad_.take_audio();
    start_new_turn();

    if (speaking_now) {
        if (!barge_in_.load()) return;
        if (seconds_since(speaking_since_ns_.load()) < jarvis::speech::kBargeMinElapsed) return;
        if (seconds < jarvis::speech::kBargeMinSeconds) return;
    } else if (now_ns() < mute_until_ns_.load()) {
        return;
    }
    if (audio.empty()) return;
    dispatch_recognition(std::move(audio), speaking_now);
}

MicService::Event MicService::run_request(const jarvis::speech::SttRequest& request) {
    Event event{Event::Kind::NotUnderstood, std::string()};
#ifdef JARVIS_ENABLE_NET
    jarvis::net::HttpClient client("http://www.google.com");
    client.set_timeout_ms(8000);
    // Durum kodu gövdeyle birlikte dönmeli: uç nokta 400/415 verirse gövde
    // biçimi (PCM/FLAC) reddedilmiş demektir.
    client.set_allow_error_status(true);
    const jarvis::net::HttpResult result =
        client.post_bytes(request.url, request.body, request.content_type);
    if (!result.has_value()) {
        event.kind = Event::Kind::RequestFailed;
        return event;
    }
    if (result.value().status_code >= 400) {
        event.kind = Event::Kind::RequestFailed;
        return event;
    }
    const jarvis::speech::SttOutcome outcome =
        jarvis::speech::parse_stt_response(result.value().text);
    if (outcome.ok()) {
        event.kind = Event::Kind::Text;
        event.text = outcome.text;
    }
#else
    event.kind = Event::Kind::RequestFailed;
#endif
    return event;
}

void MicService::dispatch_recognition(std::string pcm, bool speaking_now) {
    std::set<std::string> words_snapshot;
    {
        std::lock_guard<std::mutex> lock(words_mutex_);
        words_snapshot = spoken_words_;
    }

    jarvis::speech::SttRequest request = jarvis::speech::build_stt_request(
        settings_.stt_api_key(), pcm, kTargetRate, jarvis::speech::SttFormat::L16Pcm);

    request_in_flight_.store(true);
    std::shared_ptr<Shared> shared = shared_;
    std::shared_ptr<std::atomic<bool>> cancel = cancel_;
    const bool barge_in = barge_in_.load();

    // Ağ çağrısı bu iş parçacığını bloklamasın: ayrı iş parçacığı yalnızca
    // mutex'li kuyruğa yazar, sinyalleri zamanlayıcı yayımlar.
    std::thread([shared, cancel, request = std::move(request), words_snapshot,
                 speaking_now, barge_in]() {
        Event event = run_request(request);
        if (event.kind == Event::Kind::Text && speaking_now) {
            // Python sırası: önce tek kelimelik gürültü filtresi, sonra yankı örtüşmesi.
            if (!barge_in || !jarvis::speech::should_accept_barge(event.text)) {
                event.kind = Event::Kind::NotUnderstood;
            } else {
                const std::vector<std::string> tokens = jarvis::speech::words_of(event.text);
                if (!words_snapshot.empty() && tokens.size() >= 3) {
                    const std::set<std::string> spoken(tokens.begin(), tokens.end());
                    std::vector<std::string> both;
                    std::set_intersection(words_snapshot.begin(), words_snapshot.end(),
                                          spoken.begin(), spoken.end(),
                                          std::back_inserter(both));
                    if (double(both.size()) / double(tokens.size()) >= 0.7) {
                        event.kind = Event::Kind::NotUnderstood;
                    }
                }
            }
        }
        if (cancel->load()) return;
        std::lock_guard<std::mutex> lock(shared->mutex);
        shared->events.push_back(std::move(event));
    }).detach();
}

void MicService::drain() {
    std::deque<Event> ready;
    {
        std::lock_guard<std::mutex> lock(shared_->mutex);
        ready.swap(shared_->events);
    }
    if (ready.empty()) return;
    request_in_flight_.store(false);

    for (const Event& event : ready) {
        switch (event.kind) {
        case Event::Kind::Text:
            failures_ = 0;
            heard(QString::fromUtf8(event.text.c_str(), static_cast<int>(event.text.size())));
            break;
        case Event::Kind::NotUnderstood:
            break;
        case Event::Kind::RequestFailed:
            // Python: üst üste iki ulaşama hatasında oturumu kapat.
            if (++failures_ >= 2) {
                stop_.store(true);
                error_occurred(QString::fromUtf8(jarvis::speech::kSttRequestFailed));
                teardown();
                return;
            }
            retry_after_ns_ = now_ns() + 1000000000LL;
            turn_active_ = false;
            break;
        }
    }
}

void MicService::pump() {
    drain();
    if (stop_.load()) {
        if (capturing_.load()) teardown();
        return;
    }
    if (!capturing_.load() || io_ == nullptr) return;

    // Konuşma yokken ya da tanıma sürerken dinlenmez; akan ses atılır ki
    // bir sonraki cümle bayat bir birikim olarak geri dönmesin.
    const bool silent_window =
        paused_.load() || request_in_flight_.load() || (speaking_.load() && !barge_in_.load());
    if (silent_window || !turn_active_) {
        io_->readAll();
        pcm_.clear();
        if (!turn_active_ && !silent_window) start_new_turn();
        return;
    }

    const qint64 available = io_->bytesAvailable();
    if (available > 0) {
        append_converted(io_->read(std::min<qint64>(available, kMaxBurstBytes)));
    }
    while (pcm_.size() >= std::size_t(kChunkBytes)) {
        std::string chunk = pcm_.substr(0, std::size_t(kChunkBytes));
        pcm_.erase(pcm_.begin(), pcm_.begin() + kChunkBytes);
        feed_chunk(chunk);
        if (request_in_flight_.load() || stop_.load() || !turn_active_) break;
    }
}

void MicService::teardown() {
    if (timer_ != nullptr) timer_->stop();
    if (io_ != nullptr) {
        io_->close();
        io_ = nullptr;
    }
    if (source_ != nullptr) {
        source_->stop();
        delete source_;
        source_ = nullptr;
    }
    reset_capture_state();
    cancel_->store(true);
    request_in_flight_.store(false);
    turn_active_ = false;
    capturing_.store(false);
    finished();
}

}  // namespace jarvis::ui
