/* Offline AAC encoder availability: no audio devices, output only in memory.
 * Exit 0 after encoding a packet; exit 3 if the codec factory is unavailable.
 */
#include <AudioToolbox/AudioToolbox.h>
#include <stdint.h>
#include <stdio.h>

typedef struct {
    float pcm[8192 * 2];
    UInt32 consumed;
} Input;

static OSStatus input(AudioConverterRef converter, UInt32 *frames,
                      AudioBufferList *data, AudioStreamPacketDescription **packets,
                      void *context)
{
    (void)converter;
    (void)packets;
    Input *source = context;
    UInt32 left = 8192 - source->consumed;
    if (*frames > left) *frames = left;
    data->mNumberBuffers = 1;
    data->mBuffers[0].mNumberChannels = 2;
    data->mBuffers[0].mDataByteSize = *frames * 2 * sizeof(float);
    data->mBuffers[0].mData = source->pcm + source->consumed * 2;
    source->consumed += *frames;
    return noErr;
}

int main(void)
{
    AudioStreamBasicDescription pcm = {0}, aac = {0};
    pcm.mSampleRate = 48000;
    pcm.mFormatID = kAudioFormatLinearPCM;
    pcm.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked;
    pcm.mChannelsPerFrame = 2;
    pcm.mBytesPerPacket = pcm.mBytesPerFrame = 2 * sizeof(float);
    pcm.mFramesPerPacket = 1;
    pcm.mBitsPerChannel = 32;
    aac.mSampleRate = 48000;
    aac.mFormatID = kAudioFormatMPEG4AAC;
    aac.mFormatFlags = kMPEG4Object_AAC_LC;
    aac.mChannelsPerFrame = 2;
    aac.mFramesPerPacket = 1024;
    UInt32 size = sizeof(aac);
    OSStatus status = AudioFormatGetProperty(kAudioFormatProperty_FormatInfo, 0, NULL, &size, &aac);
    AudioConverterRef converter = NULL;
    if (!status) status = AudioConverterNew(&pcm, &aac, &converter);
    if (status) {
        printf("SKIP: AAC encoder unavailable: codec factory rc=%d\n", (int)status);
        return 3;
    }
    Input source = {0};
    uint8_t bytes[8192];
    AudioBufferList output = { .mNumberBuffers = 1,
        .mBuffers = {{ .mNumberChannels = 2, .mDataByteSize = sizeof(bytes), .mData = bytes }} };
    AudioStreamPacketDescription packet = {0};
    UInt32 count = 1;
    status = AudioConverterFillComplexBuffer(converter, input, &source, &count, &output, &packet);
    AudioConverterDispose(converter);
    if (status || count != 1 || output.mBuffers[0].mDataByteSize == 0) {
        printf("FAIL AAC encode: rc=%d packets=%u bytes=%u\n", (int)status,
               (unsigned)count, (unsigned)output.mBuffers[0].mDataByteSize);
        return 1;
    }
    printf("PASS AAC encode: packets=%u bytes=%u\n", (unsigned)count,
           (unsigned)output.mBuffers[0].mDataByteSize);
    return 0;
}
