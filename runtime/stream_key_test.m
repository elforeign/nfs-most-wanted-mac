// Saved/editor storage is checked before the transport wait. Late Forget wipe:
// original/fixed 3/0 failures; saved key survives Stop. Good: 666 checks/0 failures.
// Production secure controller, private key validation and public-sink checks.
// Original/fixed: real-editor paste 7/0 failures; six status reasons 6/0.
// Hardening original/fixed: editor overwrite 7/0 failures, unclosed Stop 2/0, erasing Stop 3/0, quit 5/0.
// Good: 657 checks/0 failures (12 cleanup assertions), 0 output matches; restart Go/sessions=0.
// Original lifetime premise: stream_key.m:8 saved buffer survives Stop; :62
// validation scratch already erased; :75 field-value NSString may outlive Save
// in the event pool; :44 field editor + cell emptied/released without overwrite.
// :148 paste NSString survives insertion through its autorelease lifetime; the
// external clipboard remains owned by the user. :101 only registers atexit;
// app.m:445,505 do not wipe before exit. Replay uses pinned original bodies.
// Reachable field/editor values and full owned buffers are inspected; AppKit's
// retired immutable allocations cannot be proven erased by this test.
// Broken stale-release/OSK/field/AX/menu/preferences/clipboard/cid: 1/1/1/2/11/1/3/34 failures.
// Raw stderr fault: 1 sentinel match; good: 0. Driver redacts before saving.
#import <Cocoa/Cocoa.h>
#include <stdio.h>
#import <objc/runtime.h>
#include "cliprec.h"
#include "livestream.h"
#include "onscreen_keyboard.h"
static int captureState, starts, stops, sessions, forwarded, clipboardWrites, prefWrites, clipboardReads;
static unsigned char passed[512]; static size_t passedSize;
static const char *terminalReason="";
static void (^beforeTransportWait)(void);
void livestream_fail(int kind) { terminalReason=kind==LIVESTREAM_FAILURE_CAPTURE?"Error: capture":"Error: encoder"; }
void livestream_error_text(char *buf,size_t size) { snprintf(buf,size,"%s",terminalReason); }
int cliprec_live_state(void) { return captureState; }
int cliprec_live_start(void) { ++starts; captureState=1; return 1; }
void cliprec_live_stop(void) { ++stops; captureState=0; }
int livestream_start_bytes(const void *bytes,size_t count) { ++sessions; memcpy(passed,bytes,count); passedSize=count; return 1; }
void livestream_stop(void) {
    if(beforeTransportWait) beforeTransportWait();
    volatile unsigned char *p=passed;
    for(size_t i=0;i<sizeof passed;++i) p[i]=0;
    passedSize=0;
}
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
static const char *profile;
const char *userdata_dir(void) { return profile; }
#undef NSApp
#define NSApp fakeApp
#ifndef STREAM_KEY_SOURCE
#define STREAM_KEY_SOURCE "stream_key.m"
#endif
#include STREAM_KEY_SOURCE
// Run the production termination bodies without exiting this test process.
static void diag_begin_exit(void) {}
static int user32_request_close(void) { return 1; }
#define exit(code) ((void)(code))
#include STREAM_QUIT_SOURCE
#undef exit

