#include "jarvis/speech/vad.hpp"

#include <algorithm>
#include <cmath>

namespace jarvis::speech {

Vad::Vad() = default;

void Vad::start_turn(const Turn& turn) {
    turn_ = turn;
    base_gate_ = std::max(kMinGate, turn_.threshold);
    start_gate_ = base_gate_;
    silence_gate_ = base_gate_;
    started_ = false;
    finished_ = false;
    last_event_ = VadEvent::Idle;
    voiced_ = 0.0;
    silence_ = 0.0;
    phrase_elapsed_ = 0.0;
    wait_elapsed_ = 0.0;
    pre_roll_.clear();
    frames_.clear();
    phrase_levels_.clear();
    // Python: deque(maxlen = max(1, int(0.3 / seconds))).
    const double seconds = std::max(0.001, turn_.chunk_seconds);
    pre_roll_max_ = std::max<std::size_t>(1, static_cast<std::size_t>(kPreRollSeconds / seconds));
}

VadEvent Vad::feed(double rms, double seconds, const std::string& bytes) {
    if (finished_) return last_event_;

    if (!started_) {
        // Jarvis konuşurken kendi sesini tabana katmamak için ölçüm duraklar.
        if (!turn_.speaking) {
            noise_samples_.push_back(rms);
            if (noise_samples_.size() > kNoiseKeep) noise_samples_.pop_front();
            if (noise_samples_.size() >= kNoiseMin) noise_floor_ = percentile_floor();
        }
        start_gate_ = std::max(base_gate_, noise_floor_ * kFloorRatio + kFloorOffset);
        pre_roll_.push_back(bytes);
        while (pre_roll_.size() > pre_roll_max_) pre_roll_.pop_front();

        if (rms >= start_gate_) {
            voiced_ += seconds;
            if (voiced_ >= kStartHold) {
                started_ = true;
                silence_gate_ = std::max({base_gate_ * 0.55, start_gate_ * 0.8, kSilenceFloor});
                frames_.assign(pre_roll_.begin(), pre_roll_.end());
                pre_roll_.clear();
                silence_ = 0.0;
                phrase_elapsed_ = 0.0;
                phrase_levels_.clear();
                last_event_ = VadEvent::SpeechStarted;
                return last_event_;
            }
        } else {
            voiced_ = 0.0;
            wait_elapsed_ += seconds;
            if (wait_elapsed_ >= std::max(0.05, turn_.timeout)) {
                started_ = false;
                finished_ = true;
                last_event_ = VadEvent::Timeout;
                return last_event_;
            }
        }
        return VadEvent::Idle;
    }

    frames_.push_back(bytes);
    phrase_elapsed_ += seconds;
    phrase_levels_.push_back(rms);

    if (rms >= silence_gate_) {
        silence_ = 0.0;
    } else {
        silence_ += seconds;
        if (silence_ >= turn_.pause_threshold) {
            finished_ = true;
            last_event_ = VadEvent::PhraseDone;
            return last_event_;
        }
    }

    // Tek düzen enerji konuşma değil, sürekli gürültüdür: vazgeç ve tabanı yükselt.
    const bool few_samples = noise_samples_.size() < kNoiseMin;
    if (phrase_elapsed_ >= kFlatSeconds && (few_samples || noise_floor_ >= base_gate_ * 0.9)) {
        std::vector<double> ordered = phrase_levels_;
        std::sort(ordered.begin(), ordered.end());
        const double median = ordered.empty() ? 0.0 : ordered[ordered.size() / 2];
        const double peak = *std::max_element(ordered.begin(), ordered.end());
        if (peak < median * kFlatRatio) {
            noise_floor_ = std::max(noise_floor_, median);
            started_ = false;
            frames_.clear();
            finished_ = true;
            last_event_ = VadEvent::AmbientOnly;
            return last_event_;
        }
    }

    if (phrase_elapsed_ >= turn_.phrase_limit) {
        finished_ = true;
        last_event_ = VadEvent::PhraseDone;
        return last_event_;
    }
    return VadEvent::Idle;
}

double Vad::percentile_floor() const {
    std::vector<double> ordered(noise_samples_.begin(), noise_samples_.end());
    std::sort(ordered.begin(), ordered.end());
    if (ordered.empty()) return 0.0;
    const std::size_t index = static_cast<std::size_t>(static_cast<double>(ordered.size()) * kNoiseSlice);
    return ordered[std::min(index, ordered.size() - 1)];
}

std::string Vad::take_audio() {
    std::string out;
    std::size_t total = 0;
    for (const std::string& frame : frames_) total += frame.size();
    out.reserve(total);
    for (const std::string& frame : frames_) out += frame;
    frames_.clear();
    return out;
}

void Vad::clear_audio() {
    frames_.clear();
}

double Vad::audio_seconds(double sample_rate, int channels, int bytes_per_sample) const {
    std::size_t bytes = 0;
    for (const std::string& frame : frames_) bytes += frame.size();
    const double denominator = std::max(1.0, sample_rate) * std::max(1, channels) * bytes_per_sample;
    return static_cast<double>(bytes) / denominator;
}

}  // namespace jarvis::speech
