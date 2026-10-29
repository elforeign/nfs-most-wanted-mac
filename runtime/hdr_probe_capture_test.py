"""Production probe readback recording, no game or GPU.
Three FP16 base/signature/after captures: normal unsafe_formats=0, short_rows=0,
converted=3, SDR_bytes=192; three SDR controls also preserve 192 bytes.
Omitted SDR conversion: unsafe_formats=3, short_rows=3, converted=0, exit 1.
Actual tone-map bytes are checked by hdr_output_test.py's native probe readback.
"""
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
probe = (RT / 'probe.m').read_text()
capture = probe[probe.index('static id<MTLBuffer> capture('):probe.index('// BGRA8 pixels:')]
# Keep production statements/selectors; replace protocol types with recording
# objects so this exercises the readback boundary without a Metal device.
for protocol, recording in (('MTLCommandBuffer', 'RecordCommand'), ('MTLTexture', 'RecordTexture'),
                            ('MTLBuffer', 'RecordBuffer'), ('MTLBlitCommandEncoder', 'RecordEncoder')):
    capture = capture.replace('id<' + protocol + '>', recording + ' *')

HARNESS = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>
@interface RecordTexture : NSObject
@property NSUInteger width, height;
@property MTLPixelFormat pixelFormat;
@property(nonatomic,strong) NSData *pixels;
@end
@implementation RecordTexture
@end
@interface RecordBuffer : NSObject
@property(nonatomic,strong) NSMutableData *data;
@end
@implementation RecordBuffer
@end
static unsigned unsafe_formats, short_rows, converted;
static RecordTexture *sdr;
@interface RecordDevice : NSObject
- (RecordBuffer *)newBufferWithLength:(NSUInteger)n options:(MTLResourceOptions)o;
@end
@implementation RecordDevice
- (RecordBuffer *)newBufferWithLength:(NSUInteger)n options:(MTLResourceOptions)o {
    (void)o;RecordBuffer *b=[RecordBuffer new];b.data=[NSMutableData dataWithLength:n];return b;
}
@end
@interface RecordEncoder : NSObject
- (void)copyFromTexture:(RecordTexture *)t sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l
    sourceOrigin:(MTLOrigin)o sourceSize:(MTLSize)z toBuffer:(RecordBuffer *)b
    destinationOffset:(NSUInteger)d destinationBytesPerRow:(NSUInteger)r destinationBytesPerImage:(NSUInteger)i;
- (void)endEncoding;
@end
@implementation RecordEncoder
- (void)copyFromTexture:(RecordTexture *)t sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l
    sourceOrigin:(MTLOrigin)o sourceSize:(MTLSize)z toBuffer:(RecordBuffer *)b
    destinationOffset:(NSUInteger)d destinationBytesPerRow:(NSUInteger)r destinationBytesPerImage:(NSUInteger)i {
    (void)s;(void)l;(void)o;(void)d;
    NSUInteger pixel=t.pixelFormat==MTLPixelFormatRGBA16Float?8:4;
    unsafe_formats+=pixel!=4;
    short_rows+=r<z.width*pixel || i<z.width*z.height*pixel || b.data.length<z.width*z.height*pixel;
    if(pixel==4 && b.data.length==t.pixels.length)memcpy(b.data.mutableBytes,t.pixels.bytes,t.pixels.length);
}
- (void)endEncoding {}
@end
@interface RecordCommand : NSObject
@property(nonatomic,strong) RecordDevice *device;
- (RecordEncoder *)blitCommandEncoder;
@end
@implementation RecordCommand
- (RecordEncoder *)blitCommandEncoder { return [RecordEncoder new]; }
@end
static RecordTexture *hdr_capture_source(RecordCommand *cmd, RecordTexture *t) {
    (void)cmd;if(t.pixelFormat!=MTLPixelFormatRGBA16Float)return t;
    ++converted;return sdr;
}
@CAPTURE@
int main(void) {@autoreleasepool{
    uint8_t pixels[64];for(unsigned i=0;i<sizeof pixels;++i)pixels[i]=i*3;
    sdr=[RecordTexture new];sdr.width=sdr.height=4;sdr.pixelFormat=MTLPixelFormatBGRA8Unorm;
    sdr.pixels=[NSData dataWithBytes:pixels length:sizeof pixels];
    RecordTexture *hdr=[RecordTexture new];hdr.width=hdr.height=4;hdr.pixelFormat=MTLPixelFormatRGBA16Float;
    unsigned different=0,bytes=0;
    for(int input=0;input<2;++input)for(int phase=0;phase<3;++phase) {
        RecordCommand *cmd=[RecordCommand new];cmd.device=[RecordDevice new];
        RecordBuffer *b=capture(cmd,input?sdr:hdr);
        different+=b.data.length!=64 || memcmp(b.data.bytes,pixels,64)!=0;bytes+=(unsigned)b.data.length;
    }
    printf("hdr_probe_capture: unsafe_formats=%u short_rows=%u converted=%u SDR_bytes=%u different=%u\n",
        unsafe_formats,short_rows,converted,bytes,different);
    return unsafe_formats || short_rows || converted!=3 || bytes!=384 || different ? 1:0;
}}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='hdr-probe-capture-') as tmp:
        tmp = Path(tmp)
        source, exe = tmp / 'test.m', tmp / 'test'
        for broken in (False, True):
            body = capture.replace('t = hdr_capture_source(cmd, t);', '') if broken else capture
            assert not broken or body != capture
            source.write_text(HARNESS.replace('@CAPTURE@', body))
            build = subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                '-fobjc-arc', str(source), '-framework', 'Foundation', '-o', str(exe)], capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-4000:]
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(run.stdout, end='')
            assert run.returncode == int(broken), run.stdout + run.stderr
            assert ('unsafe_formats=3 short_rows=3 converted=0' if broken else
                    'unsafe_formats=0 short_rows=0 converted=3') in run.stdout
    print('hdr_probe_capture_test: PASS normal=0 omitted-conversion=1')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
