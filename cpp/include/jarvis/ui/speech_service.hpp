#pragma once

// SpeechService — cümle cümle sentezleyip seçili çıkışa çalan, anında susan ses hattı.
// Python app/core/speech.py::SpeechService karşılığı.
//
// Sentez iş parçacıkları bu QObject'e dokunmaz: sonuçları Shared kuyruğuna yazarlar,
// GUI iş parçacığındaki zamanlayıcı okur. İş parçacığından doğrudan widget'a uzanan
// geri çağırmalar bu uygulamada çökmeye yol açıyordu.

#include <atomic>
#include <deque>
#include <filesystem>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include <QObject>
#include <QString>

class QAudioOutput;
class QMediaPlayer;
class QTimer;

namespace jarvis::core {
class Settings;
}

namespace jarvis::ui {

class SpeechService : public QObject {
    Q_OBJECT
public:
    explicit SpeechService(core::Settings& settings, QObject* parent = nullptr);
    ~SpeechService() override;

    // max_chars verilirse yanıt ses için kısaltılır, yoksa tüm metin okunur.
    void speak(const std::string& text, std::optional<int> max_chars = std::nullopt);
    void stop();
    // Kapanışta beklemeyen durdurma: çalan her şeyi susturur.
    void shutdown();
    // Ses karakteri, çıkış aygıtı ve düzey ayarlardan yenilenir.
    void apply_settings();

    bool busy() const;
    const std::string& spoken_text() const;
    std::string voice_label() const;

signals:
    void started();
    void finished();
    void error_occurred(const QString& message);
    void busy_changed(bool busy);

private:
    enum class Kind { ChunkReady, SynthFinished, SynthFailed };

    struct Event {
        Kind kind;
        int token;
        int index;
        std::string value;
    };

    // İş parçacıkları yalnızca bu belleğe erişir; nesne ömrü shared_ptr ile uzar.
    struct Shared {
        std::mutex mutex;
        std::deque<Event> events;
        std::shared_ptr<std::atomic<bool>> synth_stop;
    };

    void ensure_player();
    void halt(bool notify);
    void drain();
    void pump();
    void on_chunk_ready(int index, const std::string& path);
    void on_synth_finished();
    void on_synth_failed(const std::string& message);
    void on_player_error(const QString& message);
    void start_fallback();
    void poll_fallback();
    void stop_fallback();
    void start_synthesis(std::vector<std::string> chunks, std::string voice, std::string rate,
                         std::string pitch, int token);
    void finish();
    int volume() const;

    core::Settings& settings_;
    std::shared_ptr<Shared> shared_;

    QMediaPlayer* player_ = nullptr;
    QAudioOutput* output_ = nullptr;
    QTimer* timer_ = nullptr;

    int token_ = 0;
    bool active_ = false;
    std::string text_;
    std::vector<std::string> chunks_;
    std::map<int, std::filesystem::path> paths_;
    int next_play_ = 0;
    bool synth_done_ = false;
    bool started_emitted_ = false;
    bool pending_play_ = false;
    bool pumping_ = false;
    bool fallback_tried_ = false;
    bool fallback_active_ = false;
    // Yerel ses süreci yalnızca GUI iş parçacığı tarafından kullanılır.
    void* fallback_process_ = nullptr;
};

}  // namespace jarvis::ui
