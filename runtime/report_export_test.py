"""Guarded report export: original 10 checks / 7 failures; fixed 10 / 0.
Original live export retained 0 text attachments; fixed live export retains 7.
The platform seam denies Foundation atomic replacement, while permitting writes
inside the report folder. Total write denial still removes every text attachment.
"""
import hashlib
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
OLD = ('atomically:YES encoding:NSUTF8StringEncoding error:NULL',
       'options:NSDataWritingAtomic error:NULL',
       'atomically:YES encoding:NSUTF8StringEncoding error:NULL')
assert hashlib.sha256('\n'.join(OLD).encode()).hexdigest() == 'af0d4b46374a5da73f6491b4ae0c401375a2c54a9925187226b15a6aacfaf53a'
header = (RT / 'report_privacy.h').read_text()
# Only machine identity discovery is injected; decode, redaction and export run unchanged.
a = header.index('static NSDictionary *ReportPrivacyLocalIdentity(void) {')
b = header.index('// Report notes use Latin-1', a)
header = header[:a] + 'static NSDictionary *ReportPrivacyLocalIdentity(void) { return @{}; }\n\n' + header[b:]
PRELUDE = r'''
#import <Cocoa/Cocoa.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
static int denyAll, atomicDenied;
static BOOL refused(BOOL atomic, NSError **error) {
    if (!atomic && !denyAll) return NO;
    if (atomic) ++atomicDenied;
    if (error) *error=[NSError errorWithDomain:NSCocoaErrorDomain code:NSFileWriteNoPermissionError userInfo:nil];
    return YES;
}
@interface NSString (ExportFixture)
- (BOOL)fixtureWriteToFile:(NSString *)path atomically:(BOOL)atomic encoding:(NSStringEncoding)encoding error:(NSError **)error;
@end
@implementation NSString (ExportFixture)
- (BOOL)fixtureWriteToFile:(NSString *)path atomically:(BOOL)atomic encoding:(NSStringEncoding)encoding error:(NSError **)error {
    return refused(atomic,error) ? NO : [self writeToFile:path atomically:NO encoding:encoding error:error];
}
@end
@interface NSData (ExportFixture)
- (BOOL)fixtureWriteToFile:(NSString *)path options:(NSDataWritingOptions)options error:(NSError **)error;
@end
@implementation NSData (ExportFixture)
- (BOOL)fixtureWriteToFile:(NSString *)path options:(NSDataWritingOptions)options error:(NSError **)error {
    return refused((options & NSDataWritingAtomic)!=0,error) ? NO : [self writeToFile:path options:0 error:error];
}
@end
#define writeToFile fixtureWriteToFile
'''
MAIN = r'''
#undef writeToFile
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d\n",__LINE__); } } while(0)
static void put(NSString *path, const char *bytes) {
    FILE *f=fopen(path.fileSystemRepresentation,"w"); if(!f)abort(); fputs(bytes,f); fclose(f);
}
static NSString *ReadText(NSString *path) {
    return [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL];
}
int main(int argc,char **argv) { @autoreleasepool {
    if(argc!=2)return 2;
    NSString *root=[NSString stringWithUTF8String:argv[1]];
    NSArray *names=@[@"report.txt",@"settings.txt",@"events.txt",@"stats.txt",@"blackbox.txt"];
    const char *payload="Authorization: Bearer fixture-secret-value\n";
    for(NSString *name in names) put([root stringByAppendingPathComponent:name],payload);
    NSString *png=[root stringByAppendingPathComponent:@"screenshot.png"];
    put(png,"binary fixture");
    ReportPrivacyRedactBundle(root,@{});
    for(NSString *name in names) {
        NSString *text=ReadText([root stringByAppendingPathComponent:name]);
        CHECK(text && [text containsString:@"[authorization]"] && ![text containsString:@"fixture-secret-value"]);
    }
    CHECK([ReadText(png) isEqualToString:@"binary fixture"]);
    NSString *source=[root stringByAppendingPathComponent:@"source.log"];
    NSString *dest=[root stringByAppendingPathComponent:@"log.txt"];
    put(source,payload);
    ReportPrivacyCopyLog(source.fileSystemRepresentation,dest.fileSystemRepresentation,256*1024);
    NSString *text=ReadText(dest);
    CHECK(text && [text containsString:@"[authorization]"] && ![text containsString:@"fixture-secret-value"]);
    int fd=open(source.fileSystemRepresentation,O_WRONLY); if(fd<0)abort();
    if(ftruncate(fd,1024*1024+1))abort(); close(fd);
    ReportPrivacyCopyLog(source.fileSystemRepresentation,dest.fileSystemRepresentation,256*1024);
    CHECK([ReadText(dest) hasPrefix:@"Log omitted:"]);
    // Refusing even direct writes must not retain raw credentials.
    denyAll=1;
    for(NSString *name in names) put([root stringByAppendingPathComponent:name],payload);
    ReportPrivacyRedactBundle(root,@{});
    int left=0;
    for(NSString *name in names) left+=ReadText([root stringByAppendingPathComponent:name])!=nil;
    CHECK(left==0);
    put(source,payload); put(dest,payload);
    ReportPrivacyCopyLog(source.fileSystemRepresentation,dest.fileSystemRepresentation,256*1024);
    CHECK(ReadText(dest)==nil);
    printf("%d checks / %d failures; %d atomic writes denied\n",checks,failures,atomicDenied);
    return failures ? 1 : 0;
} }
'''
with tempfile.TemporaryDirectory(prefix='report-export-') as tmp:
    work = Path(tmp)
    for original in (True, False):
        label = 'original' if original else 'fixed'
        code = header
        if original:
            for current, old in zip((
                'atomically:NO encoding:NSUTF8StringEncoding error:NULL',
                'options:0 error:NULL',
                'atomically:NO encoding:NSUTF8StringEncoding error:&error'), OLD):
                assert code.count(current) == 1, current
                code = code.replace(current, old)
        src, exe = work / (label + '.m'), work / label
        src.write_text(PRELUDE + code + MAIN)
        compiled = subprocess.run(['clang', '-O1', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                   str(src), '-framework', 'Cocoa', '-framework', 'SystemConfiguration',
                                   '-framework', 'IOKit', '-o', str(exe)], capture_output=True, text=True, timeout=90)
        assert compiled.returncode == 0, compiled.stderr[-3000:]
        folder = work / (label + '-report'); folder.mkdir()
        result = subprocess.run([str(exe), str(folder)], capture_output=True, text=True, timeout=15)
        print(label + ': ' + result.stdout.strip())
        assert f'10 checks / {7 if original else 0} failures' in result.stdout, result.stderr
        assert result.returncode == int(original), result.stderr
