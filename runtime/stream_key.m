// Secure entry stays outside settings, guest memory and diagnostic serialization.
#import <Cocoa/Cocoa.h>
#include "stream_key.h"
#include "livestream.h"
#include "cliprec.h"
#include <string.h>

static unsigned char keyBytes[512];
static size_t keySize;
static const char *notice;
static NSPanel *panel;
static NSSecureTextField *field;
static NSTextField *message;
static unsigned char consumed[256];

static void erase(void *bytes, size_t count) {
    volatile unsigned char *p=bytes;
    while(count--) *p++=0;
}
static int busy(void) { return cliprec_live_state()==1 || cliprec_live_state()==2 || cliprec_live_state()==3; }

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
    NSTextView *editor=(NSTextView *)field.currentEditor;
    editor.string=@"";
    [editor.undoManager removeAllActions];
    field.stringValue=@"";
    [panel makeFirstResponder:nil];
    NSWindow *parent=panel.sheetParent;
    if(parent) [parent endSheet:panel];
    [panel orderOut:nil];
    panel.delegate=nil; field.delegate=nil;
    panel=nil; field=nil; message=nil; controller=nil;
}
static BOOL replaceKey(NSString *value) {
    // Validate the entire UTF-8 value; never truncate or normalize accepted bytes.
    if(busy()) { notice="Stop live first"; return NO; }
    NSUInteger length=[value lengthOfBytesUsingEncoding:NSUTF8StringEncoding];
    if(!length || length>sizeof keyBytes ||
       [value rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location!=NSNotFound) {
        notice="Error: invalid key"; return NO;
    }
    unsigned char temporary[512]={0};
    NSUInteger used=0;
    BOOL valid=[value getBytes:temporary maxLength:sizeof temporary usedLength:&used
        encoding:NSUTF8StringEncoding options:0 range:NSMakeRange(0,value.length) remainingRange:NULL];
    if(valid && used==length) {
        erase(keyBytes,sizeof keyBytes); memcpy(keyBytes,temporary,used); keySize=used; notice=NULL;
    } else notice="Error: invalid key";
    erase(temporary,sizeof temporary);
    return valid && used==length;
}
@implementation StreamKeyController
- (void)save:(id)sender {
    (void)sender;
    if(replaceKey(field.stringValue)) stream_key_close();
    else message.stringValue=@"Enter 1–512 UTF-8 bytes without control characters.";
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

static void quitKey(void) { stream_key_forget(0); }
void stream_key_edit(int unused) {
    (void)unused;
    if(!NSThread.isMainThread) return;
    if(busy()) { notice="Stop live first"; return; }
    if(panel) return;
    NSWindow *parent=NSApp.keyWindow;
    if(!parent) { notice="Error: editor unavailable"; return; }
    static dispatch_once_t once;
    dispatch_once(&once,^{ atexit(quitKey); });
    panel=[[NSPanel alloc] initWithContentRect:NSMakeRect(0,0,440,150)
        styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable backing:NSBackingStoreBuffered defer:NO];
    panel.title=@"Stream key"; panel.restorable=NO; panel.releasedWhenClosed=NO;
    controller=[StreamKeyController new]; panel.delegate=controller;
    message=[NSTextField labelWithString:@"Enter a stream key. Save keeps it until you quit."];
    message.frame=NSMakeRect(20,108,400,24); [panel.contentView addSubview:message];
    field=[[StreamKeyField alloc] initWithFrame:NSMakeRect(20,69,400,26)];
    field.delegate=controller; field.placeholderString=@"Stream key";
    field.cell.sendsActionOnEndEditing=NO;
    [panel.contentView addSubview:field];
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
    notice=NULL;
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
                NSString *text=[NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
                if(text) [editor insertText:text replacementRange:editor.selectedRange];
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
    if(!keySize) { notice="No key"; return; }
    if(!livestream_start_bytes(keyBytes,keySize)) { notice="Error: network"; return; }
    if(!cliprec_live_start()) { livestream_fail(LIVESTREAM_FAILURE_CAPTURE); notice="Error: capture"; return; }
    notice=NULL;
}
void stream_key_stop(int unused) {
    (void)unused;
    livestream_stop(); cliprec_live_stop(); notice="Stopped";
}
void stream_key_forget(int unused) {
    (void)unused;
    stream_key_close(); stream_key_stop(0);
    erase(keyBytes,sizeof keyBytes); keySize=0; notice=NULL;
}
void stream_key_status(char *buf,int size) {
    int state=cliprec_live_state();
    char reason[80]; livestream_error_text(reason,sizeof reason);
    const char *text=state==1?"Starting":state==2?"Live":state==3?"Stopping":
        state==4?(reason[0]?reason:"Error: capture"):notice?notice:keySize?"Ready (Off)":"No key";
    if(state==1 || state==2) {
        int transport=livestream_status();
        if(transport!=1) text=reason[0]?reason:"Error: network";
    }
    snprintf(buf,size,"%s",text);
}
