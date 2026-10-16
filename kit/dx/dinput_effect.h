// dinput_effect.h - DirectInput force feedback on the virtual pad, played on its rumble motors.
//
// Off unless RECOMP_DINPUT_FF=1 (a game's core mod sets it from its setting) and the DirectInput joystick is served:
// then the pad's X and Y axes are force-feedback actuators (DIDOI_FFACTUATOR), SendForceFeedbackCommand succeeds,
// and CreateEffect makes IDirectInputEffect objects whose levels drive host_pad_rumble. Off, every call answers as
// before (no actuators, DIERR_UNSUPPORTED).
#pragma once

#include "com.h"

#include <stdint.h>

// RECOMP_DINPUT_FF=1 and the joystick is served (read on every call).
bool dinput_ff_served();
// IDirectInputDevice2+ slots on a joystick device (the caller has checked `d`).
void dinput_ff_create_effect(X86 *c, ComObj *d);
uint32_t dinput_ff_command(ComObj *d, uint32_t flags);
uint32_t dinput_ff_state(ComObj *d, uint32_t out);
// DIPROP_FFGAIN / DIPROP_AUTOCENTER on the joystick. True when handled (`*hr` set).
bool dinput_ff_get_property(ComObj *d, uint32_t prop, uint32_t ph, uint32_t *hr);
bool dinput_ff_set_property(ComObj *d, uint32_t prop, uint32_t ph, uint32_t *hr);
// Real DirectInput stops a device's effects when it is unacquired (and on losing the foreground).
void dinput_ff_device_unacquired(ComObj *d);
// The device read the pad (Poll / GetDeviceState): finite effects expire and a game that stopped talking to its
// effects (a pause menu) goes quiet. Cheap when nothing plays.
void dinput_ff_pump();
void dinput_ff_register();
// Tests: drop every effect and the motor state.
void dinput_ff_reset();
