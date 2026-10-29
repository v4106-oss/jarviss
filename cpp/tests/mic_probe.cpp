// Mikrofon hattının canlı ucu: MicService gerçek aygıtta çalıştırılır, seviye
// tepeleri, duyulan cümleler ve hatalar konsola yazdırılır. Birim test değildir
// (ayar, aygıt ve ortam sesi gerektirir); P1-1'in canlı doğrulaması için vardır.
#include <QAudioDevice>
#include <QAudioFormat>
#include <QAudioSource>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QIODevice>
#include <QMediaDevices>
#include <QMetaObject>
#include <QThread>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>

#include <jarvis/core/settings.hpp>
#include <jarvis/ui/mic_service.hpp>

namespace {

struct Sink {
    int chunks = 0;
    double peak = 0.0;
    int errors = 0;
    int heard = 0;
    int sessions = 0;
};

void pump(int milliseconds) {
    QElapsedTimer clock;
    clock.start();
    while (clock.elapsed() < milliseconds) {
        QCoreApplication::processEvents();
    }
}

// Biçimden bağımsız ölçüm: her örnek 16-bit ölçeğine indirgenir, böylece farklı
// aygıtlar kıyaslanabilir. RMS sessizliğe gömüldüğünde (düşük mikrofon kazancı)
// yalnızca en mutlak örnek transient'i ayırt eder.
struct RawStats {
    double rms = 0.0;
    double peak = 0.0;
};

RawStats stats_raw(const QByteArray& data, const QAudioFormat& format) {
    const int bytes = std::max(1, format.bytesPerSample());
    const std::size_t count = std::size_t(data.size()) / std::size_t(bytes);
    RawStats out;
    if (count == 0) return out;
    const auto* bytes_ptr = reinterpret_cast<const unsigned char*>(data.constData());
    double sum = 0.0;
    for (std::size_t i = 0; i < count; ++i) {
        const unsigned char* at = bytes_ptr + i * std::size_t(bytes);
        double sample = 0.0;
        if (bytes == 1) {
            sample = double(int(at[0]) - 128) * 256.0;
        } else if (bytes == 2) {
            sample = double(signed((at[1] << 8) | at[0]));
        } else if (format.sampleFormat() == QAudioFormat::Float) {
            float value = 0.0F;
            std::memcpy(&value, at, sizeof(value));
            sample = double(value) * 32767.0;
        } else {
            sample = double(signed(at[3] << 24 | at[2] << 16 | at[1] << 8 | at[0])) / 65536.0;
        }
        sum += sample * sample;
        out.peak = std::max(out.peak, std::abs(sample));
    }
    out.rms = std::sqrt(sum / double(count));
    return out;
}

const char* format_name(QAudioFormat::SampleFormat format) {
    switch (format) {
    case QAudioFormat::UInt8: return "UInt8";
    case QAudioFormat::Int16: return "Int16";
    case QAudioFormat::Int32: return "Int32";
    case QAudioFormat::Float: return "Float";
    default: return "Bilinmeyen";
    }
}

// Aygıttan bağımsız kısa okuma: servis hattı sıfır görürse bununla aygıtın
// gerçekten sessiz mi yoksa çevrimin mi bozuk olduğu ayrışır. Her giriş aygıtı
// için tek tek ölçülür, böylece kayıtlı (belki sessize alınmış) aygıt ile
// Windows'un varsayılanı karşılaştırılabilir.
void probe_device(const QAudioDevice& device, int milliseconds) {
    QAudioFormat wanted;
    wanted.setSampleRate(16000);
    wanted.setChannelCount(1);
    wanted.setSampleFormat(QAudioFormat::Int16);
    const bool wanted_ok = device.isFormatSupported(wanted);
    if (!wanted_ok) wanted = device.preferredFormat();

    QAudioSource source(device, wanted);
    QIODevice* io = source.start();
    if (io == nullptr) {
        std::printf("  %-38s : akış açılamadı (hata %d)\n",
                    device.description().toUtf8().constData(),
                    static_cast<int>(source.error()));
        return;
    }
    double best_rms = 0.0;
    double peak = 0.0;
    const int steps = std::max(1, milliseconds / 100);
    for (int step = 0; step < steps; ++step) {
        pump(milliseconds / steps);
        const QByteArray data = io->readAll();
        const RawStats stats = stats_raw(data, wanted);
        best_rms = std::max(best_rms, stats.rms);
        peak = std::max(peak, stats.peak);
    }
    source.stop();
    std::printf("  %-38s : %6d Hz %-5s %dB [%s] 16k-mono[%s] → en iyi 100ms RMS %.1f, tepe %.1f\n",
                device.description().toUtf8().constData(), wanted.sampleRate(),
                wanted.channelCount() == 1 ? "mono" : "stereo", wanted.bytesPerSample(),
                format_name(wanted.sampleFormat()), wanted_ok ? "evet" : "hayır", best_rms, peak);
}

void direct_capture(int milliseconds) {
    const QList<QAudioDevice> inputs = QMediaDevices::audioInputs();
    std::printf("DOĞRUDAN: %d giriş aygıtı, varsayılan '%s'\n", static_cast<int>(inputs.size()),
                QMediaDevices::defaultAudioInput().description().toUtf8().constData());
    for (const QAudioDevice& device : inputs) {
        probe_device(device, milliseconds);
    }
}

}  // namespace

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    // Argüman 1: aygıt yoklama süresi (ms). Verilmezse kısa tur.
    const int probe_ms = argc > 1 ? std::atoi(argv[1]) : 1200;
    direct_capture(probe_ms);

    jarvis::core::Settings settings;
    // Argüman 2: oturumda kullanılacak girişin adı ("varsayılan" => boş ad).
    // Kayıtlı settings.json'a dokunulmaz; yalnızca mic_device içeren geçici bir
    // dosya kurulur, API anahtarı kopyalanmaz (gömülü varsayılana düşülür).
    if (argc > 2) {
        const std::string arg = argv[2];
        const std::string wanted = (arg == "varsayılan" || arg == "default") ? std::string() : arg;
        std::error_code ec;
        const std::filesystem::path temp =
            std::filesystem::temp_directory_path(ec) / "jarvis_mic_probe_settings.json";
        std::ofstream out(temp, std::ios::binary | std::ios::trunc);
        out << "{\"mic_device\": \"" << wanted << "\"}";
        out.close();
        settings = jarvis::core::Settings(temp);
    }

    QThread worker;
    jarvis::ui::MicService mic(settings);
    mic.moveToThread(&worker);

    Sink sink;
    // &app bağlamı: sinyaller ana iş parçacığına kuyruklanır, sayaçlar yarışmaz.
    QObject::connect(&mic, &jarvis::ui::MicService::level, &app, [&sink](double value) {
        ++sink.chunks;
        if (value > sink.peak) sink.peak = value;
    });
    QObject::connect(&mic, &jarvis::ui::MicService::heard, &app,
                     [&sink](const QString& text) {
                         ++sink.heard;
                         std::printf("DUYULDU: %s\n", text.toUtf8().constData());
                     });
    QObject::connect(&mic, &jarvis::ui::MicService::error_occurred, &app,
                     [&sink](const QString& message) {
                         ++sink.errors;
                         std::printf("HATA: %s\n", message.toUtf8().constData());
                     });
    QObject::connect(&mic, &jarvis::ui::MicService::finished, &app,
                     [&sink] {
                         ++sink.sessions;
                         std::printf("OTURUM KAPANDI\n");
                     });

    worker.start();
    QMetaObject::invokeMethod(&mic, &jarvis::ui::MicService::begin, Qt::QueuedConnection);
    std::printf("Mikrofon açılıyor (kayıtlı aygıt: '%s')\n", settings.mic_device().c_str());

    for (int round = 0; round < 10; ++round) {
        pump(500);
        std::printf("%2d. 500 ms: %d seviye olayı, tepe %.3f\n", round + 1, sink.chunks,
                    sink.peak);
        sink.chunks = 0;
        sink.peak = 0.0;
    }

    std::printf("Özet: duyulan=%d hata=%d kapanan oturum=%d\n", sink.heard, sink.errors,
                sink.sessions);
    QMetaObject::invokeMethod(&mic, &jarvis::ui::MicService::shutdown,
                              Qt::BlockingQueuedConnection);
    worker.quit();
    worker.wait();
    return 0;
}
