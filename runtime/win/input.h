#pragma once
#include "host.h"
enum { WIN_KEYBOARD=1, WIN_MOUSE=2, WIN_PAD=3, WIN_PAD_COUNT=2, WIN_PAD_AXES=6 };
typedef struct { uint32_t offset,data,time,sequence; } WinInputEvent;
// Six axes in guest order X/Y/Z/Rx/Ry/Rz; triggers Z/Rz rest at centre.
typedef struct { uint32_t axes[WIN_PAD_AXES],pov,buttons; } WinPadState;
void win_input_focus(int);
unsigned win_input_key_state(unsigned);
unsigned win_input_button_state(unsigned);
void win_input_connect(unsigned,int);
void win_input_key(unsigned,int);
void win_input_button(unsigned,int);
void win_input_motion(int32_t,int32_t,int32_t);
void win_input_state(unsigned,uint32_t,uint32_t);
uint32_t win_input_tail(unsigned);
unsigned win_input_events(unsigned,uint32_t *,WinInputEvent *,unsigned,unsigned,int *);
void win_input_pad(unsigned,int,const WinPadState *);
void win_input_pad_read(unsigned,WinPadState *);
