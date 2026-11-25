#!/usr/bin/env python3
"""Secure controller/sinks with an in-memory sentinel, never argv or environment.
Original/fixed: paste 7/0 failures, status reasons 6/0 failures.
Before-wait Forget erasure: late-wipe control 3 failures, fixed 0 (666 checks).
Hardening original/fixed: editor 7/0 failures, unclosed Stop 2/0, erasing Stop 3/0, quit 5/0.
Remembered key on relaunch: original save 0 present/1 failure, fixed 1 present/0 failures.
Rebuilt process: 1 present/0 failures. Both fixed launches: 0 Go and 0 sessions.
Installed update: retained key 1 present/0 failures; lost-key control 0 present/1 failure.
The production apply/ack path replaces a scratch app; only signature admission is stubbed.
Contention: bypassing the directory lock replaces the saved key (1 failure); the fixed write preserves it.
Measured good: 666 assertions/0 failures, 0 sentinel/output matches, 0 clipboard and preference writes.
Legacy faults: stale release/OSK/plain field 1 failure each, AX 2, menu buffer 11, preferences/clipboard 1/3, cid 34;
stderr fault leaks 1 sentinel versus fixed 0 (captured and redacted in memory).
No windows are shown; sheet presentation and event delivery are test seams.
The production secure control, controller, validation, OSK note copies and report
sanitizer run unchanged. The real secure field editor receives plain paste from
an injected read-only pasteboard. VoiceOver needs UI proof.
"""
from pathlib import Path
import fcntl
import os
import re
import runpy
import shutil
import hashlib
import secrets
import stat
import subprocess
import sys
import tempfile
from urllib.parse import quote
from unittest import mock

# Pinned original input routing and collapsed status, exercised without an Edit menu.
ORIGINAL_KEY = {'route': "int stream_key_route_event(NSEvent *event) {\n    BOOL down=event.type==NSEventTypeKeyDown, up=event.type==NSEventTypeKeyUp;\n    unsigned code=(down || up)?event.keyCode:0;\n    if(up && code<sizeof consumed && consumed[code]) { consumed[code]=0; return 1; }\n    if(!panel) {\n        if(down && code<sizeof consumed && consumed[code]) {\n            if(event.isARepeat) return 1;\n            consumed[code]=0; // a fresh press retires a release lost during focus change\n        }\n        return 0;\n    }\n    if(down && code<sizeof consumed) consumed[code]=1;\n    // No keyboard, mouse or modifier input escapes to guest/menu shortcuts while editing.\n    if(down) {\n        if(code==53) { stream_key_close(); return 1; }\n        if(code==36 || code==76) { if(!event.isARepeat) [controller save:nil]; return 1; }\n        if((event.modifierFlags & NSEventModifierFlagCommand) && (code==8 || code==7)) return 1;\n    }\n    // Block drag/copy and contextual/services export. Paste reads the user's clipboard.\n    if(event.type==NSEventTypeLeftMouseDragged || event.type==NSEventTypeRightMouseDown ||\n       event.type==NSEventTypeRightMouseUp) return 1;\n    [NSApp sendEvent:event];\n    return 1;\n}", 'status': 'void stream_key_status(char *buf,int size) {\n    int state=cliprec_live_state();\n    const char *text=state==1?"Starting":state==2?"Live":state==3?"Stopping":\n        state==4?"Error: live stopped":notice?notice:keySize?"Ready (Off)":"No key";\n    if(state==1 || state==2) {\n        int transport=livestream_status();\n        if(transport!=1) text="Error: transport";\n    }\n    snprintf(buf,size,"%s",text);\n}'}
ORIGINAL_KEY_SHA256 = {'route': '20c18687c56a583442481d64d58c279a90ebca1abdae8414ca5953996e8f8bca', 'status': '865fafe3bd993c44f395b74048f25397bf438946de16fec78e77f6df15e52a22'}

