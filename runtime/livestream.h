// Ordered HTTPS ingest. Activation is explicit and memory-only.
#pragma once
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
int livestream_start_bytes(const void *bytes, size_t size);
// Test only (diag runs): Go with a fixed internal key to http://127.0.0.1:<port>/http_upload_hls.
int livestream_start_fake(int port);
void livestream_stop(void);
// 0 Off, 1 running, 2 terminal error. No request objects cross this boundary.
int livestream_status(void);
enum { LIVESTREAM_FAILURE_ENCODER=1, LIVESTREAM_FAILURE_CAPTURE=2 };
// Preserve a terminal reason after capture detaches; a new Go clears it.
void livestream_fail(int kind);
// The same with the cause: a nonzero encoder status, else a static reason token (may be null).
void livestream_fail_reason(int kind, int code, const char *reason);
void livestream_error_text(char *buf, size_t size);
#ifdef __cplusplus
}
#include "live_seg.h"
#include <string>

namespace live {
enum class StreamError { none, stopped, overload, deadline, transport, http, redirect, responseTooLarge, keyRejected, encoder, capture };
struct StreamStats {
    size_t bytes=0, peakBytes=0;
    int64_t ticks=0, peakTicks=0;
    uint64_t accepted=0, posted=0, cancelled=0, dropped=0, failed=0;
    uint64_t playlists=0, retries=0;
    int httpCode=0;
    const char *reason=nullptr; // static token naming a capture-side failure
    bool running=false;
    StreamError error=StreamError::none;
};
#ifdef LIVESTREAM_TESTING
struct StreamTestOptions {
    std::string baseURL;
    std::vector<uint8_t> anchorDER;
    double deadlineSeconds=3;
};
#endif
struct StreamState;
class Poster {
    std::shared_ptr<StreamState> state;
    explicit Poster(std::shared_ptr<StreamState>);
public:
    static constexpr size_t maxBytes=16*1024*1024;
    static constexpr size_t maxResponseBytes=4096;
    static constexpr double deadlineSeconds=3;
    // Empty keys and invalid destinations return null, without creating a session.
    static std::shared_ptr<Poster> create(const std::string& key
#ifdef LIVESTREAM_TESTING
        , const StreamTestOptions& options={}
#endif
    );
    ~Poster();
    bool post(OwnedSegment); // false only when stopped; drops count without retiring the producer
    void stop();            // asynchronous cancellation, no automatic restart
    void fail(StreamError,int code=0,const char *reason=nullptr);
    StreamStats stats() const;
};
// Explicit start/stop. Each output callback belongs to one poster generation.
bool startStream(const std::string& key);
void stopStream();
Segmenter::Output streamOutput();
}
#endif
