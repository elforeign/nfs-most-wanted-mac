// Consent-gated, asynchronous GitHub submission. Public builds only.
#pragma once
#if defined(NFS_PUBLIC) && NFS_PUBLIC && defined(__OBJC__)
#import <Foundation/Foundation.h>
typedef void (^ReportTaskDone)(int status, NSData *output, BOOL uncertain);
typedef void (^ReportTaskRunner)(NSArray<NSString *> *arguments, NSData *input, double seconds, ReportTaskDone done);
typedef NS_ENUM(NSInteger, ReportSubmitState) {
    ReportSubmitIdle, ReportSubmitChecking, ReportSubmitReady, ReportSubmitPosting,
    ReportSubmitFailed, ReportSubmitUnknown, ReportSubmitCreated
};
@interface ReportSubmission : NSObject
@property(nonatomic, readonly) NSDictionary *snapshot;
@property(nonatomic, readonly) ReportSubmitState state;
@property(nonatomic, readonly) NSString *account;
@property(nonatomic, readonly) NSString *issueURL;
@property(nonatomic, readonly) NSString *message;
@property(nonatomic, copy) void (^changed)(void);
- (instancetype)initWithSnapshot:(NSDictionary *)snapshot runner:(ReportTaskRunner)runner;
- (void)checkAccount;
- (void)post;
- (void)forgetAccount;
@end
ReportTaskRunner ReportGitHubRunner(void);
BOOL ReportValidIssueURL(NSString *text);
BOOL ReportAutomaticAttachmentsSupported(void);
#endif