# Reviewed pre-hardening cleanup fragments; replayed verbatim, without history.
ORIGINAL_CLOSE = 'void stream_key_close(void) {\n    if(!panel) return;\n    NSTextView *editor=(NSTextView *)field.currentEditor;\n    editor.string=@"";\n    [editor.undoManager removeAllActions];\n    field.stringValue=@"";\n    [panel makeFirstResponder:nil];\n    NSWindow *parent=panel.sheetParent;\n    if(parent) [parent endSheet:panel];\n    [panel orderOut:nil];\n    panel.delegate=nil; field.delegate=nil;\n    panel=nil; field=nil; message=nil; controller=nil;\n}'
ORIGINAL_CLOSE_SHA256 = 'a0e74f444fd5e3dcb1f4b7b97b5d74456137db4fb7ba64be7b81254dcca45932'
ORIGINAL_STOP = 'void stream_key_stop(int unused) {\n    (void)unused;\n    livestream_stop(); cliprec_live_stop(); notice="Stopped";\n}'
ORIGINAL_STOP_SHA256 = '2941c26b9243baf54459293ba7b205726b1c3d941b99ef3ded03180a11be3878'
ORIGINAL_QUIT = 'static void quit_now(void) { diag_begin_exit(); fflush(NULL); exit(0); }'
ORIGINAL_SHOULD_TERMINATE = '- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {\n    (void)app;\n    if (!quitting++ && user32_request_close()) return NSTerminateCancel;\n    quit_now();\n    return NSTerminateNow;\n}'

ORIGINAL_QUIT_SHA256 = '0b53fff009d36def307c8af6ecfbd2b6e09f4b20b96fc60a69ce7b333bae782f'
ORIGINAL_SHOULD_TERMINATE_SHA256 = 'bb54ca9c220dd864415fff91e50fcb7607c785367ad9d1d1d6fbc2c9c6b56f91'

ORIGINAL_SAVE = '''    if(valid && used==length) {
        erase(keyBytes,sizeof keyBytes); memcpy(keyBytes,temporary,used); keySize=used; notice=NULL;
    } else notice="Error: invalid key";
    erase(temporary,sizeof temporary);
    return valid && used==length;'''
ORIGINAL_SAVE_SHA256 = 'f5ef8d49c2b58d7f2ba9204f2fac0eaffcac4653d7cd382e23d194b959c3d6a5'


def function(text, signature):
    begin=text.index(signature)
    brace=text.index('{',begin)
    depth=1; end=brace+1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}'); end+=1
    return text[begin:end]


def quit_source(app):
    return (function(app,'static void quit_now(')+'\n'
            '@interface KeyQuitDelegate : NSObject { int quitting; }\n@end\n'
            '@implementation KeyQuitDelegate\n'+
            function(app,'- (NSApplicationTerminateReply)applicationShouldTerminate:')+'\n'+
            function(app,'- (void)applicationWillTerminate:')+'\n@end\n')

RT=Path(__file__).resolve().parent
ROOT=RT.parent
CACHE=ROOT/'.metal-cache/stream-key'


def private_output(output,key):
    percent=''.join('%%%02X'%b for b in key.encode())
    escaped=''.join('\\u%04x'%ord(c) for c in key[:16])
    forms={key,key[:32],quote(key,safe=''),percent,percent.lower(),percent[:48],percent[:48].lower(),escaped,escaped.replace(chr(92),chr(92)*2)}
    matches=max(output.count(form) for form in forms)
    safe=output
    for form in sorted(forms,key=len,reverse=True):
        safe=re.sub(re.escape(form)+r'[^\s]*','[redacted]',safe)
    return safe,matches


