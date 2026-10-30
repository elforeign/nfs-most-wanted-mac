// Pixel equivalence and raster budgets for the actual menu renderer; no game/GPU.
// Driven by menu_raster_test.py. Good: 8361 checks / 0 failures; 3828 pixel states
// match the original renderer (4176 checks / 0 failures). Broken source controls:
// old-refresh 9/3, old-keyboard 9/3, hidden-guide 9/1, hidden-row 9/4,
// no-restore 120/1 (checks/failures). Unchanged/Guide: 0 rasters per 10 seconds;
// old refresh: 10. Keyboard: 0 full + 100 partial per 100 moves; old: 100 full.
#define main guide_fixture_main
#define osk_note_begin fixture_note_begin
#define osk_note_end fixture_note_end
#define osk_last_pad fixture_last_pad
#define osk_pass fixture_osk_pass
#include "guide_test.m"
#undef main
#undef osk_note_begin
#undef osk_note_end
#undef osk_last_pad
#undef osk_pass
void osk_note_end(int cancel);
#include "onscreen_keyboard.c"
int prompts_last_input(void) { return 1; }
void prompts_input(int p) { (void)p; }

static int live_value, hidden_reads;
static void live_text(char *b, int n) { snprintf(b,n,"Readout %d",live_value); }
static void hidden_text(char *b, int n) { ++hidden_reads; snprintf(b,n,"Hidden %d",live_value); }
static void digest(const char *label, CGContextRef ctx) {
    CHECK(ctx!=NULL);
    const unsigned char *p=CGBitmapContextGetData(ctx);
    size_t n=CGBitmapContextGetBytesPerRow(ctx)*CGBitmapContextGetHeight(ctx);
    uint64_t h=14695981039346656037ull;
    for(size_t i=0;i<n;++i) { h^=p[i]; h*=1099511628211ull; }
    printf("pixels %s %zu %016llx\n",label,n,(unsigned long long)h);
}
static NSData *pixels_of(CGContextRef ctx) {
    return [NSData dataWithBytes:CGBitmapContextGetData(ctx)
        length:CGBitmapContextGetBytesPerRow(ctx)*CGBitmapContextGetHeight(ctx)];
}
static void panel_sample(const char *name) {
    CHECK(render()); digest(name,last_ctx);
#ifndef MENU_ORIGINAL
    NSData *cached=pixels_of(last_ctx);
    [text_layouts removeAllObjects];
    CGContextRelease(panel_background); panel_background=NULL;
    CHECK(render()); CHECK([cached isEqualToData:pixels_of(last_ctx)]);
#endif
}
static void keyboard_sample(const char *name) {
    keyboard_update(); digest(name,keyboard_ctx);
#ifndef MENU_ORIGINAL
    NSData *incremental=pixels_of(keyboard_ctx);
    CGContextRelease(keyboard_ctx); keyboard_ctx=NULL;
    keyboard_update();
    if (![incremental isEqualToData:pixels_of(keyboard_ctx)]) {
        const unsigned char *a=incremental.bytes, *b=CGBitmapContextGetData(keyboard_ctx);
        size_t W=CGBitmapContextGetWidth(keyboard_ctx), H=CGBitmapContextGetHeight(keyboard_ctx);
        int x0=(int)W,y0=(int)H,x1=0,y1=0; size_t count=0;
        for(size_t y=0;y<H;++y) for(size_t x=0;x<W;++x) if(memcmp(a+(y*W+x)*4,b+(y*W+x)*4,4)) {
            x0=fmin(x0,x); x1=fmax(x1,x); y0=fmin(y0,y); y1=fmax(y1,y); ++count;
        }
        fprintf(stderr,"mismatch %s: %zu pixels bbox %d,%d..%d,%d W=%zu H=%zu\n",name,count,x0,y0,x1,y1,W,H);
        printf("menu raster: %d checks, %d failures\n",checks+1,failures+1);
        exit(1);
    }
    CHECK([incremental isEqualToData:pixels_of(keyboard_ctx)]);
#endif
}
static void raster_budgets(void) {
#ifndef MENU_ORIGINAL
    guide_close(); tab=SET_PICTURE; top=0; sel=1; dirty=1; next_refresh=0;
    portmenu_update(); uint64_t before=panel_rasters;
    for(int second=1;second<=10;++second) { ticks+=1000000000; portmenu_update(); }
    printf("budget unchanged: %llu full rasters / 10 seconds\n",(unsigned long long)(panel_rasters-before));
    CHECK(panel_rasters==before);
    live_value++; ticks+=1000000000; portmenu_update(); CHECK(panel_rasters==before+1);
    before=panel_rasters;
    // The changing row is registered but below the viewport.
    hidden_reads=0;
    for(int second=0;second<5;++second) { ticks+=1000000000; portmenu_update(); }
    CHECK(hidden_reads==0); CHECK(panel_rasters==before);
    load_document(resource(0),0); dirty=1; portmenu_update(); before=panel_rasters;
    for(int second=0;second<10;++second) { live_value++; ticks+=1000000000; portmenu_update(); }
    printf("budget Guide: %llu full rasters / 10 seconds\n",(unsigned long long)(panel_rasters-before));
    CHECK(panel_rasters==before); guide_close();
    // Status expiry is a visible change; hidden expiry waits for return from Guide.
    portmenu_status("Fixture status"); portmenu_update(); before=panel_rasters;
    ticks+=9000000000; portmenu_update(); CHECK(panel_rasters==before+1);
    // One initial bitmap, then two changed keys per navigation event.
    char note_buffer[96]="abc"; osk_note_begin(note_buffer,sizeof note_buffer,0);
    keyboard_update(); before=keyboard_full_rasters; uint64_t regions=keyboard_region_rasters, changed_regions=keyboard_changed_regions;
    for(int i=0;i<100;++i) { osk_navigation(1,0); keyboard_update(); }
    printf("budget keyboard: %llu full, %llu partial / 100 moves\n",
        (unsigned long long)(keyboard_full_rasters-before),(unsigned long long)(keyboard_region_rasters-regions));
    CHECK(keyboard_full_rasters==before); CHECK(keyboard_region_rasters-regions==100); CHECK(keyboard_changed_regions-changed_regions==200);
    osk_note_end(0);
#endif
}
int main(int argc,char **argv) {
    (void)argc; (void)argv;
    @autoreleasepool {
        G=calloc(1,0x1000000); ticks=1000000000; diag_on=1;
        layer=[CAMetalLayer layer]; layer.bounds=CGRectMake(0,0,1280,720);
        layers(1); is_open=1; present[0]=1;
        static Setting fixtures[SET_NCATEGORIES][18];
        static const char *const choices[]={"Off","A choice","café 日本語"};
        for(int cat=0;cat<SET_NCATEGORIES;++cat) for(int i=0;i<18;++i) {
            Setting *s=&fixtures[cat][i];
            s->id=strdup([[NSString stringWithFormat:@"fixture.%d.%d",cat,i] UTF8String]);
            s->category=cat; s->order=i; s->label="Fixture label"; s->help="A wrapped description: café 日本語 🚗. A longer line to exercise CoreText layout.";
            s->type=i==0 ? SET_HEADING : i==1 ? SET_CHOICE : i==2 ? SET_TOGGLE : i==3 ? SET_SLIDER :
                    i==4 ? SET_INFO : i==5 ? SET_ACTION : i==6 ? SET_TEXT : SET_CHOICE;
            s->choices=choices; s->nchoices=3; s->value=i%3; s->min=0; s->max=100; s->step=1;
            s->restart=i==7; s->locked=i==8; s->env=i==8 ? "FIXTURE_SWITCH" : NULL;
            strcpy(s->env_text,"test"); strcpy(s->text,"café note");
            if(i==4 || i==5) s->value_text=live_text;
            if(i==17) { s->type=SET_INFO; s->value_text=hidden_text; }
            settings_add(s);
        }
        if(argc>1) {
            raster_budgets();
            printf("menu raster: %d checks, %d failures\n",checks,failures);
            free(G); return failures!=0;
        }
        for(int size=0;size<3;++size) {
            layer.bounds= size==0 ? CGRectMake(0,0,1280,720) : size==1 ? CGRectMake(0,0,853,533) : CGRectMake(0,0,3840,2160);
            for(int devtype=0;devtype<4;++devtype) {
                device=devtype%3; xbox=devtype==3; xbox_pad[0]=xbox;
                for(int cat=0;cat<SET_NCATEGORIES;++cat) for(int scrolled=0;scrolled<2;++scrolled) {
                    tab=cat; top=scrolled ? 5 : 0; sel=scrolled ? 8 : 1; hover=3; hover_tab=(cat+1)%SET_NCATEGORIES;
                    char name[80]; snprintf(name,sizeof name,"tab-%d-%d-%d-%d",size,devtype,cat,scrolled); panel_sample(name);
                }
                tab=SET_PICTURE; top=0; sel=6; editing=1; panel_sample("editing"); editing=0;
                status_until=ticks/1e9+8; strcpy(status,"Fixture status café"); panel_sample("status"); status_until=0;
                for(int doc=0;doc<2;++doc) {
                    load_document(resource(doc),doc);
                    // Every scroll page, including the final clamped position.
                    for(int p=0;p<200;++p) {
                        char name[80]; snprintf(name,sizeof name,"guide-%d-%d-%d-%d",size,devtype,doc,p); panel_sample(name);
                        double previous_offset=offset; guide_command(GUIDE_PAGE_DOWN);
                        if(offset==previous_offset) break;
                    }
                    guide_close();
                }
                char note_buffer[96]="café"; osk_note_begin(note_buffer,sizeof note_buffer,0);
                for(int page_number=0;page_number<3;++page_number) {
                    page=page_number;
                    for(int shift=0;shift<2;++shift) {
                        shifted=shift;
                        for(row=0;row<5;++row) for(column=0;column<osk_key_count(row);++column) {
                            char name[80]; snprintf(name,sizeof name,"key-%d-%d-%d-%d-%d-%d",size,devtype,page_number,shift,row,column);
                            keyboard_sample(name);
                        }
                    }
                }
                // Same-length text replacement, long clipped UTF-8, deletion, disabled keys and owner changes.
                row=column=page=shifted=0;
                strcpy(note.text,"日本語 🚗"); note.length=6; keyboard_sample("text-utf8");
                memset(note.text,'W',sizeof note.text-1); note.text[sizeof note.text-1]=0; keyboard_sample("text-long");
                strcpy(note.text,"abc"); keyboard_sample("text-replace");
                note.text[0]=0; note.length=0; keyboard_sample("text-empty");
                memset(note.allowed,0,sizeof note.allowed); ++note.generation; keyboard_sample("disabled");
                is_open=0; note.note=0; ++note.generation; keyboard_sample("title"); note.note=1; is_open=1;
                osk_note_end(0); keyboard_update();
            }
        }
        layer.bounds=CGRectMake(0,0,1280,720); device=0; xbox=0;
        raster_budgets();
        printf("menu raster: %d checks, %d failures\n",checks,failures);
        free(G); return failures!=0;
    }
}
