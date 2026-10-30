// Real document/layout/shortcut/menu tests; only game, sound and host services are fixtures.
// Driven by guide_test.py, including deliberate source mutations. No game or GPU.
// Proof (checks/failures): good 1700/0. Broken production source controls:
// missing 1700/2; empty 1700/1; UTF-8 3140/1; format 2420/10; clamp 1700/10;
// resize/cache 1700/1 each; wrap 620/120; clip 1700/1; priority 1696/88;
// ordering 1700/4; tap 1700/9; release 1700/4; hidden 1700/2; Back 1698/32;
// F10 1700/5; pause 1696/6; opening 1700/4; scale 1700/6; old fonts 2420/6;
// old step 1700/6; modified release 1700/1; wheel 1700/2; page/repeat 1700/4.
// 25 controls rejected. Large panel 1960x1280; body 10, step 14; 28 lines
// versus 14 in each previous layout, for both Guide and What to test.
#include "guide.m"
#include "portmenu.m"
#include <stdio.h>
#include <stdlib.h>

uint8_t *G;
int diag_on;
const char *diag_dir="";
Device dev;
CAMetalLayer *layer;
static uint64_t ticks;
static int checks, failures, xbox, opening, pause_on, pause_off, feedback_hold;
static char saved_key[80], saved_value[80];
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } } while (0)
uint64_t host_ticks(void) { return ticks; }
void report_request_shot(void) {}
void report_note(const char *fmt,...) { (void)fmt; }
void diag_event(const char *fmt,...) { (void)fmt; }
void user32_menu_opening(void) { ++opening; }
void user32_menu_cursor(void) {}
void feedback_poll_pause(void) {}
void feedback_suppress(unsigned why,int on) { (void)why; feedback_hold=on; }
int user32_pad_layout_xbox(void) { return xbox; }
uint32_t gstr(const char *s) { (void)s; return 0x1234; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    (void)c; CHECK(fn==0x004B9080u && n==3 && args[1]==1);
    if (args[0]) ++pause_on; else ++pause_off;
    return 0;
}
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key,const char *value) { snprintf(saved_key,sizeof saved_key,"%s",key); snprintf(saved_value,sizeof saved_value,"%s",value); }
int roots_x360_has(const char *feature) { (void)feature; return 1; }
const char *port_build_id(void) { return "test"; }
void osk_note_begin(char *s,size_t n,int p) { (void)s;(void)n;(void)p; }
void osk_note_end(int cancel) { (void)cancel; }
int osk_last_pad(void) { return 0; }
void osk_pass(Cpu *c) { (void)c; }

