/* movie_api.h - plain C access to media::MovieDecoder (for the runtime, which is C). Implemented in media.cpp.
 *
 * One handle per movie; a handle must be used from one thread at a time. */
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct MediaMovie MediaMovie;

/* Opens a movie (X360 WMV3 + WMA Pro, PC EA VP6, ...). Audio comes out as interleaved float with `channels`
 * channels (6: FL FR FC LFE BL BR, sources with fewer channels fill FL/FR; 2: ITU fold-down) at `rate` Hz.
 * NULL if the file can't be opened or has no video. */
MediaMovie *media_movie_open(const char *path, int channels, int rate);
/* The same with only some streams decoded (video, audio: 0/1, at least one). A player opens the file twice, one
 * handle per stream: the X360 WMVs store audio ~1.4 s later in the file than the video it plays with, so a single
 * demuxer would have to run that far ahead on the video. */
MediaMovie *media_movie_open_streams(const char *path, int channels, int rate, int video, int audio);
void media_movie_close(MediaMovie *m);
/* Audio-only handles: decodes audio (skipping other packets) until min_frames are queued. 0 at end of file (the
 * rest of the audio is then queued). */
int media_movie_pump_audio(MediaMovie *m, size_t min_frames);

/* Decodes up to the next video frame (audio met on the way is queued). Returns the frame as BGRA, width * 4 bytes
 * per row, valid until the next call; NULL at the end of the file. */
const uint8_t *media_movie_next_frame(MediaMovie *m, int *width, int *height, double *pts_seconds);
/* Moves up to max_frames of queued audio into dst; returns the frames moved. */
size_t media_movie_take_audio(MediaMovie *m, float *dst, size_t max_frames);

int media_movie_width(const MediaMovie *m);
int media_movie_height(const MediaMovie *m);
int media_movie_source_channels(const MediaMovie *m);  /* 0: no audio stream */
double media_movie_duration(const MediaMovie *m);      /* seconds */
const char *media_movie_describe(const MediaMovie *m);  /* "wmv3 1280x720, audio wmapro 6 ch 44100 Hz" */

#ifdef __cplusplus
}
#endif
