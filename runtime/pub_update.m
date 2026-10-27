// pub_update.m - the PUBLIC build's "Check for updates" button (NFS_PUBLIC only), next to Quit/Restart game in
// F10 > Display & performance. The logic is pub_update_core.h; this is the macOS side: the app's version, one HTTPS
// request (15 s timeout) to GitHub's latest-release API, and opening the release page. Never automatic.
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#include "pub_update_core.h"
#include "settings.h"

#ifndef PUB_RELEASES_REPO
#define PUB_RELEASES_REPO "elforeign/nfs-most-wanted-mac"   // build.sh may override (the public repository)
#endif

static int mac_version(char *out, size_t cap) {
    NSString *v = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    snprintf(out, cap, "%s", v ? v.UTF8String : "");
    return v != nil;
}
static int mac_open(const char *url) {
    NSURL *u = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
    if (!u || ![u.scheme isEqualToString:@"https"]) return 0;
    dispatch_async(dispatch_get_main_queue(), ^{ [[NSWorkspace sharedWorkspace] openURL:u]; });
    return 1;
}
static int mac_fetch(void) {
    NSURL *u = [NSURL URLWithString:[NSString stringWithFormat:@"https://api.github.com/repos/%s/releases/latest", PUB_RELEASES_REPO]];
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:u cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:15];
    [req setValue:@"application/vnd.github+json" forHTTPHeaderField:@"Accept"];
    [[[NSURLSession sharedSession] dataTaskWithRequest:req completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        int ok = !err && data && [resp isKindOfClass:[NSHTTPURLResponse class]] && ((NSHTTPURLResponse *)resp).statusCode == 200;
        NSString *body = ok ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{ pu_done(body != nil, body.UTF8String); });   // state changes on the main thread only
    }] resume];
    return 1;
}

static void update_press(int unused) { (void)unused; pu_press(mac_version, mac_fetch, mac_open); }
static void update_text(char *buf, int n) { pu_status(buf, n); }
static Setting s_update = {.id = "online.check_updates", .category = SET_DISPLAY, .order = 61, .label = "Check for updates", .type = SET_ACTION,
    .help = "Asks GitHub once, only when you press it, whether a newer version is out. If so, press again to open its page.",
    .apply = update_press, .value_text = update_text};

__attribute__((constructor)) static void pub_update_register(void) { settings_add(&s_update); }
