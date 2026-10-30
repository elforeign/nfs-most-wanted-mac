// Bundled UTF-8 documents, CoreText layout and controller shortcut arbitration.
#import <Cocoa/Cocoa.h>
#import <CoreText/CoreText.h>
#include "guide.h"
#include "settings.h"
#include <math.h>

static int active;
static NSString *title;
static NSAttributedString *document;
static NSMutableArray *lines;
static NSMutableArray<NSNumber *> *baselines;
static double layout_width, content_height, viewport_height, offset;

static NSString *resource(int testing) {
    return [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:testing ? @"Testing.md" : @"Guide.md"];
}
static void open_guide(int unused) { (void)unused; guide_open(0); }
static void open_testing(int unused) { (void)unused; guide_open(1); }
static const char *const shortcut_labels[] = {"Create / View + R3 (hold)", "Off"};
static const char *const shortcut_values[] = {"view_r3", "off"};
static Setting guide_row = {.id="help.guide", .category=SET_DIAGNOSTICS, .order=-20, .label="Guide",
    .help="Read the guide without leaving the game.", .type=SET_ACTION, .apply=open_guide};
static Setting testing_row = {.id="help.testing", .category=SET_DIAGNOSTICS, .order=-10, .label="What to test",
    .help="Read the checklist included with this build.", .type=SET_ACTION, .apply=open_testing, .hidden=1};
static Setting shortcut_row = {.id="controls.guide_button", .category=SET_CONTROLS, .order=11,
    .label="Controller: open Guide", .help="Hold Create / View, then press R3 and hold both for 0.35 s. Short Create / View taps reach the game on release.",
    .type=SET_CHOICE, .choices=shortcut_labels, .values=shortcut_values, .nchoices=2, .def=1, .hidden=1, .env="NFS_GUIDE_BUTTON"};
__attribute__((constructor)) static void register_guide(void) {
    settings_add(&guide_row); settings_add(&testing_row); settings_add(&shortcut_row);
}
void guide_refresh(void) { testing_row.hidden = ![NSFileManager.defaultManager fileExistsAtPath:resource(1) ?: @""]; }
int guide_active(void) { return active; }
double guide_scale_cap(void) { return active ? 2 : 1; }   // a large panel; the text inside is half size
void guide_close(void) { active=0; }

