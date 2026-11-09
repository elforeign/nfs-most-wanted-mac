#!/usr/bin/env python3
"""Secure controller/sinks with an in-memory sentinel, never argv or environment.
Original/fixed: paste 7/0 failures, status reasons 6/0 failures.
Measured good: 630 assertions/0 failures, 0 sentinel/output matches, 0 clipboard
and preference writes; fresh process: automatic Go=0, sessions=0. Nine faults:
stale release/OSK/plain field 1 failure each, AX 2, menu buffer 11, preferences/clipboard 1/3, cid 34;
stderr fault leaks 1 sentinel versus fixed 0 (captured and redacted in memory).
No windows are shown; sheet presentation and event delivery are test seams.
The production secure control, controller, validation, OSK note copies and report
sanitizer run unchanged. The real secure field editor receives plain paste from
an injected read-only pasteboard. VoiceOver needs UI proof.
"""
from pathlib import Path
import re
import hashlib
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import quote

# Pinned original input routing and collapsed status, exercised without an Edit menu.
ORIGINAL_KEY = {'route': "int stream_key_route_event(NSEvent *event) {\n    BOOL down=event.type==NSEventTypeKeyDown, up=event.type==NSEventTypeKeyUp;\n    unsigned code=(down || up)?event.keyCode:0;\n    if(up && code<sizeof consumed && consumed[code]) { consumed[code]=0; return 1; }\n    if(!panel) {\n        if(down && code<sizeof consumed && consumed[code]) {\n            if(event.isARepeat) return 1;\n            consumed[code]=0; // a fresh press retires a release lost during focus change\n        }\n        return 0;\n    }\n    if(down && code<sizeof consumed) consumed[code]=1;\n    // No keyboard, mouse or modifier input escapes to guest/menu shortcuts while editing.\n    if(down) {\n        if(code==53) { stream_key_close(); return 1; }\n        if(code==36 || code==76) { if(!event.isARepeat) [controller save:nil]; return 1; }\n        if((event.modifierFlags & NSEventModifierFlagCommand) && (code==8 || code==7)) return 1;\n    }\n    // Block drag/copy and contextual/services export. Paste reads the user's clipboard.\n    if(event.type==NSEventTypeLeftMouseDragged || event.type==NSEventTypeRightMouseDown ||\n       event.type==NSEventTypeRightMouseUp) return 1;\n    [NSApp sendEvent:event];\n    return 1;\n}", 'status': 'void stream_key_status(char *buf,int size) {\n    int state=cliprec_live_state();\n    const char *text=state==1?"Starting":state==2?"Live":state==3?"Stopping":\n        state==4?"Error: live stopped":notice?notice:keySize?"Ready (Off)":"No key";\n    if(state==1 || state==2) {\n        int transport=livestream_status();\n        if(transport!=1) text="Error: transport";\n    }\n    snprintf(buf,size,"%s",text);\n}'}
ORIGINAL_KEY_SHA256 = {'route': '20c18687c56a583442481d64d58c279a90ebca1abdae8414ca5953996e8f8bca', 'status': '865fafe3bd993c44f395b74048f25397bf438946de16fec78e77f6df15e52a22'}

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
    osk=core[core.index('static OskCapture game, note;'):core.index('static uint32_t invoke(')]
    osk+=core[core.index('static void layout_reset('):core.index('static void retire_game(')]
    for name,text in ORIGINAL_KEY.items():
        assert hashlib.sha256(text.encode()).hexdigest()==ORIGINAL_KEY_SHA256[name]
    route_begin=source.index('int stream_key_route_event(')
    route_end=source.index('\nvoid stream_key_go(',route_begin)
    status_begin=source.index('void stream_key_status(')
    faults={
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
        cid=next(line for line in privacy.splitlines() if line.endswith('// privacy:cid'))
        cases.append(('cid',source,privacy.replace(cid,'    (void)cid;')))
        for name,text,header in cases:
            assert name=='good' or text!=source or header!=privacy
            path=d/(name+'.m'); path.write_text(text)
            # Quoted include resolves the real header from the harness directory;
            # generate a harness copy so this independent header fault takes effect.
            (d/'report_privacy.h').write_text(header)
            harness=d/(name+'-test.m'); harness.write_text((RT/'stream_key_test.m').read_text())
            exe=d/name
            cmd=['clang','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',
                 '-Wno-deprecated-declarations','-fobjc-arc','-I'+str(RT),
                 '-DSTREAM_KEY_SOURCE="'+str(path)+'"','-DSTREAM_OSK_SOURCE="'+str(d/'osk-note.inc')+'"',
                 str(harness),'-framework','Cocoa','-framework','IOKit','-framework','SystemConfiguration','-o',str(exe)]
            built=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            (CACHE/(name+'-build.log')).write_text(built.stdout+built.stderr)
            assert built.returncode==0,built.stderr[-4000:]
            result=subprocess.run([str(exe),str(d/(name+'-bundle'))],input=sentinel+'\n',capture_output=True,text=True,timeout=30)
            out=result.stdout+result.stderr
            # Scan raw/URL/escaped forms and long partial copies before persisting logs.
            safe,matches=private_output(out,sentinel)
            (CACHE/(name+'.log')).write_text(safe)
            valid=result.returncode==0 and not matches
            print(f'{name}: rc={result.returncode} key_matches={matches}; '+safe.strip().replace('\n','; '),flush=True)
            assert valid==(name=='good'),name+' privacy oracle result'
            if name=='good':
                restarted=subprocess.run([str(exe),'restart'],input=sentinel+'\n',capture_output=True,text=True,timeout=10)
                output=restarted.stdout+restarted.stderr
                assert not restarted.returncode and sentinel not in output, 'restart must stay Off'
                print(output.strip(),flush=True)
    print('stream_key_test PASS: original paste/status and 9 independent faults rejected')
    return 0

if __name__=='__main__': raise SystemExit(main())
