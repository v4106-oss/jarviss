#pragma once

// Ses başlangıcı/bitişi durum makinesi — Python app/core/voice.py::_listen çevrimi.
//
// Ses aygıtına dokunmaz: dışarıdan gelen RMS + bayt yığınlarını işler ve zamanı
// dışarıdan alır (feed(rms, seconds, bytes)). Böylece çevirimdışı test edilebilir.
// Gürültü tabanı turlar arasında taşınır; her dinleme turunda sıfırdan öğrenmek
// gürültülü odada cümle sonunu geciktiriyordu.

#include <deque>
#include <string>
#include <vector>

namespace jarvis::speech {

// Sabitler voice.py ConversationWorker sınıfından birebir alındı.
inline constexpr double kBargeMinSeconds = 0.7;
inline constexpr double kBargeMinElapsed = 0.75;
inline constexpr double kBargePhraseLimit = 6.0;
inline constexpr double kPostSpeechMute = 0.6;
inline constexpr double kListenTimeout = 0.65;
inline constexpr double kPhraseLimit = 14.0;

enum class VadEvent {
    Idle,          // bu yığın cümleye katkısız geçti
    SpeechStarted, // sessizlikten konuşmaya geçiş (bilgi amaçlı)
    PhraseDone,    // cümle kapandı; ses gövdesi hazır
    Timeout,       // sessizlik: hata değil, tur yeniden başlar
    AmbientOnly,   // tek düzen enerji: konuşma değil, taban yükseltildi
};

class Vad {
public:
    struct Turn {
        double threshold = 140.0;        // base_energy / barge_energy
        double timeout = kListenTimeout;
        double phrase_limit = kPhraseLimit;
        double pause_threshold = 0.45;   // cümle sonu sessizliği
        double chunk_seconds = 0.064;    // yığın süresi: CHUNK / SAMPLE_RATE
        bool speaking = false;           // Jarvis konuşurken taban öğrenilmez
    };

    Vad();

    // Tur başlangıcı: gürültü tabanı KORUNARAK çevrim değişkenleri sıfırlanır.
    void start_turn(const Turn& turn);

    // Bir yığın (chunk) besler. seconds = chunk/rate; bytes ham 16-bit mono veri.
    VadEvent feed(double rms, double seconds, const std::string& bytes);

    // Python _listen her yığında _speaking bayrağını canlı okur: Jarvis konuşmaya
    // başladığı anda yeni RMS değerleri gürültü tabanına karışmaz.
    void set_speaking(bool speaking) { turn_.speaking = speaking; }

    bool started() const { return started_; }

    // Cümle gövdesi: alınana kadar iç tamponda birikir.
    const std::vector<std::string>& frames() const { return frames_; }
    std::string take_audio();
    void clear_audio();
    // Ses uzunluğu (saniye) — Python _duration(audio) karşılığı.
    double audio_seconds(double sample_rate, int channels = 1, int bytes_per_sample = 2) const;

    double noise_floor() const { return noise_floor_; }
    double start_gate() const { return start_gate_; }

private:
    static constexpr double kMinGate = 120.0;      // base_gate tavanı
    static constexpr double kStartHold = 0.18;      // konuşma başlangıcı için gereken süre
    static constexpr double kPreRollSeconds = 0.3;  // geriye alınan ses
    static constexpr double kFloorRatio = 1.55;
    static constexpr double kFloorOffset = 70.0;
    static constexpr double kSilenceFloor = 90.0;
    static constexpr std::size_t kNoiseKeep = 24;   // örnek sayısı (deque maxlen)
    static constexpr std::size_t kNoiseMin = 6;
    static constexpr double kNoiseSlice = 0.3;      // percentil dilimi
    static constexpr double kFlatSeconds = 1.8;
    static constexpr double kFlatRatio = 1.7;

    double percentile_floor() const;

    Turn turn_;
    double base_gate_ = kMinGate;
    double start_gate_ = kMinGate;
    double silence_gate_ = kMinGate;
    bool started_ = false;
    bool finished_ = false;   // olay üretildikten sonra yeni yığınlar yoksayılır
    VadEvent last_event_ = VadEvent::Idle;
    double voiced_ = 0.0;
    double silence_ = 0.0;
    double phrase_elapsed_ = 0.0;
    double wait_elapsed_ = 0.0;
    std::size_t pre_roll_max_ = 5;
    std::deque<std::string> pre_roll_;
    std::vector<std::string> frames_;
    std::vector<double> phrase_levels_;

    std::deque<double> noise_samples_;
    double noise_floor_ = 0.0;
};

}  // namespace jarvis::speech
