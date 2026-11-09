// Original/fixed: two tap edges 1/1 -> 2/2; 200 ms hitch save/hide 1/304 -> 0/0.
// Production shortcut state; measured broken controls are in the Python driver.
#include "replay_input.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void check(int ok,const char *why) { if(!ok) { fprintf(stderr,"FAIL replay input: %s\n",why); exit(1); } }
int main(int argc,char **argv) {
    if(argc!=2) return 2;
    ReplayInput s={0}; int save=0,saves=0;
    if(!strcmp(argv[1],"disabled")) {
        for(unsigned key=0;key<256;++key) {
            check(!replay_input_key(&s,key,1,0,0,0x77,0,&save) && !save,"disabled keyboard byte identity");
            check(!replay_input_key(&s,key,0,0,0,0x77,0,&save) && !save,"disabled keyboard byte identity");
        }
        replay_input_disconnect(&s);
        for(unsigned mask=0;mask<4096;++mask) {
            replay_input_disconnect(&s);
            ReplayPad r=replay_input_pad(&s,1,mask,0,0);
            check(!r.hide && !r.inject && !r.save,"disabled pad byte identity");
        }
        puts("disabled: 512 key events + 4096 pad states, 0 changed");
    } else if(!strcmp(argv[1],"key")) {
        for(int i=0;i<500;++i) {
            check(replay_input_key(&s,0x77,1,i%2,1,0x77,0,&save),"held key consumed"); saves+=save;
        }
        printf("key saves=%d expected=1\n",saves); fflush(stdout);
        check(saves==1,"held and repeated key saves once");
        check(replay_input_key(&s,0x77,0,0,0,0x76,1,&save) && !save,"consumed release swallowed after binding change");
        check(!replay_input_key(&s,0x77,0,0,0,0x76,0,&save),"release swallowed once");
        check(!replay_input_key(&s,0x76,1,0,0,0x77,0,&save),"disabled key reaches career");
        check(!replay_input_key(&s,0x76,1,1,1,0x76,0,&save) && !save,"enabling while held waits for release");
        check(!replay_input_key(&s,0x76,0,0,1,0x76,0,&save),"passed key release survives enabling and rebinding");
    } else if(!strcmp(argv[1],"release")) {
        replay_input_key(&s,0x77,1,0,1,0x77,0,&save);
        replay_input_cancel(&s);
        check(replay_input_key(&s,0x77,0,0,0,0x76,1,&save),"consumed release swallowed after focus loss");
        puts("focus key: consumed releases=1 leaked=0");
    } else if(!strcmp(argv[1],"pad")) {
        const unsigned chord=16|32|256;
        ReplayPad r=replay_input_pad(&s,1,16,1,0);
        check((r.hide&16) && !r.save,"chord prefix hidden from career");
        for(int i=0;i<500;++i) {
            r=replay_input_pad(&s,1.01+i*.01,chord,1,0); saves+=r.save;
            check((r.hide&chord)==chord && !r.inject,"consumed chord hidden from career");
        }
        printf("pad saves=%d expected=1\n",saves); fflush(stdout);
        check(saves==1,"held chord saves once");
        r=replay_input_pad(&s,7,16,1,0); check(!r.save && (r.hide&16),"partial release remains consumed");
        replay_input_pad(&s,8,0,1,0);
        r=replay_input_pad(&s,9,chord,1,0); check(r.save,"released chord can save again");
        replay_input_pad(&s,10,0,1,0);
        replay_input_pad(&s,11,chord,0,0);
        r=replay_input_pad(&s,12,chord,1,0); check(!r.save,"enabling a held pad chord waits for release");
        replay_input_pad(&s,13,0,1,0);
        check(replay_input_pad(&s,14,chord,1,0).save,"fresh chord after enabling saves once");
    } else if(!strcmp(argv[1],"cancel")) {
        replay_input_pad(&s,1,16,1,0);
        replay_input_cancel(&s);
        ReplayPad r=replay_input_pad(&s,2,16|32|256,1,0);
        check(!r.save && !r.inject,"focus loss cancels chord");
        replay_input_pad(&s,3,0,1,0);
        check(replay_input_pad(&s,4,16|32|256,1,0).save,"neutral rearms after focus loss");
        replay_input_pad(&s,4.1,0,1,0);
        replay_input_pad(&s,4.2,16,1,0);
        replay_input_disconnect(&s);
        r=replay_input_pad(&s,4.21,16|32|256,1,0);
        check(!r.save && !r.inject,"held reconnect cannot save");
        replay_input_pad(&s,6,0,1,0);
        check(replay_input_pad(&s,7,16|32|256,1,0).save,"neutral rearms after reconnect");
        puts("focus/disconnect: 0 accidental saves; 2 saves after neutral");
    } else if(!strcmp(argv[1],"tap")) {
        ReplayPad r=replay_input_pad(&s,1,16,1,0);
        check(r.hide&16,"solo prefix delayed");
        r=replay_input_pad(&s,1.02,0,1,0);
        check(r.inject==16 && !r.save,"solo career tap restored");
        r=replay_input_pad(&s,1.20,0,1,0); check(!r.inject,"tap pulse bounded");
        replay_input_pad(&s,2,32,1,0);
        r=replay_input_pad(&s,2.16,32,1,0); check(!(r.hide&32) && !r.save,"held career button restored");
        r=replay_input_pad(&s,3,16|32|256,1,0); check(!r.save,"late chord cannot steal held career input");
        replay_input_pad(&s,4,2048,1,0);
        r=replay_input_pad(&s,5,2048|16|32|256,1,0); check(r.save,"held accelerator does not prevent a fresh chord");
        puts("career: restored tap=16, restored hold=32, 0 late steals");
    } else if(!strcmp(argv[1],"double-tap")) {
        const double times[]={0,.04,.09,.13,.26};
        const unsigned buttons[]={32,0,32,0,0};
        unsigned previous=0,presses=0,releases=0;
        for(unsigned i=0;i<5;++i) {
            ReplayPad r=replay_input_pad(&s,times[i],buttons[i],1,0);
            unsigned game=(buttons[i]&~r.hide)|r.inject;
            presses+=!!((game&~previous)&32); releases+=!!((previous&~game)&32);
            previous=game;
        }
        printf("double tap: presses=%u releases=%u expected=2/2\n",presses,releases); fflush(stdout);
        check(presses==2 && releases==2,"two solo taps stay separate");
    } else if(!strcmp(argv[1],"hitch")) {
        replay_input_pad(&s,0,16,1,0);
        ReplayPad r=replay_input_pad(&s,.2,16|32|256,1,0);
        printf("hitch: saves=%d hidden=%u expected=0/0\n",r.save,r.hide); fflush(stdout);
        check(!r.save && !r.hide,"expired prefix cannot become a chord");
    } else if(!strcmp(argv[1],"collision")) {
        check(!replay_input_key(&s,0x77,1,0,1,0x77,1,&save) && !save,"career/menu key wins collision");
        check(!replay_input_key(&s,0x77,0,0,1,0x77,0,&save),"passed key release reaches career after collision clears");
        ReplayPad r=replay_input_pad(&s,1,16|32|256,1,1);
        check(!r.save && !r.hide,"menu/guide wins collision");
        replay_input_pad(&s,2,0,1,0);
        r=replay_input_pad(&s,3,16|32|256|512,1,0);
        check(!r.save && !r.hide,"menu stick chord wins collision");
        puts("collisions: 3/3 preserve menu, guide and career priority");
    } else return 2;
    return 0;
}