static void write_text(NSString *path,NSString *text) {
    CHECK([text writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
}
static void layout_tests(NSString *resources) {
    load_document([resources stringByAppendingPathComponent:@"Absent.md"],0);
    CHECK([document.string containsString:@"could not be read"]);
    NSString *path=[resources stringByAppendingPathComponent:@"Fixture.md"];
    write_text(path,@" \n\t"); load_document(path,0); CHECK([document.string containsString:@"empty"]);
    unsigned char invalid[]={0xc3,0x28};
    CHECK([[NSData dataWithBytes:invalid length:2] writeToFile:path atomically:YES]);
    load_document(path,0); CHECK([document.string containsString:@"could not be read"]);
    write_text(path,@"# Heading\n## Smaller\n- A **bold** bullet\n[link](literal) and **unmatched\n");
    load_document(path,0);
    CHECK([document.string containsString:@"Heading"] && ![document.string containsString:@"# Heading"]);
    CHECK([document.string containsString:@"• A bold bullet"]);
    CHECK([document.string containsString:@"[link](literal) and **unmatched"]);
    CTFontRef font=(__bridge CTFontRef)[document attribute:(__bridge NSString *)kCTFontAttributeName atIndex:0 effectiveRange:NULL];
    CHECK(CTFontGetSize(font)==14);   // headings and text are half the panel's old size
    NSRange bold=[document.string rangeOfString:@"bold"];
    font=(__bridge CTFontRef)[document attribute:(__bridge NSString *)kCTFontAttributeName atIndex:bold.location effectiveRange:NULL];
    CHECK((CTFontGetSymbolicTraits(font)&kCTFontBoldTrait)!=0);
    CHECK(CTFontGetSize(font)==10);
    NSRange smaller=[document.string rangeOfString:@"Smaller"];
    font=(__bridge CTFontRef)[document attribute:(__bridge NSString *)kCTFontAttributeName atIndex:smaller.location effectiveRange:NULL];
    CHECK(CTFontGetSize(font)==12);
    NSMutableString *density=[NSMutableString string];
    for(int i=0;i<100;++i)[density appendString:@"A short line\n"];
    for(int testing=0;testing<2;++testing) {
        write_text(path,density);load_document(path,testing);layout(916,398);
        unsigned visible=0;
        for(NSNumber *y in baselines)visible+=y.doubleValue<=398;
        CHECK(visible==28);
        CHECK(baselines[1].doubleValue-baselines[0].doubleValue==14);
        CHECK(visible>398/28); // both previous layouts: 14 visible lines
        CHECK(PW==980 && PH==640 && guide_scale_cap()==2);
        CHECK(fit_scale(CGSizeMake(3840,2160))*PW==1960);
        printf("guide density: %s panel=1960x1280 font=10 step=14 visible=%u previous=14/14\n",testing?"Testing":"Guide",visible);
    }
    NSMutableString *longtext=[NSMutableString stringWithString:@"# Long guide\n"];
    for (int i=0;i<120;++i) [longtext appendFormat:@"Paragraph %d: café 日本語 🚗 %@\n",i,[@"unbrokené🚗" stringByPaddingToLength:220 withString:@"é" startingAtIndex:0]];
    [longtext appendString:@"FINAL-LINE"];
    write_text(path,longtext); load_document(path,0); layout(500,398);
    CHECK(lines.count>120 && content_height>398);
    CHECK(baselines.count==lines.count);
    id cached=lines; layout(500,398); CHECK(cached==lines);
    double last=-1;
    for (NSNumber *y in baselines) { CHECK(y.doubleValue>last); last=y.doubleValue; }
    for (id item in lines) {
        CTLineRef line=(__bridge CTLineRef)item;
        CHECK(CTLineGetTypographicBounds(line,NULL,NULL,NULL)<=500.01);
        CFRange range=CTLineGetStringRange(line);
        NSString *piece=[document.string substringWithRange:NSMakeRange(range.location,range.length)];
        CHECK([piece dataUsingEncoding:NSUTF8StringEncoding]!=nil);
    }
    for (int i=0;i<500;++i) guide_scroll(100);
    CHECK(offset==content_height-viewport_height);
    CFRange final=CTLineGetStringRange((__bridge CTLineRef)lines.lastObject);
    CHECK((NSUInteger)(final.location+final.length)==document.length);
    CHECK(baselines.lastObject.doubleValue-offset<=viewport_height);
    guide_command(GUIDE_HOME); CHECK(offset==0);
    guide_command(GUIDE_DOWN); CHECK(offset==14);
    guide_command(GUIDE_UP); CHECK(offset==0);
    guide_command(GUIDE_PAGE_DOWN); CHECK(offset==384);
    guide_command(GUIDE_PAGE_UP); CHECK(offset==0);
    guide_command(GUIDE_END); CHECK(offset==content_height-398);
    // Draw the actual clipped text and scrollbar into a bitmap; all guide pixels stay in their viewport.
    CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef ctx=CGBitmapContextCreate(NULL,980,640,8,980*4,space,(CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space); CHECK(ctx!=NULL);
    guide_draw(ctx,640,CGRectMake(32,116,916,398),0);
    guide_command(GUIDE_END); guide_draw(ctx,640,CGRectMake(32,116,916,398),1);
    unsigned char *pixels=CGBitmapContextGetData(ctx);
    unsigned ink=0, spill=0;
    // Bitmap rows run from the image top; the document occupies rows 116 through 513.
    for (int y=116;y<514;++y) for (int x=0;x<980;++x) {
        if (pixels[(y*980+x)*4+3]) { if (x>=32 && x<948) ++ink; else ++spill; }
    }
    for (int y=90;y<538;++y) if (y<116 || y>=514)
        for (int x=32;x<928;++x) spill+=pixels[(y*980+x)*4+3]!=0;
    if (spill || ink<=100) {
        fprintf(stderr,"bitmap ink=%u spill=%u row spans:",ink,spill);
        int in=0;
        for (int y=0;y<=640;++y) {
            int hit=0;
            if (y<640) for (int x=32;x<928;++x) if (pixels[(y*980+x)*4+3]) { hit=1; break; }
            if (hit && !in) fprintf(stderr," %d-",y);
            if (!hit && in) fprintf(stderr,"%d",y-1);
            in=hit;
        }
        fprintf(stderr,"\n");
    }
    CHECK(ink>100 && spill==0);
    CGContextRelease(ctx);
    layout(20000,1000000); CHECK(offset==0); // resize must re-clamp an old end position
    layout(1,398); CHECK(lines.count>120); // never stall on an oversized UTF-8 cluster
    for (int i=0;i<500;++i) guide_scroll(-100);
    CHECK(offset==0);
    guide_command(GUIDE_BACK); CHECK(!guide_active());
}
static GuideShortcutResult shortcut(GuideShortcut *s,double t,uint32_t p,int choice,int enabled,int open) {
    return guide_shortcut(s,t,p,0,0,1,choice,enabled,open);
}
static void shortcut_tests(void) {
    for (int choice=0;choice<4;++choice) {
        GuideShortcut s={0}; GuideShortcutResult r;
        r=shortcut(&s,1,CREATE,choice,1,0); CHECK(r.hide&CREATE); CHECK(!r.action);
        r=shortcut(&s,1.2,CREATE|R3,choice,1,0); CHECK((r.hide&(CREATE|R3))==(CREATE|R3)); CHECK(!r.action);
        r=shortcut(&s,1.54,CREATE|R3,choice,1,0); CHECK(!r.action);
        r=shortcut(&s,1.56,CREATE|R3,choice,1,0); CHECK(r.action==2 && !r.inject);
        r=shortcut(&s,2,CREATE|R3,choice,1,1); CHECK(!r.action);
        r=shortcut(&s,2.1,CREATE|R3,choice,1,0); CHECK(!r.action && (r.hide&(CREATE|R3))==(CREATE|R3));
        r=shortcut(&s,2.2,R3,choice,1,0); CHECK(r.hide&R3); CHECK(!r.inject);
        r=shortcut(&s,2.3,0,choice,1,0); CHECK(!r.hide && !r.inject);
        s=(GuideShortcut){0};
        shortcut(&s,1,CREATE,choice,1,0); r=shortcut(&s,1.1,0,choice,1,0);
        CHECK(r.inject==CREATE); r=shortcut(&s,1.15,0,choice,1,0); CHECK(r.inject==CREATE);
        r=shortcut(&s,1.3,0,choice,1,0); CHECK(!r.inject);
        s=(GuideShortcut){0};
        shortcut(&s,1,R3,choice,1,0); shortcut(&s,1.1,R3|CREATE,choice,1,0);
        r=shortcut(&s,1.8,R3|CREATE,choice,1,0); CHECK(r.action!=2);
        s=(GuideShortcut){0};
        shortcut(&s,1,R3|CREATE,choice,1,0); r=shortcut(&s,1.8,R3|CREATE,choice,1,0); CHECK(r.action!=2);
        s=(GuideShortcut){0};
        shortcut(&s,1,CREATE,choice,0,0); shortcut(&s,1.1,CREATE|R3,choice,0,0);
        r=shortcut(&s,1.8,CREATE|R3,choice,0,0); CHECK(r.action!=2);
    }
    GuideShortcut s={0}; GuideShortcutResult r;
    r=guide_shortcut(&s,1,0,0,1,1,0,1,0); CHECK(r.action==1);
    s=(GuideShortcut){0}; r=guide_shortcut(&s,1,0,1,0,1,1,1,0); CHECK(r.action==1);
    s=(GuideShortcut){0}; shortcut(&s,1,L3|R3,2,1,0); r=shortcut(&s,1.41,L3|R3,2,1,0); CHECK(r.action==1);
    s=(GuideShortcut){0}; shortcut(&s,1,CREATE,3,1,0); r=shortcut(&s,1.51,CREATE,3,1,0); CHECK(r.action==1);
    s=(GuideShortcut){0}; guide_shortcut(&s,1,CREATE,0,0,0,0,1,0);
    r=guide_shortcut(&s,1.51,CREATE,0,0,0,0,1,0); CHECK(r.action==1); // Xbox default fallback
    s=(GuideShortcut){0}; shortcut(&s,1,0,1,1,0); r=shortcut(&s,1.2,R3,1,1,0); CHECK(!r.hide && !r.action);
    s=(GuideShortcut){0}; shortcut(&s,1,CREATE,1,1,0); shortcut(&s,1.1,CREATE|R3,1,1,0);
    r=shortcut(&s,1.2,R3,1,1,0); CHECK((r.hide&R3)!=0 && r.inject==CREATE && !r.action);
    r=shortcut(&s,1.4,0,1,1,0); CHECK(!r.hide && !r.inject);
    s=(GuideShortcut){0}; r=shortcut(&s,1,CREATE,1,0,0); CHECK(!r.hide && !r.inject); // Off preserves the raw tap
}
static uint32_t pad(uint32_t buttons,int down) {
    uint32_t inject; ticks+=100000000;
    return portmenu_pad(buttons,0,down,0,0,0,0,0,0,1,&inject);
}
static void menu_tests(NSString *resources) {
    static const char *const choices[]={"Touch", "Home", "Sticks", "View"};
    static Setting menubtn={.id="controls.menu_button",.category=SET_CONTROLS,.type=SET_CHOICE,.choices=choices,.nchoices=4};
    settings_add(&menubtn); settings_init();
    CHECK(!strcmp(settings_category_names[SET_DIAGNOSTICS],"Help"));
    Setting *rs[128];
    guide_refresh(); int n=rows(SET_DIAGNOSTICS,rs,128);
    CHECK(n==1 && rs[0]==&guide_row);
    write_text([resources stringByAppendingPathComponent:@"Testing.md"],@"# Test checklist\nFirst\nLast\n");
    n=rows(SET_DIAGNOSTICS,rs,128); CHECK(n==2 && rs[0]==&guide_row && rs[1]==&testing_row);
    // The direct shortcut is a hidden switch, Off by default; the chord checks below turn it on.
    CHECK(shortcut_row.def==1 && shortcut_row.hidden);
    CHECK(settings_set(&shortcut_row,0)); CHECK(!strcmp(saved_key,"controls.guide_button")); CHECK(!strcmp(saved_value,"view_r3"));
    for (int flow=3;flow<=6;flow+=3) for (xbox=0;xbox<=1;++xbox) {
        M32(GAMEFLOW_STATE)=flow; M32(SND_CTL_STATES)=0;
        set_open(0); guide_close(); pad(0,0);
        pad(CREATE,0); pad(CREATE|R3,0); ticks+=400000000;
        int oldsel=sel;
        CHECK(pad(CREATE|R3,1)==~0u); CHECK(portmenu_is_open() && guide_active());
        CHECK(tab==SET_DIAGNOSTICS && sel==0); (void)oldsel;
        CHECK(![title isEqualToString:@"What to test"]); // chord always opens Guide
        CHECK(portmenu_hold()==(flow==6) && feedback_hold==1);
        Cpu c={0}; int before=pause_on; portmenu_pass(&c); CHECK(pause_on==before+(flow==6));
        layout(500,398); pad(CREATE|R3,1); CHECK(offset==14); pad(0,0);
        pad(0,1); CHECK(offset>0); pad(0,0);
        portmenu_key(0x24,1,0); CHECK(offset==0);
        portmenu_key(0x22,1,0); CHECK(offset==384);
        portmenu_key(0x21,1,0); CHECK(offset==0);
        pad(B_R1,0); CHECK(offset==384); pad(0,0);
        pad(B_L1,0); CHECK(offset==0); pad(0,0);
        // The same left-stick thresholds and repeat cadence serve both layouts.
        uint32_t injected;
        ticks+=100000000; portmenu_pad(0,0,0,0,0,0,-0.8f,0,0,1,&injected); CHECK(offset==14);
        ticks+=100000000; portmenu_pad(0,0,0,0,0,0,-0.8f,0,0,1,&injected); CHECK(offset==14);
        ticks+=310000000; portmenu_pad(0,0,0,0,0,0,-0.8f,0,0,1,&injected); CHECK(offset==28);
        pad(0,0);
        portmenu_key(0x23,1,0); CHECK(offset==content_height-viewport_height);
        portmenu_key(0x1B,1,0); CHECK(!guide_active() && portmenu_is_open());
        CHECK(portmenu_hold()==(flow==6));
        pad(2,0); CHECK(!portmenu_is_open()); CHECK((pad(2,0)&2)!=0); pad(0,0);
        portmenu_pass(&c); CHECK(!feedback_hold);
        portmenu_key(0x79,1,0); CHECK(portmenu_is_open());
        set_tab(SET_DIAGNOSTICS); sel=1; portmenu_key(0x0D,1,0); CHECK(guide_active() && [title isEqualToString:@"What to test"]);
        layout(500,28); portmenu_key(0x23,1,0); CHECK(offset>0);
        pad(0,0); pad(2,0); CHECK(portmenu_is_open() && !guide_active()); pad(0,0);
        sel=1; portmenu_key(0x0D,1,0); portmenu_key(0x79,1,0);
        CHECK(!portmenu_is_open() && !guide_active());
    }
    CHECK(opening>=8 && pause_on==pause_off);
    // An already paused game retains its own sound state.
    M32(GAMEFLOW_STATE)=6; M32(SND_CTL_STATES)=2; set_open(1);
    Cpu c={0}; int before=pause_on; portmenu_pass(&c); CHECK(pause_on==before);
    set_open(0); portmenu_pass(&c); CHECK(pause_on==before);
    CHECK(guide_scale_cap()==1); guide_open(0); CHECK(guide_scale_cap()==2);
    CHECK(fit_scale(CGSizeMake(3840,2160))==2);
    CHECK(fit_scale(CGSizeMake(1280,720))<=1.1);
    guide_close(); CHECK(fit_scale(CGSizeMake(3840,2160))==1);
    // Real mouse dispatch uses the guide's larger panel geometry, including its Back control.
    layer=(CAMetalLayer *)[CALayer layer]; layer.bounds=CGRectMake(0,0,1280,720);
    set_open(1); set_tab(SET_DIAGNOSTICS); guide_open(0); layout(916,398);
    CHECK(portmenu_mouse(4,640,360,-3)==1 && offset==42);
    CHECK(portmenu_mouse(4,640,360,1000)==1 && offset==0);
    double f=fit_scale(layer.bounds.size), ox,oy; panel_origin(layer.bounds.size,f,&ox,&oy);
    CHECK(portmenu_mouse(1,ox+900*f,oy+65*f,0)==1 && !guide_active() && portmenu_is_open());
    set_open(0); layer=nil;
    [NSFileManager.defaultManager removeItemAtPath:[resources stringByAppendingPathComponent:@"Testing.md"] error:NULL];
    n=rows(SET_DIAGNOSTICS,rs,128); CHECK(n==1 && rs[0]==&guide_row);
}
int main(int argc,char **argv) {
    (void)argc; (void)argv;
    @autoreleasepool {
        G=calloc(1,0x1000000);
        NSString *resources=NSBundle.mainBundle.resourcePath;
        CHECK(resource(0)!=nil);
        layout_tests(resources); shortcut_tests(); menu_tests(resources);
        printf("guide: %d checks, %d failures\n",checks,failures);
        free(G);
        return failures!=0;
    }
}
