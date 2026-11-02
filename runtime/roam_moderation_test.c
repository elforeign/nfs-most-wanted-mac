// Authentication/moderation controls use production codecs and relay; sockets are loopback-only in the driver.
// Ban ordering proof in roam_shared_test.py: pinned predecessor 9 checks/5 failures; fixed 8/0.
#include "roam_core.h"
#include "roam_moderation.h"
#include "roam_test_peer.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
static unsigned checks, failures;
#define CHECK(x, name) do { checks++; if (!(x)) { failures++; printf("FAIL %s line=%d\n", name, __LINE__); } } while (0)
typedef struct { RoamClient *c; uint32_t now; int n; uint8_t packet[1200]; } Capture;
static int capture(void *ctx, const struct sockaddr_in *to, const uint8_t *b, int n) {
    (void)to; Capture *c = ctx; c->n = n; memcpy(c->packet, b, (size_t)n);
    if (c->c) roam_client_input(c->c, b, n, c->now); return n;
}
static struct sockaddr_in address(unsigned port) {
    return (struct sockaddr_in){.sin_family=AF_INET, .sin_port=htons((uint16_t)port), .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
}
static void prepare(RoamRelay *r, RoamClient *c, struct sockaddr_in *a, Capture *out, uint32_t now, uint8_t b[1200]) {
    *out = (Capture){.c=c,.now=now}; roam_relay_sender(r, capture, out);
    c->has_cookie = c->welcomed = 0;
    int n = roam_client_hello(c, b); CHECK(n == ROAM_HDR+ROAM_HELLO_BASE, "initial_size");
    roam_relay_input(r, b, n, a, now); CHECK(c->has_cookie, "fresh_challenge");
    CHECK(roam_client_hello(c, b) == ROAM_HELLO_BYTES, "proof_size");
}
static void auth_test(void) {
    RoamRelay *r = roam_relay_new(-1,32,4); CHECK(r != NULL, "relay"); if (!r) return;
    RoamClient c; roam_client_init(&c,7,"NameSentinel");
    CHECK(!c.auth.ready, "off_init_no_key");
    struct sockaddr_in a = address(18000); Capture out; uint8_t b[1200], saved[ROAM_HELLO_BYTES];
    prepare(r,&c,&a,&out,1000,b); memcpy(saved,b,sizeof saved);
    CHECK(!roam_relay_stats(r).signature_checks && !roam_relay_stats(r).clients, "cookie_before_crypto");
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,1000);
    CHECK(c.welcomed && roam_relay_stats(r).clients == 1, "valid_signature");
    uint16_t session = c.id; uint8_t id[32];
    CHECK(roam_relay_identity(r,session,id), "verified_identifier");
    CHECK(roam_relay_kick(r,session) && !roam_relay_stats(r).clients, "kick");
    uint64_t verified = roam_relay_stats(r).signature_checks;
    roam_relay_input(r,saved,sizeof saved,&a,1001);
    CHECK(!roam_relay_stats(r).clients && roam_relay_stats(r).replay_rejected == 1, "replayed_admission");
    CHECK(roam_relay_stats(r).signature_checks == verified, "replay_before_crypto");
    for (int variant = 0; variant < 9; ++variant) {
        uint32_t now = 1100 + (uint32_t)variant*100;
        prepare(r,&c,&a,&out,now,b);
        uint64_t before = roam_relay_stats(r).signature_checks;
        if (variant == 0) memset(b+ROAM_HDR+ROAM_HELLO_BASE+20,0,73); // copied public key, no private proof
        if (variant == 1) b[ROAM_HDR+ROAM_HELLO_BASE+25] ^= 1; // bad signature
        if (variant == 2) b[ROAM_HDR] ^= 1; // room
        if (variant == 3) b[ROAM_HDR+4] ^= 1; // name
        if (variant == 4) b[ROAM_HDR+22] ^= 1; // public point
        if (variant == 5) a.sin_port = htons(18001); // copied endpoint transcript
        if (variant == 6) b[ROAM_HDR+ROAM_HELLO_BASE+4] ^= 1; // cookie
        if (variant == 7) now += 11000; // stale proof
        if (variant == 8) {
            RoamAuth impostor; CHECK(roam_auth_generate(&impostor), "impostor_key");
            CHECK(roam_auth_sign(&impostor,ROAM_VERSION,b+ROAM_HDR,b+ROAM_HDR+ROAM_HELLO_BASE,b+ROAM_HDR+ROAM_HELLO_BASE+21,b+ROAM_HDR+ROAM_HELLO_BASE+20), "impostor_sign");
            roam_auth_clear(&impostor);
        }
        c.welcomed = 0;
        roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,now);
        CHECK(!roam_relay_stats(r).clients && !c.welcomed, "signature_required");
        if (variant >= 2 && variant <= 7) CHECK(roam_relay_stats(r).signature_checks == before, "tamper_before_crypto");
        a.sin_port = htons(18000);
    }
    roam_relay_free(r);
    // Valid cookies do not bypass admission or full-capacity checks before crypto.
    r = roam_relay_new(-1,1,1); roam_client_init(&c,7,"full"); CHECK(roam_test_join(r,&c,&a,1000),"full_join");
    RoamClient other; roam_client_init(&other,7,"other"); a.sin_port = htons(18002);
    prepare(r,&other,&a,&out,1001,b); verified = roam_relay_stats(r).signature_checks;
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,1001);
    CHECK(roam_relay_stats(r).signature_checks == verified && other.last_err == ROAM_ERR_FULL, "full_before_crypto");
    roam_relay_free(r);
    RoamRelayPolicy p = roam_relay_policy(); p.admission_burst = 0;
    r = roam_relay_new_policy(-1,1,1,&p); prepare(r,&other,&a,&out,1000,b);
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,1000);
    CHECK(!roam_relay_stats(r).signature_checks && !roam_relay_stats(r).clients, "admission_before_crypto"); roam_relay_free(r);
    p = roam_relay_policy(); p.signature_burst = 0;
    r = roam_relay_new_policy(-1,1,1,&p); prepare(r,&other,&a,&out,1000,b);
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,1000);
    CHECK(!roam_relay_stats(r).signature_checks && roam_relay_stats(r).auth_limited == 1, "signature_budget"); roam_relay_free(r);
    printf("authentication: checks=%u failures=%u\n",checks,failures);
}
static void command(RoamModeration *m, RoamRelay *r, const char *s, const char *prefix, char out[ROAM_ADMIN_OUTPUT]) {
    int n = roam_moderation_command(m,r,s,strlen(s),out);
    CHECK(n > 0 && n < ROAM_ADMIN_OUTPUT && !strncmp(out,prefix,strlen(prefix)), "admin_reply");
}
static void storage_test(const char *state, const char *admin, const char *userdata) {
    unsigned start = checks, bad = failures; RoamAuth key, again;
    CHECK(roam_auth_load(&key,userdata), "persistent_key_create");
    CHECK(roam_auth_load(&again,userdata) && !memcmp(key.secret,again.secret,97), "persistent_key_reload");
    RoamModeration *m = roam_moderation_open(state,admin); CHECK(m != NULL, "moderation_open"); if (!m) { printf("moderation error=%d\n",errno); return; }
    RoamModeration *duplicate = roam_moderation_open(state,admin);
    CHECK(!duplicate, "single_writer"); roam_moderation_close(duplicate);
    RoamRelay *r = roam_test_relay(-1,32,4); roam_relay_moderation(r,m);
    RoamClient c, second; roam_client_init(&c,7,"NameSentinel"); c.auth = key;
    struct sockaddr_in a = address(19000), baddr = address(19001);
    CHECK(roam_test_join(r,&c,&a,1000), "moderation_join");
    roam_client_init(&second,7,"second"); second.auth = key;
    CHECK(roam_test_join(r,&second,&baddr,1000), "same_installation_sessions");
    char response[ROAM_ADMIN_OUTPUT], input[180]; uint8_t id[32]; char hex[65];
    CHECK(roam_relay_identity(r,c.id,id),"admin_identity"); roam_auth_hex(id,hex);
    command(m,r,"list 0\n","",response);
    CHECK(strstr(response,hex) && !strstr(response,"NameSentinel") && !strstr(response,"127.0.0.1"),"private_admin_list");
    snprintf(input,sizeof input,"ban %u 60\n",c.id); command(m,r,input,"OK\n",response);
    CHECK(!roam_relay_stats(r).clients,"ban_existing_sessions");
    c.welcomed = 0; uint64_t verified = roam_relay_stats(r).signature_checks;
    CHECK(!roam_test_join(r,&c,&a,1010),"ban_reconnect");
    CHECK(roam_relay_stats(r).signature_checks > verified && c.last_err == ROAM_ERR_BANNED,"ban_after_crypto");
    roam_relay_free(r); roam_moderation_close(m);
    m = roam_moderation_open(state,admin); CHECK(m != NULL,"restart_storage"); if (!m) return;
    r = roam_test_relay(-1,32,4); roam_relay_moderation(r,m);
    CHECK(!roam_test_join(r,&c,&a,2000),"persistent_ban_restart");
    uint64_t now = roam_moderation_utc();
    CHECK(roam_moderation_banned(m,id,now),"restart_safe_utc");
    CHECK(!roam_moderation_banned(m,id,now+61),"ban_expiry");
    snprintf(input,sizeof input,"unban %s\n",hex); command(m,r,input,"OK\n",response);
    CHECK(roam_test_join(r,&c,&a,2010),"unban_reconnect");
    const char *invalid[] = {"", "list\n", "list -1\n", "list 65536\n", "kick 0\n", "kick +1\n", "ban 1 0\n", "ban 1 2592001\n", "ban 1 18446744073709551616\n", "ban key 60\n", "unban 1\n", "list 0\nkick 1\n", "list 0\r\n", "list 0", "list 0 \n"};
    for (size_t i=0;i<sizeof invalid/sizeof *invalid;++i) command(m,r,invalid[i],"ERR syntax\n",response);
    char huge[ROAM_ADMIN_INPUT+3]; memset(huge,'X',sizeof huge); huge[sizeof huge-1]='\n';
    CHECK(roam_moderation_command(m,r,huge,sizeof huge,response)>0 && !strcmp(response,"ERR syntax\n"),"admin_oversize");
    char nul[] = "list 0\0junk\n";
    CHECK(roam_moderation_command(m,r,nul,sizeof nul-1,response)>0 && !strcmp(response,"ERR syntax\n"),"admin_nul");
    command(m,r,"kick 65535\n","ERR session\n",response);
    // Unwritable state and unsafe existing bans must produce an explicit error, preserving enforcement.
    CHECK(!chmod(state,0500),"chmod_state");
    snprintf(input,sizeof input,"ban %u 60\n",c.id); command(m,r,input,"ERR storage ",response);
    CHECK(!roam_moderation_banned(m,id,now),"failed_write_no_success"); CHECK(!chmod(state,0700),"restore_state");
    char path[4096]; snprintf(path,sizeof path,"%s/bans",state); CHECK(!chmod(path,0644),"chmod_bans");
    command(m,r,input,"ERR storage ",response); CHECK(!chmod(path,0600),"restore_bans");
    roam_relay_free(r); roam_moderation_close(m);
    m = roam_moderation_open(state,admin); CHECK(m != NULL && !roam_moderation_banned(m,id,now),"persistent_unban_restart"); roam_moderation_close(m);
    roam_auth_clear(&key); roam_auth_clear(&again);
    printf("moderation: checks=%u failures=%u; restart/expiry/unban exercised\n",checks-start,failures-bad);
}
static int write_file(const char *root, const char *name, const void *bytes, size_t n) {
    char path[4096]; snprintf(path,sizeof path,"%s/%s",root,name);
    int fd = open(path,O_WRONLY|O_CREAT|O_TRUNC,0600); if (fd<0) return 0;
    int ok = write(fd,bytes,n) == (ssize_t)n; close(fd); return ok;
}
static int fixture(const char *directory, int verify) {
    char path[4096]; uint8_t body[ROAM_HELLO_BASE+20] = {0}, sig[72] = {0};
    if (verify) {
        snprintf(path,sizeof path,"%s/request",directory); FILE *f=fopen(path,"rb");
        if (!f || fread(body,1,sizeof body,f)!=sizeof body) return 1;
        fclose(f); snprintf(path,sizeof path,"%s/foreign.sig",directory); f=fopen(path,"rb"); if (!f) return 1;
        size_t n=fread(sig,1,sizeof sig,f); int extra=fgetc(f); fclose(f);
        int ok=extra==EOF && roam_auth_verify(ROAM_VERSION,body,body+ROAM_HELLO_BASE,sig,(uint8_t)n);
        puts(ok?"cross-provider verify PASS":"cross-provider verify FAIL"); return !ok;
    }
    RoamAuth a; if (!roam_auth_generate(&a)) return 1;
    body[0]=7; memcpy(body+4,"NameSentinel",12); memcpy(body+20,a.secret,65); memcpy(body+ROAM_HELLO_BASE,"CookieSentinel",14);
    uint8_t length; int ok=roam_auth_sign(&a,ROAM_VERSION,body,body+ROAM_HELLO_BASE,sig,&length);
    ok = ok && write_file(directory,"request",body,sizeof body) && write_file(directory,"native.sig",sig,length) && write_file(directory,"key",a.secret,97);
    roam_auth_clear(&a); return !ok;
}
// Alias regression: original 10 checks/3 failures; fixed 10 checks/0 failures.
// A valid signature over a hybrid alias must never reach identity hashing or admission.
static int alias_test(const char *state, const char *admin) {
    RoamAuth key; CHECK(roam_auth_generate(&key), "alias_key");
    RoamModeration *m = roam_moderation_open(state,admin);
    CHECK(m != NULL,"alias_storage"); if (!m) return 1;
    RoamRelay *r = roam_test_relay(-1,32,4); roam_relay_moderation(r,m);
    RoamClient c; roam_client_init(&c,7,"alias"); c.auth = key;
    struct sockaddr_in a = address(19100); Capture out; uint8_t b[1200];
    CHECK(roam_test_join(r,&c,&a,1000),"alias_original_join");
    uint8_t id[32]; CHECK(roam_relay_identity(r,c.id,id),"alias_original_identifier");
    uint64_t utc = roam_moderation_utc();
    CHECK(roam_moderation_ban(m,id,utc+60,utc),"alias_original_ban");
    roam_relay_enforce_bans(r); CHECK(!roam_relay_stats(r).clients,"alias_ban_removes_original");
    // Obtain a genuine cookie for the alternate encoding, then sign that transcript.
    uint8_t *hello = b+ROAM_HDR;
    roam_hdr_put(b,ROAM_HELLO,0,0); memset(hello,0,ROAM_HELLO_PROOF);
    hello[0]=7; memcpy(hello+20,key.secret,65);
    hello[20]=(uint8_t)(6 | (key.secret[64]&1));
    out=(Capture){0}; roam_relay_sender(r,capture,&out);
    uint64_t verified=roam_relay_stats(r).signature_checks;
    roam_relay_input(r,b,ROAM_HDR+ROAM_HELLO_BASE,&a,1100);
    if (out.n == ROAM_HDR+20) memcpy(hello+ROAM_HELLO_BASE,out.packet+ROAM_HDR,20);
    CHECK(roam_auth_sign(&key,ROAM_VERSION,hello,hello+ROAM_HELLO_BASE,
          hello+ROAM_HELLO_BASE+21,hello+ROAM_HELLO_BASE+20),"alias_sign");
    CHECK(!roam_auth_verify(ROAM_VERSION,hello,hello+ROAM_HELLO_BASE,
          hello+ROAM_HELLO_BASE+21,hello[ROAM_HELLO_BASE+20]),"alias_import_rejected");
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,1101);
    CHECK(!roam_relay_stats(r).clients,"alias_cannot_escape_ban");
    CHECK(roam_relay_stats(r).signature_checks == verified,"alias_rejected_before_crypto");
    roam_auth_clear(&key); roam_relay_free(r); roam_moderation_close(m);
    printf("ALIAS %s: %u checks, %u failures\n",failures?"FAIL":"PASS",checks,failures);
    return failures?1:0;
}
int main(int argc, char **argv) {
    if (argc==4 && !strcmp(argv[1],"--alias")) return alias_test(argv[2],argv[3]);
    if (argc==3 && !strcmp(argv[1],"--load")) {
        RoamAuth a; int ok=roam_auth_no_dumps() && roam_auth_load(&a,argv[2]);
        roam_auth_clear(&a); printf("credential %s error=%d\n",ok?"PASS":"FAIL",ok?0:errno); return !ok;
    }
    if (argc==3 && !strcmp(argv[1],"--fixture")) return fixture(argv[2],0);
    if (argc==3 && !strcmp(argv[1],"--verify")) return fixture(argv[2],1);
    if (argc!=4) return 2;
    CHECK(roam_auth_no_dumps(),"disable_dumps"); struct rlimit limit;
    CHECK(!getrlimit(RLIMIT_CORE,&limit) && !limit.rlim_cur && !limit.rlim_max,"core_limit");
    auth_test(); storage_test(argv[1],argv[2],argv[3]);
    printf("MODERATION %s: %u checks, %u failures\n",failures?"FAIL":"PASS",checks,failures); return failures?1:0;
}
