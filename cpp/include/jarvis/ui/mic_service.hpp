#pragma once

// Mikrofondan kesintisiz Türkçe konuşma oturumu — Python app/core/voice.py
// ConversationWorker karşılığı: yakalama, VAD, söz kesme (barge-in) ve STT isteği.
//
// Nesne kendi QThread'inde yaşar; QAudioSource o iş parçacığında oluşturulur çünkü
// Qt sesi olay döngüsü ister. GUI iş parçacığı yalnızca atomik bayrakları yazar
// (Python da bayrakları arayüz iş parçacığından doğrudan set ediyordu).
// STT isteği ayrı bir iş parçacığında yürür ve sonucunu mutex'li kuyruğa yazar;
// zamanlayıcı o kuyruğu okur. İş parçacığından widget'a uzanan geri çağırmalar
// bu uygulamada çökmeye yol açıyordu.

#include <QAudioFormat>
#include <QObject>
#include <QString>

#include <atomic>
#include <deque>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include <jarvis/speech/pcm.hpp>
#include <jarvis/speech/stt.hpp>
#include <jarvis/speech/vad.hpp>

class QAudioSource;
class QIODevice;
class QTimer;

namespace jarvis::core {
class Settings;
}

namespace jarvis::ui {

class MicService : public QObject {
    Q_OBJECT
public:
    explicit MicService(core::Settings& settings, QObject* parent = nullptr);
    ~MicService() override;

    // --- GUI iş parçacığından DOĞRUDAN çağrılabilir (yalnız atomik/bayrak) ---
    void set_speaking(bool speaking, const QString& spoken_text = QString());
    void set_paused(bool paused);
    // Yakalamayı kapatır; kapanış iş parçacığında olur ve finished() yayılır.
    void request_stop();

    bool capturing() const { return capturing_.load(); }

    // Konuşurken söz kesme: kulaklık varsayımı ayardan gelir.
    void set_barge_in(bool enabled) { barge_in_.store(enabled); }

public slots:
    // Bu iş parçacığında yakalamayı açar (kuyruklu çağrılır).
    void begin();
    // Kapanış: bekleyen isteği iptal edip aygıtı bırakır.
    void shutdown();

signals:
    void level(double value);
    void heard(const QString& text);
    void error_occurred(const QString& message);
    void finished();

private:
    struct Event {
        enum class Kind { Text, NotUnderstood, RequestFailed };

        Kind kind;
        std::string text;
    };

    // İş parçacıkları yalnızca bu belleğe dokunur; nesne ömrü shared_ptr ile uzar.
    struct Shared {
        std::mutex mutex;
        std::deque<Event> events;
    };

    void teardown();
    void pump();
    void drain();
    void handle_event(jarvis::speech::VadEvent event);
    void dispatch_recognition(std::string pcm, bool speaking_now);
    // Tek oturumluk STT isteği: ağ/çözümleme sonucunu Event olarak döner.
    static Event run_request(const jarvis::speech::SttRequest& request);

    bool open_device();
    void reset_capture_state();
    void append_converted(const QByteArray& raw);
    double measure_ambient();
    void start_new_turn();
    void feed_chunk(const std::string& bytes);

    core::Settings& settings_;
    std::shared_ptr<Shared> shared_;
    std::shared_ptr<std::atomic<bool>> cancel_;
    jarvis::speech::Vad vad_;

    std::atomic<bool> stop_{false};
    std::atomic<bool> speaking_{false};
    std::atomic<bool> paused_{false};
    std::atomic<bool> barge_in_{true};
    std::atomic<bool> capturing_{false};
    std::atomic<bool> request_in_flight_{false};
    // Zaman damgaları steady_clock nanosaniye; <0 ise ayarlanmamış.
    std::atomic<long long> speaking_since_ns_{-1};
    std::atomic<long long> mute_until_ns_{-1};

    QAudioSource* source_ = nullptr;
    QIODevice* io_ = nullptr;
    QTimer* timer_ = nullptr;

    QAudioFormat format_;
    // Aygıtın biçimi 16 kHz mono Int16 değilse çevirimi bu dönüştürücü yapar.
    std::optional<jarvis::speech::PcmConverter> converter_;

    // Dönüşüm artıkları: yığın sınırından bağımsız 16 kHz mono tampon.
    std::string pcm_;

    double base_energy_ = 140.0;
    double barge_energy_ = 716.0;
    bool turn_active_ = false;
    int failures_ = 0;
    long long retry_after_ns_ = 0;
    std::set<std::string> spoken_words_;
    std::mutex words_mutex_;
};

}  // namespace jarvis::ui