static NSDictionary *attributes(double size, int bold) {
    NSFont *font = bold ? [NSFont boldSystemFontOfSize:size] : [NSFont systemFontOfSize:size];
    return @{(__bridge NSString *)kCTFontAttributeName:font,
             (__bridge NSString *)kCTForegroundColorAttributeName:(__bridge id)CGColorGetConstantColor(kCGColorWhite)};
}
static NSAttributedString *format(NSString *source) {
    NSMutableAttributedString *result = [[NSMutableAttributedString alloc] init];
    for (NSString *raw in [source componentsSeparatedByString:@"\n"]) {
        int heading = [raw hasPrefix:@"## "] ? 2 : [raw hasPrefix:@"# "] ? 1 : 0;
        NSString *text = heading ? [raw substringFromIndex:heading+1] : raw;
        if ([text hasPrefix:@"- "] || [text hasPrefix:@"* "]) text=[@"• " stringByAppendingString:[text substringFromIndex:2]];
        NSMutableAttributedString *part = [[NSMutableAttributedString alloc] initWithString:@"" attributes:nil];
        NSDictionary *normal=attributes(heading==1 ? 14 : heading==2 ? 12 : 10, heading!=0);
        // Matched pairs only: unsupported or unmatched markup stays literal.
        NSUInteger start=0;
        while (start < text.length) {
            NSRange a=[text rangeOfString:@"**" options:0 range:NSMakeRange(start,text.length-start)];
            NSRange b=a.location==NSNotFound ? NSMakeRange(NSNotFound,0) :
                [text rangeOfString:@"**" options:0 range:NSMakeRange(NSMaxRange(a),text.length-NSMaxRange(a))];
            if (b.location==NSNotFound) {
                [part appendAttributedString:[[NSAttributedString alloc] initWithString:[text substringFromIndex:start] attributes:normal]];
                break;
            }
            [part appendAttributedString:[[NSAttributedString alloc] initWithString:[text substringWithRange:NSMakeRange(start,a.location-start)] attributes:normal]];
            [part appendAttributedString:[[NSAttributedString alloc] initWithString:[text substringWithRange:NSMakeRange(NSMaxRange(a),b.location-NSMaxRange(a))] attributes:attributes(heading ? 12 : 10,1)]];
            start=NSMaxRange(b);
        }
        [part appendAttributedString:[[NSAttributedString alloc] initWithString:@"\n" attributes:normal]];
        [result appendAttributedString:part];
    }
    return result;
}
static void load_document(NSString *path, int testing) {
    title=testing ? @"What to test" : @"Guide";
    NSData *data=path ? [NSData dataWithContentsOfFile:path] : nil;
    NSString *source=data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
    if (!source) source=@"This document could not be read. Press Back to return to Help.";
    else if (![source stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length)
        source=@"This document is empty. Press Back to return to Help.";
    document=format(source);
    lines=nil; baselines=nil; layout_width=0; offset=0; content_height=0;
    active=1;
}
void guide_open(int testing) { load_document(resource(testing),testing); }
static void clamp_scroll(void) { offset=fmax(0,fmin(offset,fmax(0,content_height-viewport_height))); }
static void layout(double width, double height) {
    viewport_height=height;
    if (!lines || layout_width!=width) {
        lines=[NSMutableArray array]; baselines=[NSMutableArray array];
        CTTypesetterRef typesetter=CTTypesetterCreateWithAttributedString((__bridge CFAttributedStringRef)document);
        CFIndex pos=0, length=document.length;
        double y=0;
        while (pos<length) {
            CFIndex count=CTTypesetterSuggestLineBreak(typesetter,pos,fmax(1,width));
            if (count<=0) count=CTTypesetterSuggestClusterBreak(typesetter,pos,fmax(1,width));
            if (count<=0) { // Always advance by a complete composed character, even at a one-point width.
                count=[document.string rangeOfComposedCharacterSequenceAtIndex:(NSUInteger)pos].length;
            }
            CTLineRef line=CTTypesetterCreateLine(typesetter,CFRangeMake(pos,count));
            double ascent,descent,leading;
            CTLineGetTypographicBounds(line,&ascent,&descent,&leading);
            double step=fmax(14,ceil(ascent+descent+2));
            [baselines addObject:@(y+ascent)]; [lines addObject:CFBridgingRelease(line)];
            y+=step; pos+=count;
        }
        CFRelease(typesetter);
        content_height=y; layout_width=width;
    }
    clamp_scroll();
}
void guide_scroll(double count) { offset+=count*14; clamp_scroll(); }
void guide_command(int command) {
    switch (command) {
    case GUIDE_UP: guide_scroll(-1); break;
    case GUIDE_DOWN: guide_scroll(1); break;
    case GUIDE_PAGE_UP: offset-=fmax(14,viewport_height-14); break;
    case GUIDE_PAGE_DOWN: offset+=fmax(14,viewport_height-14); break;
    case GUIDE_HOME: offset=0; break;
    case GUIDE_END: offset=content_height; break;
    case GUIDE_BACK: guide_close(); break;
    }
    clamp_scroll();
}
static void label(CGContextRef ctx, NSString *text, double x, double y, double size) {
    NSAttributedString *s=[[NSAttributedString alloc] initWithString:text attributes:attributes(size,0)];
    CTLineRef line=CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)s);
    CGContextSetTextPosition(ctx,x,y); CTLineDraw(line,ctx); CFRelease(line);
}
void guide_draw(CGContextRef ctx, double panel_height, CGRect viewport, int xbox) {
    layout(viewport.size.width-20,viewport.size.height);
    label(ctx,title,viewport.origin.x,panel_height-80,14);
    CGRect clip=CGRectMake(viewport.origin.x,panel_height-CGRectGetMaxY(viewport),viewport.size.width-16,viewport.size.height);
    CGContextSaveGState(ctx); CGContextClipToRect(ctx,clip);
    for (NSUInteger i=0;i<lines.count;++i) {
        double y=baselines[i].doubleValue-offset;
        if (y < -40 || y>viewport_height+40) continue;
        CGContextSetTextPosition(ctx,viewport.origin.x,panel_height-viewport.origin.y-y);
        CTLineDraw((__bridge CTLineRef)lines[i],ctx);
    }
    CGContextRestoreGState(ctx);
    double maximum=fmax(0,content_height-viewport_height);
    if (maximum>0) {
        double h=fmax(16,viewport_height*viewport_height/content_height);
        double y=viewport.origin.y+(viewport_height-h)*offset/maximum;
        CGContextSetRGBFillColor(ctx,1,0.62,0.20,1);
        CGContextFillRect(ctx,CGRectMake(CGRectGetMaxX(viewport)-5,panel_height-y-h,4,h));
    }
    label(ctx,[NSString stringWithFormat:@"%d%%",maximum>0 ? (int)lround(100*offset/maximum) : 100],viewport.origin.x,panel_height-552,8);
    label(ctx,xbox ? @"D-pad / ↑↓ / wheel  scroll    LB RB / PgUp PgDn  page    B / Esc  Back    F10  close" :
          @"D-pad / ↑↓ / wheel  scroll    L1 R1 / PgUp PgDn  page    ○ / Esc  Back    F10  close",viewport.origin.x,panel_height-602,8);
}

