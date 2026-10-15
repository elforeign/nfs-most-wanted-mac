// NFSU1 run 8 (120 fps): the highest frame rate of the screen a window is on. CGDisplayModeGetRefreshRate reports 0
// for an adaptive (ProMotion) panel and the presenter asked only the main display, so a 120 Hz laptop panel - or a
// TV set to 120 Hz while another display is main - paced at 60. NSScreen.maximumFramesPerSecond (macOS 12) answers
// for the window's own screen. Main thread only, as the host's window state already is.
#import <AppKit/AppKit.h>

double os_window_max_fps(void *ns_window) {
    NSWindow *window = (__bridge NSWindow *)ns_window;
    NSScreen *screen = window ? window.screen : NSScreen.mainScreen;
    if (!screen)
        return 0;
    if (@available(macOS 12.0, *))
        return (double)screen.maximumFramesPerSecond;
    return 0;
}

// NFSMW run 35 (GitHub PR #10): the strip a notched MacBook Pro keeps above a full-screen window, in pixels
// (safeAreaInsets.top x the backing scale: 74 on the 14" and 16" panels, so 3024x1964 -> 3024x1890 and 3456x2234 ->
// 3456x2160). 0 on a screen without a notch. Main thread only.
int os_window_notch_pixels(void *ns_window) {
    NSWindow *window = (__bridge NSWindow *)ns_window;
    NSScreen *screen = window ? window.screen : NSScreen.mainScreen;
    if (!screen)
        return 0;
    if (@available(macOS 12.0, *))
        return (int)lround(screen.safeAreaInsets.top * screen.backingScaleFactor);
    return 0;
}
