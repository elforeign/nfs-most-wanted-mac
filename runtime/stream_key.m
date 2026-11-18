// Secure entry stays outside settings, guest memory and diagnostic serialization.
#import <Cocoa/Cocoa.h>
#include <CommonCrypto/CommonDigest.h>
#include "stream_key.h"
#include "livestream.h"
#include "cliprec.h"
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/acl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __cplusplus
extern "C"
#endif
const char *userdata_dir(void);

static unsigned char keyBytes[512];
static size_t keySize;
static BOOL loaded;
static const char *notice;
static NSPanel *panel;
static NSSecureTextField *field;
static NSTextField *message;
static NSButton *remember;
static unsigned char consumed[256];

static void erase(void *bytes, size_t count) {
    volatile unsigned char *p=bytes;
    while(count--) *p++=0;
}
static int busy(void) { return cliprec_live_state()==1 || cliprec_live_state()==2 || cliprec_live_state()==3; }

// Relative operations keep all file access inside the checked private
// directory, even if a path is replaced.
static BOOL ownerOnly(int fd, BOOL directory) {
    struct stat st;
    if(fstat(fd,&st) || st.st_uid!=geteuid() || (st.st_mode&077)) return NO;
    if(directory ? !S_ISDIR(st.st_mode) : (!S_ISREG(st.st_mode) || st.st_nlink!=1)) return NO;
    acl_t acl=acl_get_fd_np(fd,ACL_TYPE_EXTENDED);
    if(!acl) return errno==ENOENT;
    acl_free(acl);
    return NO;
}