enum { CREATE=64, L3=256, R3=512 };
GuideShortcutResult guide_shortcut(GuideShortcut *s, double now, uint32_t phys, int home, int touch,
                                  int has_touchpad, int choice, int enabled, int menu_open) {
    GuideShortcutResult r={0};
    uint32_t edge=phys & ~s->last;
    int view=(phys & CREATE)!=0, r3=(phys & R3)!=0;
    int use_view=choice==3 || (choice==0 && !has_touchpad);
    s->consumed &= phys;
    s->modified &= phys;
    int sticks=(phys & (L3|R3))==(L3|R3);
    if (!sticks) { s->sticks=0; s->sticks_done=0; }
    else if (!s->sticks) { s->sticks=1; s->sticks_at=now; }
    if (!menu_open && !s->consumed && (enabled || use_view)) {
        if ((edge & CREATE) && !s->pending) {
            s->pending=1; s->done=0; s->ordered=enabled && !(s->last & R3) && !(edge & R3);
            s->create_at=now; s->inject_until=0;
        }
        if (s->pending && view) {
            r.hide |= CREATE;
            if (enabled && r3) {
                s->modified |= R3;
                r.hide |= R3;
                if (!s->combo && s->ordered && (edge & R3)) { s->combo=1; s->combo_at=now; }
                s->create_at=now; // suspend the ordinary View hold while R3 is modified
                if (s->combo && now-s->combo_at>=0.35) {
                    r.action=2; s->done=1; s->consumed |= CREATE|R3; s->inject_until=0;
                }
            } else {
                s->combo=0;
                if (use_view && !s->done && now-s->create_at>=0.5) { r.action=1; s->done=1; s->consumed |= CREATE; }
            }
        }
    }
    if (!view) {
        if (s->pending && !s->done && !menu_open) s->inject_until=now+0.12;
        s->pending=0; s->done=0; s->combo=0; s->ordered=0;
    }
    if (!r.action && !s->combo && !s->consumed) {
        if ((choice==0 && touch && !s->last_touch) || ((choice==1 || (choice==0 && !has_touchpad)) && home && !s->last_home)) r.action=1;
        if (choice==2 && sticks && !s->sticks_done && now-s->sticks_at>=0.4) { r.action=1; s->sticks_done=1; }
        if (menu_open && use_view && (edge & CREATE)) r.action=1;
    }
    if (r.action) { s->done=1; s->inject_until=0; }
    if (!menu_open && !r.action && now<s->inject_until) r.inject=CREATE;
    r.hide |= s->consumed;
    r.hide |= s->modified;
    s->last=phys; s->last_home=home; s->last_touch=touch;
    return r;
}
