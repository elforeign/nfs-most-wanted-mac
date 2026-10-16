// focus_note.h - when a window focus change is worth telling the guest about.
#pragma once

// The host's view of the window focus as the guest has been told it. A change is noted (and queued for apply_focus)
// when it differs from the last one, or while no activation has reached a guest window yet: apply_focus drops a
// change that comes before the guest's CreateWindow, and a window focused from the start sends no further event.
// NFS Underground (NFSU1 run 2, 387564e) was focused before its window existed, never saw WM_ACTIVATEAPP(1), believed
// it was in the background and blocked in GetMessageA. NFSMW has the same start-up order without the symptom.
struct FocusNote {
    bool focused = false;
    bool activated = false; // a change has been queued while the guest had a window

    // true: queue this change for the guest.
    bool note(bool now_focused, bool guest_has_window) {
        if (now_focused == focused && activated)
            return false;
        focused = now_focused;
        activated = guest_has_window;
        return true;
    }
};