// Observe the real secure editor's primitive: emptying must follow a full
// equal-length overwrite. Foundation's retired allocations are not inspectable.
static IMP storageReplace;
static unsigned storageOverwrites;
static void observeReplace(id storage,SEL selector,NSRange range,NSString *value) {
    if(range.location==0 && range.length==[storage length] && range.length &&
       value.length==range.length &&
       [value rangeOfCharacterFromSet:NSCharacterSet.whitespaceCharacterSet.invertedSet].location==NSNotFound)
        ++storageOverwrites;
    ((void(*)(id,SEL,NSRange,id))storageReplace)(storage,selector,range,value);
}
static int diag_on;
static int prompts_last_input(void) { return 1; }
static int portmenu_is_open(void) { return 1; }
static void diag_event(const char *fmt,...) { (void)fmt; }
#include STREAM_OSK_SOURCE
#include "report_privacy.h"
static int checks, failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL key line %d\n",__LINE__); } } while(0)
static void hardeningProof(NSString *sentinel) {
    unsigned before=failures;
    // Hold the real control and editor after each close, instead of testing nil.
    for(unsigned action=0;action<7;++action) {
        CHECK(replaceKey(sentinel,NO));
        stream_key_edit(0);
        NSTextField *heldField=field;
        NSTextView *heldEditor=(NSTextView *)field.currentEditor;
        heldEditor.string=sentinel;
        if(!storageReplace) {
            Method primitive=class_getInstanceMethod(heldEditor.textStorage.class,@selector(replaceCharactersInRange:withString:));
            storageReplace=method_setImplementation(primitive,(IMP)observeReplace);
        }
        unsigned overwritten=storageOverwrites;
        beforeTransportWait=^{
            if(action<3) return;
            unsigned residue=0;
            for(size_t i=0;i<sizeof keyBytes;++i) residue|=keyBytes[i];
            CHECK(!panel && ![heldEditor.textStorage.string containsString:sentinel] &&
                  ![heldField.stringValue containsString:sentinel]);
            if(action==3) CHECK(keySize && residue);
            else CHECK(!keySize && !residue);
        };
        if(action==0) [controller save:nil];
        else if(action==1) [controller cancel:nil];
        else if(action==2) stream_key_close();
        else if(action==3) stream_key_stop(0);
        else if(action==4) stream_key_forget(0);
        else if(action==5) {
            KeyQuitDelegate *quit=[KeyQuitDelegate new];
            [quit applicationShouldTerminate:nil];
        } else {
            KeyQuitDelegate *quit=[KeyQuitDelegate new];
            [quit applicationWillTerminate:nil];
        }
        CHECK(![heldEditor.textStorage.string containsString:sentinel] &&
              ![heldField.stringValue containsString:sentinel] && storageOverwrites>overwritten);
        if(action==3) {
            unsigned residual=0;
            for(size_t i=0;i<sizeof passed;++i) residual+=passed[i]!=0;
            CHECK(keySize==strlen(sentinel.UTF8String) &&
                  !memcmp(keyBytes,sentinel.UTF8String,keySize) && !residual && !passedSize && !panel);
        } else if(action>=4) {
            // Full buffer, including unused tail; don't mistake keySize=0 for erasure.
            unsigned residual=0;
            for(size_t i=0;i<sizeof keyBytes;++i) residual+=keyBytes[i]!=0;
            for(size_t i=0;i<sizeof passed;++i) residual+=passed[i]!=0;
            CHECK(!residual && !keySize && !passedSize && !panel);
        }
        beforeTransportWait=nil;
        stream_key_forget(0);
    }
    CHECK(replaceKey(sentinel,NO));
    quit_now(); // the immediate-exit fallback also wipes before exit(0)
    unsigned residue=0;
    for(size_t i=0;i<sizeof keyBytes;++i) residue+=keyBytes[i]!=0;
    CHECK(!residue && !keySize);
    printf("hardening editor/stop/forget/quit: checks=12 failures=%u\n",failures-before);
}
static NSEvent *event(unsigned code,BOOL down,NSString *characters,NSEventModifierFlags mods) {
    return [NSEvent keyEventWithType:down?NSEventTypeKeyDown:NSEventTypeKeyUp location:NSZeroPoint
        modifierFlags:mods timestamp:0 windowNumber:0 context:nil characters:characters
        charactersIgnoringModifiers:characters isARepeat:NO keyCode:code];
}
static void setupSheet(void) {
    [NSApplication sharedApplication];
    fakeApp=[KeyApp new];
    fakeApp.keyWindow=[[KeyParent alloc] initWithContentRect:NSMakeRect(0,0,640,480)
        styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
}
static int storageProof(const char *mode, NSString *sentinel, const char *input) {
    char status[80];
    stream_key_status(status,sizeof status);
    if(!strcmp(mode,"remember") || !strcmp(mode,"session-only")) {
        setupSheet();
        stream_key_edit(0);
        CHECK(remember.state==NSControlStateValueOn);
        if(!strcmp(mode,"session-only")) remember.state=NSControlStateValueOff;
        field.stringValue=sentinel;
        [controller save:nil];
        CHECK(!panel && keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
        [[KeyQuitDelegate new] applicationShouldTerminate:nil];
        stream_key_status(status,sizeof status);
        CHECK(!keySize && !strcmp(status,"No key"));
    } else if(!strcmp(mode,"forget")) {
        CHECK(keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
        stream_key_forget(0);
        stream_key_status(status,sizeof status);
        CHECK(!keySize && !strcmp(status,"No key"));
    } else if(!strcmp(mode,"relaunch")) {
        CHECK(keySize==strlen(input) && !memcmp(keyBytes,input,keySize) && !strcmp(status,"Ready (Off)"));
    } else if(!strcmp(mode,"contended-save")) {
        CHECK(!replaceKey(@"replacement",YES));
    } else {
        const char *expected=!strcmp(mode,"unavailable")?"Saved key unavailable":"No key";
        CHECK(!keySize && !strcmp(status,expected));
    }
    CHECK(!starts && !sessions);
    printf("%s: key_present=%d go=%d sessions=%d status=%s failures=%d\n",
           mode,keySize!=0,starts,sessions,status,failures);
    return failures?1:0;
}
int main(int argc,char **argv) { @autoreleasepool {
    if(argc!=3 && argc!=4) return 2;
    profile=argv[2];
    char input[2048]; if(!fgets(input,sizeof input,stdin)) return 2;
    input[strcspn(input,"\n")]=0;
    char marker[33]; memcpy(marker,input,32); marker[32]=0;
    NSString *sentinel=[NSString stringWithUTF8String:input];
    if(argc==4) return storageProof(argv[3],sentinel,input);
    setupSheet();
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
    CHECK(!replaceKey(@"",NO)); CHECK(!replaceKey(@"one\ntwo",NO));
    unichar nul[3]={'a',0,'b'}; CHECK(!replaceKey([NSString stringWithCharacters:nul length:3],NO));
    CHECK(!replaceKey([@"x" stringByPaddingToLength:513 withString:@"x" startingAtIndex:0],NO));
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
    stream_key_edit(0); CHECK(!panel); CHECK(!replaceKey(@"replacement",NO));
    stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"Starting"));
    captureState=2; stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,"Live"));
    captureState=4;
    for(const char **reason=(const char *[]){"Error: key rejected","Error: YouTube 400","Error: no response","Error: network","Error: encoder","Error: capture",NULL};*reason;++reason) {
        terminalReason=*reason; stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,*reason));
        CHECK(keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
    }
    terminalReason="";
    stream_key_stop(0); CHECK(keySize==strlen(input) && !memcmp(keyBytes,input,keySize));
    CHECK(!passedSize);
    terminalReason="Error: no response"; stream_key_status(menu,sizeof menu); CHECK(!strcmp(menu,terminalReason));
    terminalReason="";
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
    CHECK(replaceKey([@"x" stringByPaddingToLength:512 withString:@"x" startingAtIndex:0],NO)); CHECK(keySize==512);
    CHECK(!replaceKey([@"é" stringByPaddingToLength:257 withString:@"é" startingAtIndex:0],NO)); CHECK(keySize==512);
    stream_key_forget(0);
    hardeningProof(sentinel);
    printf("key: checks=%d failures=%d starts=%d clipboard_writes=%d preference_writes=%d\n",checks,failures,starts,clipboardWrites,prefWrites);
    return failures?1:0;
} }
