// os_update_macos.mm - the macOS side of the opt-in update check (host/update_check.cpp, NFSMW 3.0.4): one HTTPS
// request to GitHub's latest-release API, the app's own version, and opening a page in the browser.
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include <stdio.h>
#include <string.h>

extern "C" int os_app_version(char *out, size_t cap) {
    if (!out || !cap)
        return 0;
    NSString *v = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    snprintf(out, cap, "%s", v ? v.UTF8String : "");
    return v != nil;
}

extern "C" int os_open_url(const char *url) {
    if (!url || !*url)
        return 0;
    NSURL *u = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
    if (!u || ![u.scheme isEqualToString:@"https"])
        return 0;
    dispatch_async(dispatch_get_main_queue(), ^{
      [[NSWorkspace sharedWorkspace] openURL:u];
    });
    return 1;
}

// GET https://api.github.com/repos/<repo>/releases/latest; `done` runs on a background queue with the tag and the
// release page (html_url), or ok 0.
extern "C" int os_update_fetch_latest(const char *repo, void (*done)(int ok, const char *tag, const char *url)) {
    if (!repo || !*repo || !done)
        return 0;
    NSString *s = [NSString stringWithFormat:@"https://api.github.com/repos/%s/releases/latest", repo];
    NSURL *u = [NSURL URLWithString:s];
    if (!u)
        return 0;
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:u
                                                       cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                                   timeoutInterval:15];
    [req setValue:@"application/vnd.github+json" forHTTPHeaderField:@"Accept"];
    NSURLSessionDataTask *task = [[NSURLSession sharedSession]
        dataTaskWithRequest:req
          completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
            NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
            NSDictionary *json = nil;
            if (!error && data && [http isKindOfClass:[NSHTTPURLResponse class]] && http.statusCode == 200)
                json = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            NSString *tag = [json isKindOfClass:[NSDictionary class]] ? json[@"tag_name"] : nil;
            NSString *page = [json isKindOfClass:[NSDictionary class]] ? json[@"html_url"] : nil;
            const bool ok = [tag isKindOfClass:[NSString class]] && tag.length;
            done(ok ? 1 : 0, ok ? tag.UTF8String : "",
                 [page isKindOfClass:[NSString class]] ? page.UTF8String : "");
          }];
    [task resume];
    return 1;
}