def installed_update_proof(exe, rebuilt, base, sentinel):
    helpers=runpy.run_path(str(ROOT/'tests/setup/test_apply.py'))
    base.mkdir()
    fixture=helpers['Fixture'](base)
    install,apply=helpers['ins'],helpers['a']
    executable=fixture.product['exec']
    installed=fixture.dest/'Contents/MacOS'/executable
    staged=fixture.job/fixture.bundle/'Contents/MacOS'/executable
    shutil.copy2(exe,installed)
    shutil.copy2(rebuilt,staged)
    before=hashlib.sha256(installed.read_bytes()).digest()
    after=hashlib.sha256(staged.read_bytes()).digest()
    assert before!=after, 'update must replace executable bytes'
    fixture.descriptor['hashes']['app']=install.digest_tree(fixture.job/fixture.bundle)
    fixture.descriptor['destination'].update(identity=install.ident(fixture.dest),sha256=install.digest_tree(fixture.dest))
    fixture.write_ready()

    def launch(mode, failed=False):
        result=subprocess.run([str(installed),str(fixture.dest),str(fixture.profile),mode],
                              input=sentinel+'\n',capture_output=True,text=True,timeout=15)
        safe,matches=private_output(result.stdout+result.stderr,sentinel)
        assert not matches and result.returncode==int(failed), 'installed '+mode
        assert 'go=0 sessions=0' in safe
        return safe

    launch('remember')
    key=fixture.profile/'.stream-key/stream-key'
    record=key.read_bytes()
    with mock.patch.object(install,'codesign_ok',return_value=True):
        token=fixture.apply()
        fixture.receipt(token)
        apply.acknowledge(fixture.profile,token)
    assert hashlib.sha256(installed.read_bytes()).digest()==after
    assert key.read_bytes()==record
    assert 'key_present=1' in launch('relaunch')
    print('installed update: executable replaced, key_present=1 go=0 sessions=0 failures=0',flush=True)
    key.unlink()
    assert 'key_present=0' in launch('relaunch',failed=True)
    print('lost-key control: key_present=0 go=0 sessions=0 failures=1',flush=True)


