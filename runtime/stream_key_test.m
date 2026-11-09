// Production secure controller, private key validation and public-sink checks.
// Original/fixed: real-editor paste 7/0 failures; six status reasons 6/0.
// Good: 630 checks/0 failures, 0 output matches; restart Go/sessions=0.
// Broken stale-release/OSK/field/AX/menu/preferences/clipboard/cid: 1/1/1/2/11/1/3/34 failures.
// Raw stderr fault: 1 sentinel match; good: 0. Driver redacts before saving.
#import <Cocoa/Cocoa.h>
#include <stdio.h>
#include "cliprec.h"
#include "livestream.h"
#include "onscreen_keyboard.h"
static int captureState, starts, stops, sessions, forwarded, clipboardWrites, prefWrites, clipboardReads;
static unsigned char passed[512]; static size_t passedSize;
static const char *terminalReason="";
void livestream_fail(int kind) { terminalReason=kind==LIVESTREAM_FAILURE_CAPTURE?"Error: capture":"Error: encoder"; }
void livestream_error_text(char *buf,size_t size) { snprintf(buf,size,"%s",terminalReason); }
int cliprec_live_state(void) { return captureState; }
int cliprec_live_start(void) { ++starts; captureState=1; return 1; }
void cliprec_live_stop(void) { ++stops; captureState=0; }
int livestream_start_bytes(const void *bytes,size_t count) { ++sessions; memcpy(passed,bytes,count); passedSize=count; return 1; }
void livestream_stop(void) {}
int livestream_status(void) { return captureState?1:0; }
// Private service doubles trap exports without touching system preferences/clipboard.
@interface KeyPreferences : NSObject
+ (instancetype)standardUserDefaults;
- (void)setObject:(id)value forKey:(NSString *)key;
@end
@implementation KeyPreferences
+ (instancetype)standardUserDefaults { return [self new]; }
- (void)setObject:(id)value forKey:(NSString *)key { (void)value; (void)key; ++prefWrites; }
@end
static NSString *clipboardInput;
@interface KeyPasteboard : NSObject
+ (instancetype)generalPasteboard;
- (BOOL)setString:(NSString *)value forType:(NSPasteboardType)type;
- (NSString *)stringForType:(NSPasteboardType)type;
@end
@implementation KeyPasteboard
+ (instancetype)generalPasteboard { return [self new]; }
- (BOOL)setString:(NSString *)value forType:(NSPasteboardType)type { (void)value; (void)type; ++clipboardWrites; return YES; }
- (NSString *)stringForType:(NSPasteboardType)type {
    if(![type isEqual:NSPasteboardTypeString]) return nil;
    ++clipboardReads; return clipboardInput;
}
@end
#define NSUserDefaults KeyPreferences
#define NSPasteboard KeyPasteboard
static void (^deliverEvent)(NSEvent *);
@interface KeyParent : NSWindow @end
@implementation KeyParent
- (void)beginSheet:(NSWindow *)sheet completionHandler:(void (^)(NSModalResponse))handler { (void)sheet; (void)handler; }
@end
@interface KeyApp : NSObject
@property NSWindow *keyWindow;
- (void)sendEvent:(NSEvent *)event;
@end
@implementation KeyApp
- (void)sendEvent:(NSEvent *)event { ++forwarded; if(deliverEvent) deliverEvent(event); }
@end
static KeyApp *fakeApp;
#undef NSApp
#define NSApp fakeApp
#ifndef STREAM_KEY_SOURCE
#define STREAM_KEY_SOURCE "stream_key.m"
#endif
#include STREAM_KEY_SOURCE
static int diag_on;
static int prompts_last_input(void) { return 1; }
static int portmenu_is_open(void) { return 1; }
static void diag_event(const char *fmt,...) { (void)fmt; }
#include STREAM_OSK_SOURCE
#include "report_privacy.h"
static int checks, failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL key line %d\n",__LINE__); } } while(0)
static NSEvent *event(unsigned code,BOOL down,NSString *characters,NSEventModifierFlags mods) {
    return [NSEvent keyEventWithType:down?NSEventTypeKeyDown:NSEventTypeKeyUp location:NSZeroPoint
        modifierFlags:mods timestamp:0 windowNumber:0 context:nil characters:characters
        charactersIgnoringModifiers:characters isARepeat:NO keyCode:code];
}
int main(int argc,char **argv) { @autoreleasepool {
    char input[2048]; if(!fgets(input,sizeof input,stdin)) return 2;
    input[strcspn(input,"\n")]=0;
    char marker[33]; memcpy(marker,input,32); marker[32]=0;
    NSString *sentinel=[NSString stringWithUTF8String:input];
    if(argc>1 && !strcmp(argv[1],"restart")) {
        char status[80]; stream_key_status(status,sizeof status);
        stream_key_go(0);
        CHECK(!keySize && !starts && !sessions && !strcmp(status,"No key"));
        printf("restart: automatic_go=%d sessions=%d checks=%d failures=%d\n",starts,sessions,checks,failures);
        return failures?1:0;
    }
    [NSApplication sharedApplication];
    fakeApp=[KeyApp new];
    fakeApp.keyWindow=[[KeyParent alloc] initWithContentRect:NSMakeRect(0,0,640,480)
        styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
    char menu[160]={0}; stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"No key"));
    stream_key_go(0); CHECK(!starts && !sessions);
    stream_key_edit(0); CHECK(panel && field && [field isKindOfClass:NSSecureTextField.class]);
    field.stringValue=sentinel;
    CHECK(![[field accessibilityValue] containsString:sentinel]);
    CHECK([[field.cell accessibilityValue] isEqual:@""]);
    CHECK(![[(StreamKeyField *)field accessibilityAttributedStringValue].string containsString:sentinel]);
    CHECK(![[panel title] containsString:sentinel]);
    CHECK(!starts && !sessions);
    NSTextField *closed=field;
    [controller save:nil]; CHECK(!panel && !field && keySize>95); CHECK(!closed.stringValue.length);
    CHECK(!strstr(note_original,marker) && !strstr(note.text,marker));
    CHECK(keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
    stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"Ready (Off)") && !strstr(menu,input));
    // Invalid input cannot truncate or overwrite a previously accepted key.
    CHECK(!replaceKey(@"")); CHECK(!replaceKey(@"one\ntwo"));
    unichar nul[3]={'a',0,'b'}; CHECK(!replaceKey([NSString stringWithCharacters:nul length:3]));
    CHECK(!replaceKey([@"x" stringByPaddingToLength:513 withString:@"x" startingAtIndex:0]));
    CHECK(keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
    stream_key_edit(0); field.stringValue=@"replacement"; [controller cancel:nil];
    CHECK(!panel && keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
    stream_key_edit(0); field.stringValue=sentinel;
    NSEvent *mouse=[NSEvent mouseEventWithType:NSEventTypeMouseMoved location:NSZeroPoint modifierFlags:0 timestamp:0 windowNumber:0 context:nil eventNumber:0 clickCount:0 pressure:0];
    CHECK(stream_key_route_event(mouse)); CHECK(forwarded==1); forwarded=0;
    CHECK(stream_key_route_event(event(8,YES,@"c",NSEventModifierFlagCommand)));
    CHECK(!forwarded); CHECK(stream_key_route_event(event(7,YES,@"x",NSEventModifierFlagCommand)));
    clipboardInput=sentinel; field.stringValue=@"";
    NSTextView *realEditor=(NSTextView *)field.currentEditor;
    CHECK(realEditor && [realEditor isKindOfClass:NSTextView.class]);
    realEditor.string=@""; realEditor.selectedRange=NSMakeRange(0,0);
    CHECK(stream_key_route_event(event(9,YES,@"v",NSEventModifierFlagCommand))); CHECK(!forwarded);
    CHECK([realEditor.string isEqual:sentinel]); CHECK([field.stringValue isEqual:sentinel]);
    CHECK(!clipboardWrites && !realEditor.allowsUndo);
    CHECK(clipboardReads==1);
    realEditor.selectedRange=NSMakeRange(0,realEditor.string.length);
    clipboardInput=@"plain replacement";
    CHECK(stream_key_route_event(event(9,YES,@"v",NSEventModifierFlagCommand)));
    CHECK([realEditor.string isEqual:@"plain replacement"] && clipboardReads==2);
    clipboardInput=nil;
    CHECK(stream_key_route_event(event(9,YES,@"v",NSEventModifierFlagCommand)));
    CHECK([realEditor.string isEqual:@"plain replacement"] && clipboardReads==3);
    CHECK(stream_key_route_event(event(6,YES,@"z",NSEventModifierFlagCommand)));
    CHECK(!forwarded && !clipboardWrites && !realEditor.allowsUndo);
    CHECK(!realEditor.menu && !realEditor.registeredDraggedTypes.count);
    CHECK(![realEditor validRequestorForSendType:NSPasteboardTypeString returnType:nil]);
    CHECK(stream_key_route_event(event(53,YES,@"",0)) && !panel);
    CHECK(stream_key_route_event(event(9,NO,@"v",0))); // consumed release after closing
    CHECK(stream_key_route_event(event(53,NO,@"",0)));
    CHECK(!stream_key_route_event(event(0,YES,@"a",0)));
    stream_key_edit(0); CHECK(stream_key_route_event(event(0,YES,@"a",0)));
    stream_key_close(); // focus change loses the old release
    NSEvent *repeat=[NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint modifierFlags:0 timestamp:0
        windowNumber:0 context:nil characters:@"a" charactersIgnoringModifiers:@"a" isARepeat:YES keyCode:0];
    CHECK(stream_key_route_event(repeat));
    CHECK(!stream_key_route_event(event(0,YES,@"a",0))); // fresh press starts a new cycle
    CHECK(!stream_key_route_event(event(0,NO,@"a",0))); // its release must reach the guest
    stream_key_go(0); stream_key_go(0); CHECK(starts==1 && sessions==1);
    CHECK(passedSize==strlen(input) && !memcmp(passed,input,passedSize));
    stream_key_edit(0); CHECK(!panel); CHECK(!replaceKey(@"replacement"));
    stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"Starting"));
    captureState=2; stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"Live"));
    captureState=4;
    for(const char **reason=(const char *[]){"Error: key rejected","Error: YouTube 400","Error: no response","Error: network","Error: encoder","Error: capture",NULL};*reason;++reason) {
        terminalReason=*reason; stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,*reason));
    }
    terminalReason="";
    stream_key_stop(0); CHECK(keySize==strlen(input));
    stream_key_go(0); CHECK(starts==2 && sessions==2);
    stream_key_forget(0); CHECK(!keySize && !captureState);
    for(size_t i=0;i<sizeof keyBytes;++i) CHECK(keyBytes[i]==0);
    stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"No key"));
    CHECK(!clipboardWrites && !prefWrites);
    // Every report attachment and issue-preview input passes the same production rule.
    NSString *encoded=[sentinel stringByAddingPercentEncodingWithAllowedCharacters:NSCharacterSet.alphanumericCharacterSet];
    for(NSString *name in @[@"report.txt",@"events.txt",@"log.txt",@"crash.txt",@"settings.txt",@"stats.txt",@"preview"]) {
        (void)name;
        for(NSString *inputText in @[
            [NSString stringWithFormat:@"https://a.upload.youtube.com/http_upload_hls?cid=%@&copy=0",encoded],
            [NSString stringWithFormat:@"cid=\"%@\"",encoded],
            [NSString stringWithFormat:@"%%63%%69%%64%%3D%@",encoded],
            [NSString stringWithFormat:@"\\u0063\\u0069\\u0064=%@",encoded],
            [NSString stringWithFormat:@"stream_key=\"%@\"",sentinel]]) {
            NSString *out=ReportEndpointRedact(inputText);
            CHECK(out && ![out containsString:sentinel] && ![out containsString:encoded]);
        }
    }
    NSString *bundle=[NSString stringWithUTF8String:argv[1]];
    CHECK([NSFileManager.defaultManager createDirectoryAtPath:bundle withIntermediateDirectories:YES attributes:nil error:nil]);
    for(NSString *name in @[@"report.txt",@"events.txt",@"log.txt",@"crash.txt",@"settings.txt",@"stats.txt"]) {
        NSString *path=[bundle stringByAppendingPathComponent:name];
        NSString *raw=[NSString stringWithFormat:@"cid=%@&copy=0\nbuild: fixture\n",encoded];
        CHECK([raw writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:nil]);
    }
    ReportPrivacyRedactBundle(bundle,@{});
    for(NSString *name in @[@"report.txt",@"events.txt",@"log.txt",@"crash.txt",@"settings.txt",@"stats.txt"]) {
        NSString *out=[NSString stringWithContentsOfFile:[bundle stringByAppendingPathComponent:name] encoding:NSUTF8StringEncoding error:nil];
        CHECK(out && ![out containsString:sentinel] && ![out containsString:encoded] && [out containsString:@"build: fixture"]);
    }
    // The generic note/OSK buffer remains unrelated to the secure editor.
    char osk[96]="public note"; osk_note_begin(osk,sizeof osk,0); osk_note_end(0);
    CHECK(!strcmp(osk,"public note") && !strstr(osk,input));
    CHECK(!strstr(note_original,input) && !strstr(note.text,input));
    // Exact boundary and multi-byte input validation.
    CHECK(replaceKey([@"x" stringByPaddingToLength:512 withString:@"x" startingAtIndex:0])); CHECK(keySize==512);
    CHECK(!replaceKey([@"é" stringByPaddingToLength:257 withString:@"é" startingAtIndex:0])); CHECK(keySize==512);
    stream_key_forget(0);
    printf("key: checks=%d failures=%d starts=%d clipboard_writes=%d preference_writes=%d\n",checks,failures,starts,clipboardWrites,prefWrites);
    return failures?1:0;
} }
