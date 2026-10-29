#include "jarvis/speech/pcm.hpp"

#include <algorithm>
#include <bit>
#include <cmath>

namespace jarvis::speech {
namespace {

void push_int16(std::string& out, double value) {
    const long rounded = std::lround(value);
    const int clamped = static_cast<int>(std::max(-32768L, std::min(32767L, rounded)));
    out.push_back(static_cast<char>(static_cast<unsigned char>(clamped & 0xFF)));
    out.push_back(static_cast<char>(static_cast<unsigned char>((clamped >> 8) & 0xFF)));
}

std::int16_t clamp_int16(double value) {
    const long rounded = std::lround(value);
    return static_cast<std::int16_t>(std::max(-32768L, std::min(32767L, rounded)));
}

}  // namespace

int pcm_sample_bytes(PcmSample sample) {
    switch (sample) {
    case PcmSample::UInt8:
        return 1;
    case PcmSample::Int16:
        return 2;
    case PcmSample::Int32:
    case PcmSample::Float:
        return 4;
    case PcmSample::Unknown:
        break;
    }
    return 2;
}

double sample_at(const unsigned char* at, PcmSample sample) {
    switch (sample) {
    case PcmSample::UInt8:
        return double(static_cast<int>(at[0]) - 128) * 256.0;
    case PcmSample::Int32: {
        const std::uint32_t bits = (std::uint32_t(at[3]) << 24) | (std::uint32_t(at[2]) << 16) |
                                   (std::uint32_t(at[1]) << 8) | std::uint32_t(at[0]);
        // 32-bit tamsayı 16-bit ölçeğine indirgenir.
        return double(std::int32_t(bits)) / 65536.0;
    }
    case PcmSample::Float: {
        const std::uint32_t bits = (std::uint32_t(at[3]) << 24) | (std::uint32_t(at[2]) << 16) |
                                   (std::uint32_t(at[1]) << 8) | std::uint32_t(at[0]);
        return double(std::bit_cast<float>(bits)) * 32767.0;
    }
    case PcmSample::Int16:
    case PcmSample::Unknown:
        break;
    }
    const std::int16_t value = static_cast<std::int16_t>(std::uint16_t(at[0]) |
                                                          (std::uint16_t(at[1]) << 8));
    return double(value);
}

double rms16(const char* data, std::size_t size) {
    const std::size_t count = size / 2;
    if (count == 0) return 0.0;
    const auto* bytes = reinterpret_cast<const unsigned char*>(data);
    double sum = 0.0;
    for (std::size_t i = 0; i < count; ++i) {
        const double sample = sample_at(bytes + i * 2, PcmSample::Int16);
        sum += sample * sample;
    }
    return std::sqrt(sum / double(count));
}

PcmConverter::PcmConverter(int source_rate, int channels, PcmSample sample, int target_rate)
    : source_rate_(source_rate > 0 ? source_rate : target_rate),
      channels_(channels > 0 ? channels : 1),
      sample_(sample),
      target_rate_(target_rate > 0 ? target_rate : 16000),
      step_(double(source_rate_ > 0 ? source_rate_ : target_rate_) /
            double(target_rate_ > 0 ? target_rate_ : 16000)) {}

void PcmConverter::append(const char* data, std::size_t size, std::string& out) {
    if (data == nullptr || size == 0) return;

    if (passthrough()) {
        out.append(data, size);
        return;
    }

    const std::size_t sample_bytes =
        std::max<std::size_t>(1, std::size_t(pcm_sample_bytes(sample_)));
    const std::size_t frame_bytes = sample_bytes * std::size_t(channels_);

    const unsigned char* at = reinterpret_cast<const unsigned char*>(data);
    std::size_t remaining = size;

    // Önceki yığından yarım kare kaldıysa önce o tamamlanır; atlanan baytlar
    // örnek hizasını kalıcı olarak kaydırırdı.
    if (!tail_.empty()) {
        const std::size_t take = std::min(frame_bytes - tail_.size(), remaining);
        tail_.append(reinterpret_cast<const char*>(at), take);
        at += take;
        remaining -= take;
        if (tail_.size() < frame_bytes) return;  // kare henüz tamamlanmadı
        append_frame(reinterpret_cast<const unsigned char*>(tail_.data()));
        tail_.clear();
    }

    const std::size_t frames = remaining / frame_bytes;
    for (std::size_t frame = 0; frame < frames; ++frame) {
        append_frame(at + frame * frame_bytes);
    }
    const std::size_t used = frames * frame_bytes;
    tail_.assign(reinterpret_cast<const char*>(at + used), remaining - used);

    // Doğrusal arama: position_ adımsız ilerler, taşan konum bir sonraki
    // yığının ilk örneğine bindirilir.
    const double limit = double(pending_.size()) - 1.0;
    while (position_ < limit) {
        const std::size_t index = static_cast<std::size_t>(position_);
        const double fraction = position_ - double(index);
        const double a = double(pending_[index]);
        const double b = double(pending_[index + 1]);
        push_int16(out, a + (b - a) * fraction);
        position_ += step_;
    }
    if (!pending_.empty()) {
        const std::size_t drop = pending_.size() - 1;
        pending_.erase(pending_.begin(), pending_.begin() + static_cast<long>(drop));
        position_ -= double(drop);
    }
}

void PcmConverter::append_frame(const unsigned char* at) {
    double acc = 0.0;
    const std::size_t sample_bytes =
        std::max<std::size_t>(1, std::size_t(pcm_sample_bytes(sample_)));
    for (int channel = 0; channel < channels_; ++channel) {
        acc += sample_at(at + std::size_t(channel) * sample_bytes, sample_);
    }
    pending_.push_back(clamp_int16(acc / double(channels_)));
}

}  // namespace jarvis::speech
