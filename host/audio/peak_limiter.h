// Linked game-mix sample-peak limiter; no allocations in process(). Movies never enter this path.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

class PeakLimiter {
public:
    static constexpr float threshold = 0.891250938f; // -1 dBFS
    void reset(int rate) {
        rate_ = rate;
        delay_ = std::max(1, rate / 500); // <= 2 ms at the supported audio rates
        delay_pcm_.assign(delay_ * 6, 0.0f);
        peaks_.resize(delay_ + 2);
        head_ = count_ = pos_ = 0;
        frame_ = 0;
        gain_ = 1.0;
        release_frames_ = std::max(1, rate * 150 / 1000);
        release_left_ = release_frames_;
    }
    int rate() const { return rate_; }
    int delay() const { return delay_; }
    // Returns temporal frames with gain < 1 (all six channels share that gain).
    uint64_t process(float *pcm, size_t frames) {
        uint64_t limited = 0;
        const size_t cap = peaks_.size();
        for (size_t i = 0; i < frames; ++i, ++frame_) {
            float *s = pcm + i * 6;
            float peak = 0;
            for (int c = 0; c < 6; ++c) peak = std::max(peak, std::fabs(s[c]));
            // Monotone deque: maximum over the delayed output through the newest input.
            // Each entry is inserted/removed once: amortised O(1), independent of look-ahead length.
            while (count_ && peaks_[head_].frame + delay_ < frame_) {
                head_ = (head_ + 1) % cap; --count_;
            }
            while (count_ && peaks_[(head_ + count_ - 1) % cap].peak <= peak) --count_;
            peaks_[(head_ + count_) % cap] = {frame_, peak}; ++count_;
            const double maximum = peaks_[head_].peak;
            const double target = maximum > threshold ? threshold / maximum : 1.0;
            if (target <= gain_) {
                // A full-scale gain descent takes at most the look-ahead: the offending frame
                // cannot reach the output before its safe gain. No hard safety clamp is needed.
                gain_ = std::max(target, gain_ - 1.0 / delay_);
                release_left_ = release_frames_;
            } else {
                // Finite 150 ms linear release, ending at exactly unity (no asymptotic quiet gain).
                gain_ = std::min(target, release_left_ <= 1 ? 1.0 : gain_ + (1.0 - gain_) / release_left_);
                if (release_left_ > 1) --release_left_;
            }
            limited += gain_ < 1.0;
            float *old = delay_pcm_.data() + pos_ * 6;
            for (int c = 0; c < 6; ++c) {
                float input = s[c];
#ifdef NFS_LIMITER_BROKEN
                s[c] = input * (float)gain_; // negative control: attack with no audio look-ahead
#else
                s[c] = old[c] * (float)gain_;
#endif
                old[c] = input;
            }
            if (++pos_ == delay_) pos_ = 0;
        }
        return limited;
    }
private:
    struct Peak { uint64_t frame; float peak; };
    std::vector<float> delay_pcm_;
    std::vector<Peak> peaks_;
    size_t head_ = 0, count_ = 0;
    uint64_t frame_ = 0;
    int rate_ = 0, delay_ = 0, pos_ = 0, release_frames_ = 0, release_left_ = 0;
    double gain_ = 1.0;
};