static int storeDirectory(BOOL create) {
    int root=open(userdata_dir(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(root<0) return -1;
    int fd=-1;
    if(!create || mkdirat(root,STREAM_KEY_DIR,0700)==0 || errno==EEXIST)
        fd=openat(root,STREAM_KEY_DIR,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    int error=errno;
    close(root);
    if(fd<0) { errno=error; return -1; }
    if(ownerOnly(fd,YES)) return fd;
    close(fd);
    errno=EACCES;
    return -1;
}

static BOOL removeFile(int dir, const char *name) {
    return unlinkat(dir,name,0)==0 || errno==ENOENT;
}

static int lockStore(BOOL create) {
    int dir=storeDirectory(create);
    if(dir<0 || flock(dir,LOCK_EX|LOCK_NB)==0) return dir;
    int error=errno;
    close(dir);
    errno=error;
    return -1;
}

static BOOL removeStoredKey(void) {
    int dir=lockStore(NO);
    if(dir<0) return errno==ENOENT;
    BOOL removed=removeFile(dir,STREAM_KEY_FILE);
    BOOL cleaned=removeFile(dir,STREAM_KEY_TEMP);
    close(dir);
    return removed && cleaned;
}

static BOOL writeStoredKey(const unsigned char *bytes, size_t size) {
    int dir=lockStore(YES);
    if(dir<0) return NO;
    int fd=-1;
    if(removeFile(dir,STREAM_KEY_TEMP))
        fd=openat(dir,STREAM_KEY_TEMP,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    BOOL saved=NO;
    if(fd>=0) {
        // The digest detects truncated or damaged records. The key follows it as UTF-8.
        unsigned char record[CC_SHA256_DIGEST_LENGTH+sizeof keyBytes];
        CC_SHA256(bytes,(CC_LONG)size,record);
        memcpy(record+CC_SHA256_DIGEST_LENGTH,bytes,size);
        size_t length=CC_SHA256_DIGEST_LENGTH+size, offset=0;
        if(fchmod(fd,0600)==0 && ownerOnly(fd,NO)) {
            while(offset<length) {
                ssize_t n=write(fd,record+offset,length-offset);
                if(n<0 && errno==EINTR) continue;
                if(n<=0) break;
                offset+=(size_t)n;
            }
        }
        erase(record,sizeof record);
        BOOL complete=offset==length && fsync(fd)==0;
        if(close(fd)==0 && complete)
            saved=renameat(dir,STREAM_KEY_TEMP,dir,STREAM_KEY_FILE)==0;
        if(!saved) unlinkat(dir,STREAM_KEY_TEMP,0);
    }
    close(dir);
    return saved;
}

static BOOL validKeyText(NSString *value) {
    return value.length &&
        [value rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location==NSNotFound;
}

static void loadStoredKey(void) {
    if(loaded) return;
    loaded=YES;
    atexit(stream_key_quit);
    int dir=storeDirectory(NO);
    if(dir<0) {
        if(errno!=ENOENT) notice="Saved key unavailable";
        return;
    }
    int fd=openat(dir,STREAM_KEY_FILE,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    int error=errno;
    close(dir);
    if(fd<0) {
        if(error!=ENOENT) notice="Saved key unavailable";
        return;
    }

    // Read one byte beyond the maximum to detect an oversized record.
    unsigned char record[CC_SHA256_DIGEST_LENGTH+sizeof keyBytes+1];
    size_t length=0;
    if(ownerOnly(fd,NO)) {
        while(length<sizeof record) {
            ssize_t n=read(fd,record+length,sizeof record-length);
            if(n<0 && errno==EINTR) continue;
            if(n<0) length=0;
            if(n<=0) break;
            length+=(size_t)n;
        }
    }
    close(fd);
    if(length>CC_SHA256_DIGEST_LENGTH && length<sizeof record) {
        unsigned char *bytes=record+CC_SHA256_DIGEST_LENGTH;
        size_t size=length-CC_SHA256_DIGEST_LENGTH;
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256(bytes,(CC_LONG)size,digest);
        if(!memcmp(digest,record,sizeof digest)) {
            @autoreleasepool {
                NSString *value=[[NSString alloc] initWithBytes:bytes length:size encoding:NSUTF8StringEncoding];
                if(validKeyText(value)) {
                    memcpy(keyBytes,bytes,size);
                    keySize=size;
                }
            }
        }
        erase(digest,sizeof digest);
    }
    erase(record,sizeof record);
    if(!keySize) notice="Saved key unavailable";
}

@interface StreamKeyCell : NSSecureTextFieldCell @end
@implementation StreamKeyCell
- (id)accessibilityValue { return @""; }
@end
@interface StreamKeyField : NSSecureTextField
@end
@implementation StreamKeyField
+ (Class)cellClass { return StreamKeyCell.class; }
- (id)accessibilityValue { return @""; }
- (NSAttributedString *)accessibilityAttributedStringValue { return [[NSAttributedString alloc] initWithString:@""]; }
- (id)validRequestorForSendType:(NSPasteboardType)s returnType:(NSPasteboardType)r { (void)s; (void)r; return nil; }
@end

@interface StreamKeyController : NSObject <NSWindowDelegate, NSTextFieldDelegate>
- (void)save:(id)sender;
- (void)cancel:(id)sender;
@end
static StreamKeyController *controller;

void stream_key_close(void) {
    if(!panel) return;
    @autoreleasepool {
        NSTextView *editor=(NSTextView *)field.currentEditor;
        // Overwrite the reachable storage before releasing the editor/cell values.
        NSUInteger length=editor.textStorage.length;
        NSString *blank=[@"" stringByPaddingToLength:length withString:@" " startingAtIndex:0];
        [editor.textStorage replaceCharactersInRange:NSMakeRange(0,length) withString:blank];
        [editor.textStorage replaceCharactersInRange:NSMakeRange(0,length) withString:@""];
        [editor.undoManager removeAllActions];
        field.stringValue=[@"" stringByPaddingToLength:field.stringValue.length withString:@" " startingAtIndex:0];
        field.stringValue=@"";
        [panel makeFirstResponder:nil];
        NSWindow *parent=panel.sheetParent;
        if(parent) [parent endSheet:panel];
        [panel orderOut:nil];
        panel.delegate=nil; field.delegate=nil;
        panel=nil; field=nil; message=nil; remember=nil; controller=nil;
    }
}
static BOOL replaceKey(NSString *value, BOOL rememberOnMac) {
    // Validate the entire UTF-8 value; never truncate or normalize accepted bytes.
    if(busy()) { notice="Stop live first"; return NO; }
    NSUInteger length=[value lengthOfBytesUsingEncoding:NSUTF8StringEncoding];
    if(!length || length>sizeof keyBytes || !validKeyText(value)) {
        notice="Error: invalid key"; return NO;
    }
    unsigned char temporary[512]={0};
    NSUInteger used=0;
    BOOL valid=[value getBytes:temporary maxLength:sizeof temporary usedLength:&used
        encoding:NSUTF8StringEncoding options:0 range:NSMakeRange(0,value.length) remainingRange:NULL];
    BOOL saved=NO;
    if(!valid || used!=length) notice="Error: invalid key";
    else {
        saved=rememberOnMac ? writeStoredKey(temporary,used) : removeStoredKey();
        if(saved) {
            erase(keyBytes,sizeof keyBytes); memcpy(keyBytes,temporary,used); keySize=used; notice=NULL;
        } else notice=rememberOnMac ? "Couldn't remember the key." : "Couldn't remove the saved key.";
    }
    erase(temporary,sizeof temporary);
    return saved;
}
@implementation StreamKeyController
- (void)save:(id)sender {
    (void)sender;
    @autoreleasepool {
        if(replaceKey(field.stringValue,remember.state==NSControlStateValueOn)) stream_key_close();
        else message.stringValue=!strcmp(notice,"Error: invalid key")
            ? @"Enter 1–512 UTF-8 bytes without control characters."
            : [NSString stringWithUTF8String:notice];
    }
}
- (void)cancel:(id)sender { (void)sender; stream_key_close(); }
- (BOOL)windowShouldClose:(NSWindow *)sender { (void)sender; stream_key_close(); return NO; }
- (BOOL)control:(NSControl *)control textView:(NSTextView *)view doCommandBySelector:(SEL)selector {
    (void)control; (void)view;
    if(selector==@selector(insertNewline:)) { [self save:nil]; return YES; }
    if(selector==@selector(cancelOperation:)) { [self cancel:nil]; return YES; }
    return selector==@selector(copy:) || selector==@selector(cut:) || selector==@selector(complete:);
}
- (NSArray *)control:(NSControl *)control textView:(NSTextView *)view completions:(NSArray *)words
    forPartialWordRange:(NSRange)range indexOfSelectedItem:(NSInteger *)index {
    (void)control; (void)view; (void)words; (void)range; (void)index; return @[];
}
@end

void stream_key_edit(int unused) {
    (void)unused;
    if(!NSThread.isMainThread) return;
    if(busy()) { notice="Stop live first"; return; }
    if(panel) return;
    NSWindow *parent=NSApp.keyWindow;
    if(!parent) { notice="Error: editor unavailable"; return; }
    loadStoredKey();
    panel=[[NSPanel alloc] initWithContentRect:NSMakeRect(0,0,440,180)
        styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable backing:NSBackingStoreBuffered defer:NO];
    panel.title=@"Stream key"; panel.restorable=NO; panel.releasedWhenClosed=NO;
    controller=[StreamKeyController new]; panel.delegate=controller;
    message=[NSTextField labelWithString:@"Enter a stream key."];
    message.frame=NSMakeRect(20,138,400,24); [panel.contentView addSubview:message];
    field=[[StreamKeyField alloc] initWithFrame:NSMakeRect(20,99,400,26)];
    field.delegate=controller; field.placeholderString=@"Stream key";
    field.cell.sendsActionOnEndEditing=NO;
    [panel.contentView addSubview:field];
    remember=[NSButton checkboxWithTitle:@"Remember on this Mac" target:nil action:NULL];
    remember.state=NSControlStateValueOn;
    remember.frame=NSMakeRect(20,64,400,22); [panel.contentView addSubview:remember];
    NSButton *save=[NSButton buttonWithTitle:@"Save" target:controller action:@selector(save:)];
    save.frame=NSMakeRect(325,18,95,32); save.keyEquivalent=@"\r"; [panel.contentView addSubview:save];
    NSButton *cancel=[NSButton buttonWithTitle:@"Cancel" target:controller action:@selector(cancel:)];
    cancel.frame=NSMakeRect(220,18,95,32); cancel.keyEquivalent=@"\e"; [panel.contentView addSubview:cancel];
    [parent beginSheet:panel completionHandler:nil];
    [panel makeFirstResponder:field];
    NSTextView *editor=(NSTextView *)field.currentEditor;
    editor.allowsUndo=NO; editor.automaticTextCompletionEnabled=NO;
    editor.automaticSpellingCorrectionEnabled=NO; editor.continuousSpellCheckingEnabled=NO;
    editor.grammarCheckingEnabled=NO; editor.automaticTextReplacementEnabled=NO;
    editor.richText=NO; editor.importsGraphics=NO; editor.usesFindPanel=NO;
    [editor unregisterDraggedTypes]; editor.menu=nil;
}
int stream_key_editing(void) { return panel!=nil; }
int stream_key_route_event(NSEvent *event) {
    BOOL down=event.type==NSEventTypeKeyDown, up=event.type==NSEventTypeKeyUp;
    unsigned code=(down || up)?event.keyCode:0;
    if(up && code<sizeof consumed && consumed[code]) { consumed[code]=0; return 1; }
    if(!panel) {
        if(down && code<sizeof consumed && consumed[code]) {
            if(event.isARepeat) return 1;
            consumed[code]=0; // a fresh press retires a release lost during focus change
        }
        return 0;
    }
    if(down && code<sizeof consumed) consumed[code]=1;
    // No keyboard, mouse or modifier input escapes to guest/menu shortcuts while editing.
    if(down) {
        if(code==53) { stream_key_close(); return 1; }
        if(code==36 || code==76) { if(!event.isARepeat) [controller save:nil]; return 1; }
        if(event.modifierFlags & NSEventModifierFlagCommand) {
            NSTextView *editor=(NSTextView *)field.currentEditor;
            if(code==9 && editor) {
                // Read only the plain string type. Dispatch to the active field
                // editor directly; the application has no global Paste menu.
                @autoreleasepool {
                    NSString *text=[NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
                    if(text) [editor insertText:text replacementRange:editor.selectedRange];
                }
            } else if(code==0) [editor selectAll:nil];
            return 1; // copy, cut, undo and services shortcuts stay blocked
        }
    }
    // Block drag/copy and contextual/services export. Paste reads the user's clipboard.
    if(event.type==NSEventTypeLeftMouseDragged || event.type==NSEventTypeRightMouseDown ||
       event.type==NSEventTypeRightMouseUp) return 1;
    [NSApp sendEvent:event];
    return 1;
}
void stream_key_go(int unused) {
    (void)unused;
    if(busy() || panel) return;
    loadStoredKey();
    if(!keySize) return;
    if(!livestream_start_bytes(keyBytes,keySize)) { notice="Error: network"; return; }
    if(!cliprec_live_start()) { livestream_fail(LIVESTREAM_FAILURE_CAPTURE); notice="Error: capture"; return; }
    notice=NULL;
}
void stream_key_stop(int unused) {
    (void)unused;
    stream_key_close();
    cliprec_live_stop(); livestream_stop();
    notice="Stopped";
}
void stream_key_forget(int unused) {
    (void)unused;
    BOOL removed=removeStoredKey();
    stream_key_quit();
    if(!removed) notice="Couldn't remove the saved key.";
}
void stream_key_quit(void) {
    loaded=YES; // A quit can be deferred. Do not reload after clearing memory.
    stream_key_close();
    erase(keyBytes,sizeof keyBytes); keySize=0;
    stream_key_stop(0); notice=NULL;
}
void stream_key_status(char *buf,int size) {
    loadStoredKey();
    int state=cliprec_live_state();
    char reason[80]; livestream_error_text(reason,sizeof reason);
    const char *text=state==1?"Starting":state==2?"Live":state==3?"Stopping":
        state==4?(reason[0]?reason:"Error: capture"):notice?notice:keySize?"Ready (Off)":"No key";
    if(!state && notice && !strcmp(notice,"Stopped") && reason[0]) text=reason;
    if(state==1 || state==2) {
        int transport=livestream_status();
        if(transport!=1) text=reason[0]?reason:"Error: network";
    }
    snprintf(buf,size,"%s",text);
}
