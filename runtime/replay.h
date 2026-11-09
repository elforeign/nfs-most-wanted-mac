// Compressed replay retention and exact, asynchronous save-last-30 export.
#pragma once
#include <CoreMedia/CoreMedia.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <vector>
#include <condition_variable>
#include <string>
#include <chrono>
namespace clip {
class Packet;
class Fanout;
class Sink;
enum class Result;
using OwnedPacket=std::shared_ptr<const Packet>;
}
namespace replay {
constexpr size_t hardCap = 256ull*1024*1024;
constexpr size_t transferCap = 16ull*1024*1024;
constexpr size_t retentionCap = hardCap-transferCap;
constexpr int prerollFrames = 2112; // AAC-LC decoder warm-up at 48 kHz
struct Stats {
    bool armed=false;
    uint64_t generation=0, evicted=0, rejected=0;
    size_t bytes=0, peakBytes=0, gops=0, packets=0;
    CMTime first=kCMTimeInvalid, end=kCMTimeInvalid;
};
struct Snapshot {
    std::vector<clip::OwnedPacket> packets;
    CMTime start=kCMTimeInvalid, end=kCMTimeInvalid;
    size_t bytes=0;
    std::string error;
};
struct SavePoint {
    CMTime start=kCMTimeInvalid, end=kCMTimeInvalid;
    uint64_t token=0, lifetime=0, generation=0;
    std::chrono::steady_clock::time_point deadline;
    std::string error;
};
class Ring {
    struct State;
    mutable std::mutex lock;
    std::unique_ptr<State> state;
    std::atomic<bool> armed{false};
    std::condition_variable changed;
    CMTime pinnedStart=kCMTimeInvalid;
    uint64_t lifetime=0;
    uint64_t saveToken=0;
    clip::Result consumeArmed(const clip::OwnedPacket&) __attribute__((noinline));
public:
    Ring(); // Initially Off; no retention storage or consumer thread.
    ~Ring();
    void arm();
    void disarm(); // releases all retained samples and formats
    clip::Result consume(const clip::OwnedPacket&);
    Stats stats() const;
    // Inspection copies immutable references under the lock, without file I/O.
    std::vector<clip::OwnedPacket> inspect() const;
    Snapshot snapshot(CMTime end, double timeout=2.0);
    SavePoint reserve(CMTime end, double timeout=2.0); // pin before scheduling file output
    Snapshot snapshot(const SavePoint&);
};
// One capture-setup hook. NFS_REPLAY=on explicitly arms a fresh ring for
// this packet stream; unset/off creates no ring, queue or thread.
std::shared_ptr<Ring> attach(clip::Fanout&, std::shared_ptr<Ring> ring={}, std::shared_ptr<clip::Sink>* sink=nullptr);
class Export {
    struct State;
    std::shared_ptr<State> state;
public:
    Export();
    // Latch end on the action thread. One bounded snapshot/export at a time.
    bool save(const std::shared_ptr<Ring>&, CMTime end, const std::string& folder);
    bool busy() const;
    std::string status() const;
    bool wait(double seconds) const;
};
}