def storage_proof(exe, command, profile, sentinel, variant):
    profile.mkdir()
    bundle=profile.parent/'unused-bundle'

    def run(binary, mode, failed=False):
        result=subprocess.run([str(binary),str(bundle),str(profile),mode],input=sentinel+'\n',
                              capture_output=True,text=True,timeout=15)
        safe,matches=private_output(result.stdout+result.stderr,sentinel)
        print(f'{binary.name}: {safe.strip()}',flush=True)
        assert result.returncode==int(failed) and not matches, 'storage '+mode
        return safe

    run(exe,'remember')
    if variant=='original-memory-only':
        assert 'key_present=0 go=0 sessions=0' in run(exe,'relaunch',failed=True)
        return

    store=profile/'.stream-key'
    key=store/'stream-key'
    record=key.read_bytes()
    unlocked=variant=='unlocked-store'
    fd=os.open(store,os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        run(exe,'contended-save',failed=unlocked)
        assert (key.read_bytes()!=record)==unlocked, 'contended save changed the key'
    finally:
        os.close(fd)
    if unlocked:
        return
    assert stat.S_IMODE(store.stat().st_mode)==0o700 and stat.S_IMODE(key.stat().st_mode)==0o600
    assert not (store/'stream-key.tmp').exists()
    assert 'key_present=1 go=0 sessions=0' in run(exe,'relaunch')
    rebuilt=exe.with_name('rebuilt')
    built=subprocess.run([*command[:-1],str(rebuilt),'-O0'],capture_output=True,text=True,timeout=90)
    assert built.returncode==0,built.stderr[-4000:]
    assert 'key_present=1 go=0 sessions=0' in run(rebuilt,'relaunch')
    if variant=='good':
        installed_update_proof(exe,rebuilt,profile.parent/'installed-update',sentinel)

    key.chmod(0o644)
    assert 'key_present=0 go=0 sessions=0 status=Saved key unavailable' in run(rebuilt,'unavailable')
    key.chmod(0o000)
    run(rebuilt,'unavailable')
    key.chmod(0o600)
    subprocess.run(['/bin/chmod','+a','everyone allow read',str(key)],check=True,capture_output=True)
    run(rebuilt,'unavailable')
    subprocess.run(['/bin/chmod','-N',str(key)],check=True,capture_output=True)
    key.write_bytes(record[:-1]+bytes([record[-1] ^ 1]))
    run(rebuilt,'unavailable')
    key.write_bytes(record)
    run(rebuilt,'forget')
    assert not key.exists()
    run(rebuilt,'missing')
    run(exe,'remember')
    run(exe,'session-only')
    assert not key.exists()
    run(rebuilt,'missing')


def main():
    if sys.platform!='darwin':
        print('SKIP: requires Cocoa'); return 3
    CACHE.mkdir(parents=True,exist_ok=True)
    source=(RT/'stream_key.m').read_text()
    privacy=(RT/'report_privacy.h').read_text()
    # Independent source boundaries ensure no generic setting or storage path is added.
    assert not re.search(r'\b(?:getenv|setenv|NSUserDefaults|CFPreferences\w*|app_pref_set|report_note|diag_event|osk_note_begin|SecItem\w*)\b',source)
    menu=(RT/'settings_port.c').read_text()
    rows=re.findall(r'static Setting s_stream_\w+ = \{.*?\};',menu,re.S)
    assert len(rows)==6
    assert all('.pref=""' in row and '.env' not in row and 'SET_TEXT' not in row for row in rows)
    assert all('REG(s_stream_'+name+');' in menu for name in ('heading','key','forget','go','stop','status'))
    assert 'stream_key.m \\' in (RT/'build.sh').read_text()
    user=(RT/'user32.m').read_text()
    assert user.index('if(stream_key_route_event(e)) {')<user.index('            switch (e.type)')
    core=(RT/'onscreen_keyboard.c').read_text()
    osk=core[core.index('static OskCapture game, note;'):core.index('#if NFS_GAME == NFS_GAME_MW\nstatic uint32_t invoke(')]
    osk+=core[core.index('static void layout_reset('):core.index('static void retire_game(')]
    for name,text in ORIGINAL_KEY.items():
        assert hashlib.sha256(text.encode()).hexdigest()==ORIGINAL_KEY_SHA256[name]
    route_begin=source.index('int stream_key_route_event(')
    route_end=source.index('\nvoid stream_key_go(',route_begin)
    status_begin=source.index('void stream_key_status(')
    for text,digest in ((ORIGINAL_CLOSE,ORIGINAL_CLOSE_SHA256),(ORIGINAL_STOP,ORIGINAL_STOP_SHA256),
                        (ORIGINAL_QUIT,ORIGINAL_QUIT_SHA256),(ORIGINAL_SHOULD_TERMINATE,ORIGINAL_SHOULD_TERMINATE_SHA256)):
        assert hashlib.sha256(text.encode()).hexdigest()==digest
    app=(RT/'app.m').read_text()
    quit_good=quit_source(app)
    quit_original=(ORIGINAL_QUIT+'\n@interface KeyQuitDelegate : NSObject { int quitting; }\n@end\n'
                   '@implementation KeyQuitDelegate\n'+ORIGINAL_SHOULD_TERMINATE+
                   '\n- (void)applicationWillTerminate:(NSNotification *)n { (void)n; }\n@end\n')
    faults={
        'original-close':source.replace(function(source,'void stream_key_close('),ORIGINAL_CLOSE),
        'original-stop':source.replace(function(source,'void stream_key_stop('),ORIGINAL_STOP),
        'erase-stop':source.replace('    notice="Stopped";', '    erase(keyBytes,sizeof keyBytes); keySize=0; notice="Stopped";'),
        'original-quit':source,
        'late-forget-wipe':source.replace('    erase(keyBytes,sizeof keyBytes); keySize=0;\n    stream_key_stop(0); notice=NULL;', '    stream_key_stop(0);\n    erase(keyBytes,sizeof keyBytes); keySize=0; notice=NULL;'),
        'original-paste':source[:route_begin]+ORIGINAL_KEY['route']+source[route_end:],
        'original-status':source[:status_begin]+ORIGINAL_KEY['status']+'\n',
        'osk-buffer':source.replace('keySize=used; notice=NULL;', 'keySize=used; notice=NULL; static char leak[512]; size_t count=MIN(used,511u); memcpy(leak,keyBytes,count); leak[count]=0; osk_note_begin(leak,sizeof leak,0);'),
        'stale-release':source.replace('consumed[code]=0; // a fresh press','(void)code; // a fresh press'),
        'plain-field':source.replace('StreamKeyField : NSSecureTextField','StreamKeyField : NSTextField').replace('static NSSecureTextField *field;','static NSTextField *field;').replace('StreamKeyCell : NSSecureTextFieldCell','StreamKeyCell : NSTextFieldCell'),
        'ax':source.replace('- (id)accessibilityValue { return @""; }','- (id)accessibilityValue { return self.stringValue; }'),
        'menu-buffer':source.replace('snprintf(buf,size,"%s",text);','(void)text; snprintf(buf,size,"%.*s",(int)keySize,keyBytes);'),
        'stderr':source.replace('keySize=used; notice=NULL;','keySize=used; notice=NULL; fprintf(stderr,"%s\\n",value.UTF8String);'),
        'preference':source.replace('keySize=used; notice=NULL;','keySize=used; notice=NULL; [NSUserDefaults.standardUserDefaults setObject:value forKey:@"stream_key"];'),
        'clipboard':source.replace('keySize=used; notice=NULL;','keySize=used; notice=NULL; [NSPasteboard.generalPasteboard setString:value forType:NSPasteboardTypeString];'),
    }
    sentinel='sk_'+secrets.token_hex(60)+'+&=/%?#é'
    with tempfile.TemporaryDirectory(prefix='proof-',dir=CACHE) as temp:
        d=Path(temp); (d/'osk-note.inc').write_text(osk)
        cases=[('good',source,privacy),*[(name,text,privacy) for name,text in faults.items()]]
        assert hashlib.sha256(ORIGINAL_SAVE.encode()).hexdigest()==ORIGINAL_SAVE_SHA256
        replace=function(source,'static BOOL replaceKey(')
        old_replace=replace[:replace.index('    BOOL saved=NO;')]+'    (void)rememberOnMac;\n'+ORIGINAL_SAVE+'\n}'
        cases.append(('original-memory-only',source.replace(replace,old_replace),privacy))
        cases.append(('unlocked-store',source.replace('int dir=lockStore(YES);','int dir=storeDirectory(YES);'),privacy))
        cid=next(line for line in privacy.splitlines() if line.endswith('// privacy:cid'))
        cases.append(('cid',source,privacy.replace(cid,'    (void)cid;')))
        for name,text,header in cases:
            assert name in ('good','original-quit') or text!=source or header!=privacy
            path=d/(name+'.m'); path.write_text(text)
            # Quoted include resolves the real header from the harness directory;
            # generate a harness copy so this independent header fault takes effect.
            (d/'report_privacy.h').write_text(header)
            harness=d/(name+'-test.m'); harness.write_text((RT/'stream_key_test.m').read_text())
            (d/'quit.inc').write_text(quit_original if name=='original-quit' else quit_good)
            exe=d/name
            cmd=['clang','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',
                 '-Wno-deprecated-declarations','-fobjc-arc','-I'+str(RT),
                 '-DSTREAM_QUIT_SOURCE=\"'+str(d/'quit.inc')+'\"','-DSTREAM_KEY_SOURCE="'+str(path)+'"','-DSTREAM_OSK_SOURCE="'+str(d/'osk-note.inc')+'"',
                 str(harness),'-framework','Cocoa','-framework','IOKit','-framework','SystemConfiguration','-o',str(exe)]
            built=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            (CACHE/(name+'-build.log')).write_text(built.stdout+built.stderr)
            assert built.returncode==0,built.stderr[-4000:]
            if name in ('good','original-memory-only','unlocked-store'):
                storage_proof(exe,cmd,d/(name+'-store'),sentinel,name)
                if name!='good':
                    continue
            profile=d/(name+'-profile')
            profile.mkdir()
            result=subprocess.run([str(exe),str(d/(name+'-bundle')),str(profile)],input=sentinel+'\n',capture_output=True,text=True,timeout=30)
            out=result.stdout+result.stderr
            # Scan raw/URL/escaped forms and long partial copies before persisting logs.
            safe,matches=private_output(out,sentinel)
            (CACHE/(name+'.log')).write_text(safe)
            valid=result.returncode==0 and not matches
            print(f'{name}: rc={result.returncode} key_matches={matches}; '+safe.strip().replace('\n','; '),flush=True)
            assert valid==(name=='good'),name+' privacy oracle result'
    print('stream_key_test PASS: persistence, privacy and original failure controls')
    return 0

if __name__=='__main__': raise SystemExit(main())
