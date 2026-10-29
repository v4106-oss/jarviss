#pragma once

// Ham ses yakalama yığınından STT'nin istediği 16 kHz mono Int16'ya indirgeme.
// Python tarafında bu işi speech_recognition PortAudio akışı üzerinde
// audioop.set_format + audioop.ratecv ile yapıyordu; burada Qt yakalama biçimi
// (genelde 48 kHz float) aygıttan geldiği için çevrim elle yapılıyor.
//
// Salt veri dönüşümü: Qt/OS gerektirmez, birim testi çevrimden geçebilir.

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace jarvis::speech {

// QAudioFormat::SampleFormat'un Qt'den bağımsız karşılığı.
enum class PcmSample {
    Unknown,
    UInt8,   // işaretsiz 8-bit, önyargı 128
    Int16,
    Int32,
    Float,   // 32-bit kayan nokta, [-1, 1]
};

// Bir örnek kaç bayt (Python: QAudioFormat.bytesPerSample karşılığı).
int pcm_sample_bytes(PcmSample sample);

// 16-bit küçük uç örneklerin etkin değeri (Python audioop.rms(data, 2)).
double rms16(const char* data, std::size_t size);

// Bir tek örneği 16-bit ölçeğine taşır.
double sample_at(const unsigned char* at, PcmSample sample);

// Kanalları ortalayarak mono indirgeyen ve doğrusal arayarak hedef hıza
// örnekleyen durumlu dönüştürücü. Durum nesnede taşınır: yığın sınırları
// örnekleme ritmini bozmaz, böylece cümlenin süresi uzamaz.
class PcmConverter {
public:
    PcmConverter(int source_rate, int channels, PcmSample sample, int target_rate = 16000);

    // Kaynak zaten hedef biçimde mi? Evetse ham yığın olduğu gibi geçer.
    bool passthrough() const {
        return source_rate_ == target_rate_ && channels_ == 1 && sample_ == PcmSample::Int16;
    }

    // Ham yığını dönüştürüp out'a ekler; işlenmemiş artığı içinde saklar.
    void append(const char* data, std::size_t size, std::string& out);

    void reset() {
        pending_.clear();
        position_ = 0.0;
    }

private:
    // Tek kareyi mono int16'ya indirger ve örnekleme kuyruğuna ekler.
    void append_frame(const unsigned char* at);

    int source_rate_;
    int channels_;
    PcmSample sample_;
    int target_rate_;
    double step_;
    double position_ = 0.0;
    std::vector<std::int16_t> pending_;
    // Yığın sınırında bölünen yarım kare; örnek hizasını korumak için saklanır.
    std::string tail_;
};

}  // namespace jarvis::speech
