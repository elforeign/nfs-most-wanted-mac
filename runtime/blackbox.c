#include "blackbox.h"
#include "blackbox_errors.h"
#include "blackbox_render.h"
#include "blackbox_mw_fe.h"
#include <stdatomic.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#define FRAMES 16384u
#define PRESENTS 65536u
#define EVENTS 4096u
#define ERRORS 512u
#define WINDOW 30000000000ull
typedef struct { uint64_t seq, ns, frame; } Header;
typedef BlackboxPass Pass;
typedef struct {
    Header h;
    uint32_t flow, valid;
    uint64_t flow_since;
    BlackboxTiming timing;
    uint32_t deferred, pass_count;
    uint64_t render_serial;
    uint32_t render_valid, attempted, unattributed, overflow, failed;
    unsigned char reserved[20];
    Pass pass[32];
} Frame;
typedef struct { Header h; uint32_t stage, valid; uint64_t values[3]; } Present;
typedef struct { Header h; uint32_t kind, valid; uint64_t values[11]; } Event;
enum { FE_LOAD = 3, FE_PACKAGE, FE_MW, MW_MOVIE, MW_NIS, CARBON_MOVIE, CARBON_NIS, INPUT_STATE, STATE_CONTEXTS };
#define EVENT_WORDS (sizeof(Event) / sizeof(uint64_t))
_Static_assert(sizeof(Frame) == 376, "frame budget");
_Static_assert(sizeof(Present) == 56, "presentation budget");
_Static_assert(sizeof(Event) == 120, "event budget");
_Static_assert(sizeof(BlackboxMWFE) <= sizeof(((Event *)0)->values), "MW FE event payload");
_Static_assert(ATOMIC_LLONG_LOCK_FREE == 2, "recorder requires lock-free atomics");
/* The gate protects every byte of payload, including readers' memcpy. No seqlock
 * copies with data races. A contended producer drops once; it never spins. */
#define SLOT(T) struct { _Atomic uint64_t gate; T value; }
static SLOT(Frame) frames[FRAMES];
static SLOT(Present) presents[PRESENTS];
static SLOT(Event) events[EVENTS], errors[ERRORS];
/* The guest frame thread alone writes this context. Atomic words let a snapshot
 * reject an overlapping write without blocking the writer or racing its payload. */
static struct { _Atomic uint64_t version, words[EVENT_WORDS]; } state_context[STATE_CONTEXTS];
_Static_assert(sizeof *frames == 384 && sizeof *presents == 64 && sizeof *events == 128, "published slot budgets");
_Static_assert(sizeof frames + sizeof presents + sizeof events + sizeof errors + sizeof state_context < 11 * 1024 * 1024, "storage cap");
typedef struct {
    void *slots;
    size_t stride, size, count;
    _Atomic uint64_t head, lost;
} Ring;
static Ring rings[] = {
    {frames, sizeof *frames, sizeof(Frame), FRAMES, 0, 0},
    {presents, sizeof *presents, sizeof(Present), PRESENTS, 0, 0},
    {events, sizeof *events, sizeof(Event), EVENTS, 0, 0},
    {errors, sizeof *errors, sizeof(Event), ERRORS, 0, 0}
};
typedef struct {
    uint64_t anchor, flow_since, frame, generation, captured_at, heads[4], lost[4], unavailable[4];
    uint32_t game, flow;
    Frame frames[FRAMES];
    Present presents[PRESENTS];
    Event events[EVENTS], errors[ERRORS];
    Event state_context[STATE_CONTEXTS];
} Snapshot;
static Snapshot *frozen;
static _Atomic uint64_t menu_generation, snapshot_losses;
static atomic_flag snapshot_gate = ATOMIC_FLAG_INIT;
static _Atomic uint64_t last_ns, last_frame, flow_since;
static _Atomic uint32_t current_flow, game_id;
static _Atomic int enabled_state;
/* Only the guest frame producer uses these. Other producers read atomic context. */
static uint32_t previous_flow;
static int have_flow;
static uint64_t heartbeat;

int blackbox_enabled(void) {
    int state = atomic_load_explicit(&enabled_state, memory_order_acquire);
    if (!state) {
        const char *value = getenv("NFS_BLACKBOX");
        int desired = value && !strcmp(value, "0") ? 1 : 2;
        int expected = 0;
        atomic_compare_exchange_strong_explicit(&enabled_state, &expected, desired,
                                                memory_order_release, memory_order_relaxed);
        state = atomic_load_explicit(&enabled_state, memory_order_acquire);
    }
    return state == 2;
}
uint64_t blackbox_now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}
static int acquire(_Atomic uint64_t *gate) {
    uint64_t expected = 0;
    return atomic_compare_exchange_strong_explicit(gate, &expected, 1,
                                                  memory_order_acquire, memory_order_relaxed);
}
static void release(_Atomic uint64_t *gate) { atomic_store_explicit(gate, 0, memory_order_release); }
static int publish(unsigned which, void *record) {
    Ring *r = &rings[which];
    Header *h = record;
    h->seq = atomic_fetch_add_explicit(&r->head, 1, memory_order_relaxed) + 1;
    unsigned char *slot = (unsigned char *)r->slots + ((h->seq - 1) % r->count) * r->stride;
    _Atomic uint64_t *gate = (void *)slot;
    if (!acquire(gate)) {
        atomic_fetch_add_explicit(&r->lost, 1, memory_order_relaxed);
        return 0;
    }
    Header *old = (void *)(slot + sizeof *gate);
    int ok = old->seq < h->seq;
    if (ok) memcpy(old, record, r->size);
    else atomic_fetch_add_explicit(&r->lost, 1, memory_order_relaxed);
    release(gate);
    return ok;
}
/* Only failures visit this ring. Bounded, nonblocking aggregation retains the
 * first observation and counts repeats; snapshots use the same slot gates. */
void blackbox_record_error(const BlackboxError *error, uint64_t serial) {
    if (!error || !blackbox_enabled()) return;
    static atomic_flag producer = ATOMIC_FLAG_INIT;
    Ring *r = &rings[3];
    if (atomic_flag_test_and_set_explicit(&producer, memory_order_acquire)) {
        atomic_fetch_add_explicit(&r->lost, 1, memory_order_relaxed);
        return;
    }
    uint64_t now = blackbox_now_ns();
    if (!serial) serial = atomic_load_explicit(&last_frame, memory_order_relaxed);
    uint64_t head = atomic_load_explicit(&r->head, memory_order_relaxed);
    for (uint64_t n = 0; n < head && n < ERRORS; ++n) {
        unsigned i = (head - 1 - n) % ERRORS;
        if (!acquire(&errors[i].gate)) continue;
        Event *e = &errors[i].value;
        int same = e->h.seq && e->kind == BB_ERROR &&
            e->values[0] == error->subsystem && e->values[1] == error->operation &&
            e->values[2] == (uint64_t)error->code && e->values[3] == error->identifier &&
            e->values[4] == error->caller;
        if (same) {
            if (now > e->h.ns) e->h.ns = now;
            e->h.frame = serial;
            ++e->values[6];
        }
        release(&errors[i].gate);
        if (same) {
            atomic_flag_clear_explicit(&producer, memory_order_release);
            return;
        }
    }
    Event e = {.h = {0, now, serial}, .kind = BB_ERROR, .valid = 1,
        .values = {error->subsystem, error->operation, (uint64_t)error->code,
                   error->identifier, error->caller, now, 1, serial}};
    publish(3, &e);
    atomic_flag_clear_explicit(&producer, memory_order_release);
}
static void event(uint32_t kind, uint64_t now, uint64_t frame, uint64_t a, uint64_t b) {
    Event e = {.h = {0, now, frame}, .kind = kind, .valid = 1, .values = {a, b}};
    publish(2, &e);
}
/* Retail FE labels only. Unknown labels remain explicit numeric ID zero. */
static const char *const fe_labels[] = {
    "<unknown>",
    "Autosave_Overlay.fng",
    "BUSTED_OVERLAY.fng",
    "Chyron_IG.fng",
    "ControllerUnplugged.fng",
    "Credits.fng",
    "DEMO_PSA.fng",
    "DEMO_SPLASH.fng",
    "DiscError.fng",
    "EA_TRAX.fng",
    "EA_TRAX_JUKEBOX.FNG",
    "Fe_WorldMap_Acquisitions.fng",
    "Fe_WorldMap_DefenceRace.fng",
    "Fe_WorldMap_RandomEncounter.fng",
    "Fe_WorldMap_World_View.fng",
    "FEANYMOVIE.FNG",
    "FeArrayScroller.fng",
    "FeAutosculptBodyKit.fng",
    "FEAutosculptExhaust.fng",
    "FEAutosculptFrontBumper.fng",
    "FEAutosculptHood.fng",
    "FEAutosculptRearBumper.fng",
    "FEAutosculptRoof.fng",
    "FEAutosculptSkirt.fng",
    "FEAutosculptSpoiler.fng",
    "FEAutosculptTop.fng",
    "FEAutosculptWheel.fng",
    "FeBackdrop.fng",
    "FeBlackScreen.fng",
    "FeBonusCards.fng",
    "FeBossDefeated.fng",
    "FEBusted.fng",
    "FeCarClassSelect.fng",
    "FeCarSelect.fng",
    "FeChallengeSeries.fng",
    "FeChyron.fng",
    "FeColourChooser.fng",
    "FeComingSoonM2.fng",
    "FeComingSoonM3.fng",
    "FeControllerConfig.fng",
    "FeCrewCar.fng",
    "FeCrewLogo.fng",
    "FeCrewLogoColourChooser.fng",
    "FeCrewManagement.fng",
    "FeCustomizeAutozone.fng",
    "FeCustomizeMain.fng",
    "FeCustomizeParts.fng",
    "FeCustomizePerformance.fng",
    "FeCustomizeVisuals.fng",
    "FeDialog.fng",
    "FEGameOver.fng",
    "FeGameRoomEditSettings.fng",
    "FeGameRoomPlayerDetails.fng",
    "FeGameWon.fng",
    "FeGarageMain.fng",
    "FeIconScroller.fng",
    "FEJukebox.fng",
    "FeLayerManagement.fng",
    "FeMainMenu.fng",
    "FeMainMenu_Sub.fng",
    "FeMenuScreen.fng",
    "FeMyCarsManager.fng",
    "FeOnlineChatDialog.fng",
    "FeOnlineDisconnect.fng",
    "FeOnlineFeedback.fng",
    "FeOnlineGameRoom.fng",
    "FeOnlineGameType.fng",
    "FeOnlineLeaderboard.fng",
    "FeOnlineMessenger.fng",
    "FeOnlineMessengerChatPopup.fng",
    "FeOnlineMessengerInvitePopup.fng",
    "FeOnlineMessengerUserPopup.fng",
    "FeOnlinePlayerStats.fng",
    "FEOnlineTOS.fng",
    "FeOptionsScreen.fng",
    "FePauseMain.fng",
    "FePauseOptionsMain.fng",
    "FEPCOnlineAccountTaken.fng",
    "FEPCOnlineCDKey.fng",
    "FEPCOnlineCreateAccount.fng",
    "FEPCOnlineForgotAccount.fng",
    "FEPCOnlineLogin.fng",
    "FePerformanceSlider.fng",
    "FePhotoMode.fng",
    "FEPostPursuitRewards.fng",
    "FePostRaceResults.fng",
    "FEPostStages.fng",
    "FePressStart.fng",
    "FeQuickRaceOptions.fng",
    "FeQuickRaceWingman.fng",
    "FeRaceStatistics.fng",
    "FeRewardCard.fng",
    "FeSaveGhostDialog.fng",
    "FeShowcase.fng",
    "FeSmsMailbox.fng",
    "FeSmsMessage.fng",
    "FeStatisticsStatsListing.fng",
    "FESubtitler.fng",
    "FeTextScroller.fng",
    "FeVinylSelection.fng",
    "FeVinylTransform.fng",
    "FeVisualRideHeight.fng",
    "FeVisualWindowTint.fng",
    "FeWidgetMenu.fng",
    "FeWidgetScroller.fng",
    "FeWorldMapQuickList.fng",
    "HUD_Player1.fng",
    "HUD_Player2.fng",
    "HUD_SingleRace.fng",
    "InGameDialog.fng",
    "Keyboard.fng",
    "Keyboard_PS3.fng",
    "LOADING.FNG",
    "loading_boot.fng",
    "Loading_Controller.fng",
    "Loading_Tips.fng",
    "LS_ATTRACTFMV.FNG",
    "LS_EALOGO.FNG",
    "LS_LangSelect.fng",
    "LS_PSA.FNG",
    "LS_SPLASH.FNG",
    "Marketing1.fng",
    "MC_List.fng",
    "MC_MAIN.FNG",
    "Options_PC_Controller.fng",
    "ScreenPrintf.fng",
    "UI_DebugCarCustomize.fng",
    "WorldMapMain.fng",
    "WS_LS_Splash.fng",
    "X360_Pause.fng",
    "FadeScreen.fng",
    "FadeScreenNoLoadingBar.fng",
    "MainMenu.fng",
    "OL_MAIN.FNG",
    "WS_Loading.fng",
    "WS_Loading_Controller.fng",
};
static const char *const mw_stage_labels[] = {
    "<unknown>", "BeginGameFlowLoadTrack", "BeginWorldLoad", "LoadingFrontEnd",
    "EndGameFlowLoadingFrontEnd", "UnloadingFrontEnd", "LoadingRegion",
    "WaitForSimulation", "FinishedGameLoading", "GameFlowWaitForExitDemoResourcesLoaded",
};
uint32_t blackbox_mw_stage_label(const char *name) {
    for (unsigned i = 1; i < sizeof mw_stage_labels / sizeof *mw_stage_labels; ++i)
        if (!strcmp(name, mw_stage_labels[i])) return i;
    return 0;
}
uint32_t blackbox_fe_label(const char *name) {
    for (unsigned i = 1; i < sizeof fe_labels / sizeof *fe_labels; ++i)
        if (!strcasecmp(name, fe_labels[i])) return i;
    return 0;
}
static const char *fe_label(uint64_t id) {
    return fe_labels[id < sizeof fe_labels / sizeof *fe_labels ? id : 0];
}
static void state_event(unsigned key, uint32_t kind, const void *state, size_t size, uint32_t valid,
                        uint64_t frame, uint64_t now) {
    if (!blackbox_enabled()) return;
    Event e = {.h = {0, now, frame}, .kind = kind, .valid = valid};
    memcpy(e.values, state, size);
    publish(2, &e);
    uint64_t words[EVENT_WORDS];
    memcpy(words, &e, sizeof e);
    atomic_store_explicit(&state_context[key].version, e.h.seq * 2 - 1, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    for (unsigned i = 0; i < EVENT_WORDS; ++i)
        atomic_store_explicit(&state_context[key].words[i], words[i], memory_order_relaxed);
    atomic_store_explicit(&state_context[key].version, e.h.seq * 2, memory_order_release);
}
void blackbox_record_input(const BlackboxInput *state, uint64_t frame, uint64_t now) {
    state_event(INPUT_STATE, BB_INPUT, state, sizeof *state, 1, frame, now);
}
static void dump_input_event(FILE *f, const Event *v, uint64_t anchor) {
    BlackboxInput s; memcpy(&s, v->values, sizeof s);
    const char *layout = s.layout == 1 ? "playstation" : s.layout == 2 ? "xbox" : s.layout == 3 ? "pc" : "unobserved";
    fprintf(f, "INPUT source=%s source_changed=%u connected=%u layout=%s changes=%u unchanged_for=%.3fs\n",
            s.source ? "controller" : "keyboard/mouse", !!(s.changes & 1), s.connected,
            layout, s.changes, (double)(anchor - s.source_since) / 1000000000.0);
}
void blackbox_record_fe(const BlackboxFE *state, uint32_t valid, uint64_t frame, uint64_t now) {
    if (state->index < FE_LOAD) state_event(state->index, BB_FE, state, sizeof *state, valid, frame, now);
}
void blackbox_record_load(const BlackboxLoad *state, uint32_t valid, uint64_t frame, uint64_t now) {
    state_event(FE_LOAD, BB_LOAD, state, sizeof *state, valid, frame, now);
}
void blackbox_record_package(uint32_t label, uint32_t address, uint32_t count,
                             uint32_t truncated, uint64_t frame, uint64_t now) {
    uint32_t state[] = {label, address, count, truncated};
    state_event(FE_PACKAGE, BB_PACKAGE, state, sizeof state, 1, frame, now);
}
void blackbox_record_mw_fe(const BlackboxMWFE *state, uint64_t frame, uint64_t now) {
    state_event(FE_MW, BB_MW_FE, state, sizeof *state, 1, frame, now);
}
static void dump_fe_event(FILE *f, const Event *v) {
    if (v->kind == BB_MW_FE) {
        BlackboxMWFE m; memcpy(&m, v->values, sizeof m);
        unsigned stage = m.stage_label < sizeof mw_stage_labels / sizeof *mw_stage_labels ? m.stage_label : 0;
        unsigned fade_limits = BB_MW_FADE_PACKAGE_LIMIT | BB_MW_FADE_OBJECT_LIMIT | BB_MW_FADE_SCRIPT_INVALID;
        const char *fade = !m.engine_ready || (m.limits & fade_limits) ? "unknown" :
                           m.fade_script == BB_MW_FADE_ON_SCRIPT ? "1" : "0";
        fprintf(f, "MW LOAD state=%u loading=%u continuation=%08X stage='%s' stage_ptr=%08X "
                   "loading_package='%s' loading_ptr=%08X seen=%u tips_done=%u "
                   "fade_on=%s fade_script=%08X fade_package=%08X fade_object=%08X limits=%X\n",
                m.flow, m.flow == 4 || m.flow == 5, m.continuation, mw_stage_labels[stage],
                m.stage_name, fe_label(m.loading_label), m.loading_name, m.screen_seen, m.tips_done,
                fade, m.fade_script, m.fade_package, m.fade_object, m.limits);
        fprintf(f, "MW FE manager=%08X flags=%u/%u controller_errors=%08X%08X "
                   "requested='%s' requested_ptr=%08X garage_type=%u boot=%u pause_request=%u\n",
                m.manager, m.manager_0, m.manager_1, m.controller_errors_hi, m.controller_errors_lo,
                fe_label(m.requested_label), m.requested_name, m.garage_type, m.boot, m.pause_request);
        fprintf(f, "MW PACKAGE control='%s' address=%08X hash=%08X count=%u engine_ready=%u truncated=%u signature=%08X\n",
                fe_label(m.control_label), m.control_package, m.control_hash, m.package_count,
                m.engine_ready, !!(m.limits & BB_MW_PACKAGE_LIMIT), m.list_signature);
    } else if (v->kind == BB_FE) {
        BlackboxFE m; memcpy(&m, v->values, sizeof m);
        fprintf(f, "FE %s valid=%u address=%08X vt=%08X state=%d start=%d prev=%d screen='%s' next='%s' pending=%u wait=%d child=%08X\n",
                m.index == 0 ? "root" : m.index == 1 ? "child" : "grand", v->valid,
                m.address, m.vtable, (int)m.state, (int)m.start, (int)m.previous,
                fe_label(m.screen), fe_label(m.next), m.pending, (int)m.wait, m.child);
    } else if (v->kind == BB_LOAD) {
        BlackboxLoad l; memcpy(&l, v->values, sizeof l);
        fprintf(f, "LOAD valid=%u mask=%X loader busy=%u want=%d have=%d+%d garage_ready=%u\n",
                v->valid, l.mask, l.busy, (int)l.want, (int)l.have, (int)l.queued, l.garage_ready);
    } else {
        uint32_t p[4]; memcpy(p, v->values, sizeof p);
        fprintf(f, "PACKAGE control='%s' address=%08X count=%u truncated=%u\n", fe_label(p[0]), p[1], p[2], p[3]);
    }
}
typedef struct { uint32_t edge; BlackboxCinematic state; } CinematicEvent;
_Static_assert(sizeof(CinematicEvent) <= sizeof(((Event *)0)->values), "cinematic event payload");

void blackbox_record_cinematic(BlackboxKind kind, const BlackboxCinematic *state,
                               uint32_t valid, uint64_t frame, uint64_t now) {
    CinematicEvent p = {BB_CIN_STATE, *state};
    state_event(kind == BB_MOVIE ? MW_MOVIE : MW_NIS, kind, &p, sizeof p, valid, frame, now);
}
void blackbox_cinematic_edge(BlackboxKind kind, BlackboxCinematicEdge edge,
                            const BlackboxCinematic *state, uint32_t valid) {
    CinematicEvent p = {edge, *state};
    Event e = {.h = {0, blackbox_now_ns(), atomic_load_explicit(&last_frame, memory_order_relaxed)},
               .kind = kind, .valid = valid};
    memcpy(e.values, &p, sizeof p);
    publish(2, &e);
}
typedef struct { uint32_t edge; BlackboxCarbonCinematic state; } CarbonCinematicEvent;
_Static_assert(sizeof(CarbonCinematicEvent) <= sizeof(((Event *)0)->values), "Carbon cinematic payload");
void blackbox_record_carbon_cinematic(BlackboxKind kind, BlackboxCinematicEdge edge,
                                     const BlackboxCarbonCinematic *state,
                                     uint32_t valid, uint64_t frame, uint64_t now) {
    if (!blackbox_enabled()) return;
    CarbonCinematicEvent p = {edge, *state};
    if (edge == BB_CIN_STATE)
        state_event(kind == BB_CARBON_MOVIE ? CARBON_MOVIE : CARBON_NIS,
                    kind, &p, sizeof p, valid, frame, now);
    else {
        Event e = {.h = {0, now, atomic_load_explicit(&last_frame, memory_order_relaxed)},
                   .kind = kind, .valid = valid};
        memcpy(e.values, &p, sizeof p);
        publish(2, &e);
    }
}
static int cinematic(const Event *v) {
    return v->kind == BB_MOVIE || v->kind == BB_NIS ||
           v->kind == BB_CARBON_MOVIE || v->kind == BB_CARBON_NIS;
}

static unsigned context_key(const Event *v) {
    uint32_t index;
    memcpy(&index, v->values, sizeof index);
    switch (v->kind) {
    case BB_FE: return index;
    case BB_LOAD: return FE_LOAD;
    case BB_PACKAGE: return FE_PACKAGE;
    case BB_MW_FE: return FE_MW;
    case BB_INPUT: return INPUT_STATE;
    case BB_MOVIE: if (index == BB_CIN_STATE) return MW_MOVIE; break;
    case BB_NIS: if (index == BB_CIN_STATE) return MW_NIS; break;
    case BB_CARBON_MOVIE: if (index == BB_CIN_STATE) return CARBON_MOVIE; break;
    case BB_CARBON_NIS: if (index == BB_CIN_STATE) return CARBON_NIS; break;
    }
    return STATE_CONTEXTS;
}
static double seconds(uint64_t later, uint64_t earlier);
static void dump_cinematic_event(FILE *f, const Event *v, uint64_t anchor) {
    static const char *const edges[] = {
        "state", "play", "stop", "destroy", "end", "timeout", "load", "unload"
    };
    if (v->kind == BB_CARBON_MOVIE || v->kind == BB_CARBON_NIS) {
        CarbonCinematicEvent p;
        memcpy(&p, v->values, sizeof p);
        const BlackboxCarbonCinematic *s = &p.state;
        const char *edge = p.edge < sizeof edges / sizeof *edges ? edges[p.edge] : "unknown";
        if (v->kind == BB_CARBON_MOVIE)
            fprintf(f, "CARBON MOVIE edge=%s valid=%u player=%08X class=%08X state=%u next=%u "
                       "playing=%u filename_hash=%08X playback_clock=not_decoded_yet\n",
                    edge, v->valid, s->address, s->class_id, s->state, s->next,
                    s->state >= 3 && s->state <= 5, s->filename_hash);
        else {
            fprintf(f, "CARBON NIS edge=%s valid=%u interface=%08X class=%08X ",
                    edge, v->valid, s->address, s->class_id);
            if (!s->decoded) fputs("state=unavailable layout=not_decoded_yet\n", f);
            else {
                fprintf(f, "state=%u track=%08X clock_valid=%u ", s->state, s->track, s->clock_valid);
                if (s->clock_valid) {
                    float elapsed, length;
                    memcpy(&elapsed, &s->elapsed, sizeof elapsed);
                    memcpy(&length, &s->length, sizeof length);
                    fprintf(f, "elapsed=%.3f length=%.3f playback_unchanged_for=%.3fs\n",
                            elapsed, length, seconds(anchor, s->clock_since));
                } else fputs("playback_unchanged_for=unavailable\n", f);
            }
        }
        return;
    }
    CinematicEvent p;
    memcpy(&p, v->values, sizeof p);
    const BlackboxCinematic *s = &p.state;
    const char *edge = p.edge < sizeof edges / sizeof *edges ? edges[p.edge] : "unknown";
    if (v->kind == BB_MOVIE)
        fprintf(f, "MW MOVIE edge=%s valid=%u player=%08X state=%u next=%u paused=%u filename_hash=%08X\n",
                edge, v->valid, s->address, s->state, s->next, s->paused, s->id);
    else
        fprintf(f, "MW NIS edge=%s valid=%u activity=%08X state=%u scene_hash=%08X scene=%08X\n",
                edge, v->valid, s->address, s->state, s->id, s->scene);
}
/* Optional renderer adapter; the core and guest-only fixtures need no Metal. */
__attribute__((weak)) int blackbox_render_take(BlackboxRenderFrame *out) { (void)out; return 0; }
__attribute__((weak)) void blackbox_render_definitions(FILE *f) { fputs("UNAVAILABLE passes async_present\n", f); }
void blackbox_record_present(uint64_t serial, uint32_t stage, uint32_t status,
                             uint64_t a, uint64_t b, uint64_t c) {
    if (!blackbox_enabled()) return;
    /* Both frame and final completion paths retain their original serial here.
     * MTLCommandBufferStatusError=5 also covers a missing NSError payload. */
    if ((stage == BB_RENDER_GPU || stage == BB_PRESENT_GPU) && (status == 5 || c)) {
        BlackboxError error = {BB_ERR_METAL, stage == BB_RENDER_GPU ? BB_OP_FRAME_COMMAND : BB_OP_FINAL_COMMAND,
                               (int64_t)c, status, 0};
        blackbox_record_error(&error, serial);
    }
    Present p = {.h = {0, blackbox_now_ns(), serial}, .stage = stage,
                 .valid = status, .values = {a, b, c}};
    publish(1, &p);
}
void blackbox_record_frame(uint32_t game, uint64_t frame, uint32_t flow,
                           const BlackboxTiming *timing, uint64_t now) {
    if (!blackbox_enabled()) return;
    atomic_store_explicit(&game_id, game, memory_order_relaxed);
    if (!have_flow || flow != previous_flow) {
        event(BB_FLOW, now, frame, have_flow ? previous_flow : UINT32_MAX, flow);
        atomic_store_explicit(&flow_since, now, memory_order_relaxed);
        previous_flow = flow;
        have_flow = 1;
    }
    if (!heartbeat || now - heartbeat >= 1000000000ull) {
        event(BB_HEARTBEAT, now, frame, flow, atomic_load_explicit(&flow_since, memory_order_relaxed));
        heartbeat = now;
    }
    Frame f = {.h = {0, now, frame}, .flow = flow, .valid = timing != NULL,
               .flow_since = atomic_load_explicit(&flow_since, memory_order_relaxed)};
    if (timing) f.timing = *timing;
    BlackboxRenderFrame render;
    if (blackbox_render_take(&render)) {
        f.render_valid = 1; f.render_serial = render.serial;
        f.attempted = render.attempted; f.deferred = render.deferred; f.failed = render.failed;
        f.unattributed = render.unattributed; f.overflow = render.overflow;
        f.pass_count = render.count;
        memcpy(f.pass, render.pass, sizeof f.pass);
    }
    if (publish(0, &f)) {
        atomic_store_explicit(&current_flow, flow, memory_order_relaxed);
        atomic_store_explicit(&last_frame, frame, memory_order_relaxed);
        atomic_store_explicit(&last_ns, now, memory_order_release);
    }
}
static void *snapshot_records(Snapshot *s, unsigned i) {
    switch (i) {
    case 0: return s->frames;
    case 1: return s->presents;
    case 2: return s->events;
    default: return s->errors;
    }
}
static Snapshot *capture(void) {
    Snapshot *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->anchor = atomic_load_explicit(&last_ns, memory_order_acquire);
    s->frame = atomic_load_explicit(&last_frame, memory_order_relaxed);
    s->flow = atomic_load_explicit(&current_flow, memory_order_relaxed);
    s->flow_since = atomic_load_explicit(&flow_since, memory_order_relaxed);
    s->game = atomic_load_explicit(&game_id, memory_order_relaxed);
    for (unsigned k = 0; k < STATE_CONTEXTS; ++k) {
        uint64_t version = atomic_load_explicit(&state_context[k].version, memory_order_acquire);
        if (!version || (version & 1)) continue;
        uint64_t words[EVENT_WORDS];
        for (unsigned i = 0; i < EVENT_WORDS; ++i)
            words[i] = atomic_load_explicit(&state_context[k].words[i], memory_order_relaxed);
        atomic_thread_fence(memory_order_acquire);
        if (version == atomic_load_explicit(&state_context[k].version, memory_order_relaxed))
            memcpy(&s->state_context[k], words, sizeof(Event));
    }
    for (unsigned k = 0; k < 4; ++k) {
        Ring *r = &rings[k];
        s->heads[k] = atomic_load_explicit(&r->head, memory_order_acquire);
        s->lost[k] = atomic_load_explicit(&r->lost, memory_order_relaxed);
        uint64_t first = s->heads[k] > r->count ? s->heads[k] - r->count : 0;
        for (uint64_t i = first; i < s->heads[k]; ++i) {
            unsigned char *slot = (unsigned char *)r->slots + (i % r->count) * r->stride;
            _Atomic uint64_t *gate = (void *)slot;
            if (!acquire(gate)) { ++s->unavailable[k]; continue; }
            Header *h = (void *)(slot + sizeof *gate);
            if (h->seq == i + 1) memcpy((char *)snapshot_records(s, k) + (i % r->count) * r->size, h, r->size);
            else ++s->unavailable[k];
            release(gate);
        }
    }
    /* Use a single safely copied frame for observed-state context, never a mix
     * of atomic fields from two frames during a concurrent watchdog snapshot. */
    Frame *latest = NULL;
    for (unsigned i = 0; i < FRAMES; ++i) {
        Frame *v = &s->frames[i];
        if (v->h.seq && v->h.ns <= s->anchor && (!latest || v->h.seq > latest->h.seq)) latest = v;
    }
    if (latest) {
        s->frame = latest->h.frame;
        s->flow = latest->flow;
        s->flow_since = latest->flow_since;
    }
    return s;
}
void blackbox_menu_at(int open, uint64_t now) {
    if (!blackbox_enabled()) return;
    event(BB_MENU, now, atomic_load_explicit(&last_frame, memory_order_relaxed), open != 0, 0);
    uint64_t generation = atomic_fetch_add_explicit(&menu_generation, 1, memory_order_acq_rel) + 1;
    if (atomic_flag_test_and_set_explicit(&snapshot_gate, memory_order_acquire)) {
        atomic_fetch_add_explicit(&snapshot_losses, 1, memory_order_relaxed);
        return;
    }
    free(frozen);
    frozen = open ? capture() : NULL;
    if (frozen) {
        frozen->generation = generation;
        frozen->captured_at = now;
    }
    else if (open) atomic_fetch_add_explicit(&snapshot_losses, 1, memory_order_relaxed);
    atomic_flag_clear_explicit(&snapshot_gate, memory_order_release);
}
void blackbox_menu(int open) { blackbox_menu_at(open, blackbox_now_ns()); }
static double seconds(uint64_t later, uint64_t earlier) {
    return later >= earlier ? (later - earlier) / 1e9 : -(double)(earlier - later) / 1e9;
}
static void dump(FILE *f, Snapshot *s, BlackboxTrigger trigger, uint64_t now, int saved) {
    static const char *const triggers[] = {"F10", "hang", "stall", "crash"};
    uint64_t cutoff = s->anchor > WINDOW ? s->anchor - WINDOW : 0;
    fprintf(f, "blackbox v1 game=%s window=30.000s trigger=%s snapshot=%s snapshot_age=%.3fs no_frame=%.3fs\n",
            s->game == BLACKBOX_CARBON ? "carbon" : s->game == BLACKBOX_MW ? "mw" : "unknown",
            triggers[trigger <= BB_CRASH ? trigger : BB_CRASH], saved ? "menu" : "live",
            seconds(now, s->anchor), seconds(now, atomic_load_explicit(&last_ns, memory_order_acquire)));
    fprintf(f, "MENU_SNAPSHOT losses=%llu\n", (unsigned long long)atomic_load_explicit(&snapshot_losses, memory_order_relaxed));
    fprintf(f, "OBSERVED f%llu flow=%u unchanged_for=%.3fs\n", (unsigned long long)s->frame, s->flow,
            seconds(s->anchor, s->flow_since));
    const char *fe = s->game == BLACKBOX_MW ? " fe package load fade movie nis" :
                     s->game == BLACKBOX_CARBON ? " fe package load movie nis" : "";
    fprintf(f, "AVAILABLE flow frame_timing attempted_draws ticks menu input errors%s%s\n",
            fe, s->game == BLACKBOX_MW || s->game == BLACKBOX_CARBON ? "" : "; UNAVAILABLE movie nis");
    blackbox_render_definitions(f);
    /* Summarize only copied observations for the latest encoded serial. An
     * absent callback is missing-or-pending, never evidence of a blank image. */
    const Frame *render_latest = NULL;
    for (unsigned i = 0; i < FRAMES; ++i) {
        const Frame *v = &s->frames[i];
        if (v->h.seq && v->render_valid && (!render_latest || v->h.seq > render_latest->h.seq)) render_latest = v;
    }
    if (render_latest) {
        unsigned stages = 0, dropped = 0;
        uint64_t shown_seq = 0;
        for (unsigned i = 0; i < PRESENTS; ++i) {
            const Present *v = &s->presents[i];
            if (!v->h.seq || v->h.frame != render_latest->render_serial ||
                v->h.ns < cutoff || v->h.ns > (saved ? s->captured_at : now)) continue;
            if (v->stage < 32) stages |= 1u << v->stage;
            if (v->stage == BB_PRESENT_SHOWN && v->h.seq > shown_seq) { shown_seq = v->h.seq; dropped = v->valid; }
        }
        const char *shown = stages & (1u << BB_PRESENT_SHOWN) ? (dropped ? "dropped" : "observed") :
            stages & (1u << BB_PRESENT_MISSING) ? "no_drawable" :
            stages & (1u << BB_PRESENT_SUPPRESSED) ? "not_scheduled" : "missing_or_pending";
        fprintf(f, "LATEST_PRESENT serial=%llu submit=%s frame_gpu=%s final_gpu=%s shown=%s\n",
                (unsigned long long)render_latest->render_serial,
                stages & (1u << BB_RENDER_SUBMIT) ? "observed" : "missing_or_pending",
                stages & (1u << BB_RENDER_GPU) ? "observed" : "missing_or_pending",
                stages & (1u << BB_PRESENT_GPU) ? "observed" : "missing_or_pending", shown);
    }
    uint64_t latest_state[STATE_CONTEXTS] = {0};
    for (unsigned i = 0; i < EVENTS; ++i) {
        Event *v = &s->events[i];
        if (!v->h.seq || v->h.ns > s->anchor) continue;
        unsigned key = context_key(v);
        if (key < STATE_CONTEXTS && v->h.seq > latest_state[key]) latest_state[key] = v->h.seq;
    }
    for (unsigned i = 0; i < STATE_CONTEXTS; ++i) {
        const Event *v = &s->state_context[i];
        if (v->h.seq <= latest_state[i] || v->h.ns > s->anchor) continue;
        fprintf(f, "% .6f f%llu %s ", seconds(v->h.ns, s->anchor), (unsigned long long)v->h.frame,
                cinematic(v) ? "CINEMATIC_CONTEXT" : v->kind == BB_INPUT ? "INPUT_CONTEXT" : "FE_CONTEXT");
        if (cinematic(v)) dump_cinematic_event(f, v, s->anchor);
        else if (v->kind == BB_INPUT) dump_input_event(f, v, s->anchor);
        else dump_fe_event(f, v);
        latest_state[i] = 0;
    }
    const char *const names[] = {"frames", "present", "events", "errors"};
    for (unsigned k = 0; k < 4; ++k) {
        Ring *r = &rings[k];
        uint64_t first = s->heads[k] > r->count ? s->heads[k] - r->count : 0;
        uint64_t oldest = 0, kept = 0;
        for (uint64_t i = first; i < s->heads[k]; ++i) {
            Header *h = (void *)((char *)snapshot_records(s, k) + (i % r->count) * r->size);
            const Event *e = k == 2 ? (const Event *)h : NULL;
            /* A hook can enter a blocking guest call after the last frame. */
            int edge = e && cinematic(e) && context_key(e) == STATE_CONTEXTS;
            if (edge && h->ns > (saved ? s->captured_at : now)) continue;
            /* Completion/presentation may arrive after the last guest sample.
             * Freeze at capture time for F10, otherwise at export time. */
            int present = k == 1 && h->ns <= (saved ? s->captured_at : now);
            /* A repeat can update last_ns after the trigger but before this
             * snapshot copies its slot. Retain the known pre-trigger first
             * observation; mark the interval count unavailable below. */
            int failure = k == 3 && ((Event *)h)->values[5] <= (saved ? s->captured_at : now);
            int after_frame = edge || present || failure || (e && e->kind == BB_MENU);
            int context = 0;
            if (e) for (unsigned j = 0; j < STATE_CONTEXTS; ++j)
                if (h->seq && h->seq == latest_state[j]) context = 1;
            if (!h->seq || (h->ns < cutoff && !context) || (h->ns > s->anchor && !after_frame)) continue;
            if (h->ns <= s->anchor && (!oldest || h->ns < oldest)) oldest = h->ns;
            ++kept;
            fprintf(f, "% .6f f%llu ", seconds(h->ns, s->anchor), (unsigned long long)h->frame);
            if (k == 0) {
                Frame *v = (void *)h;
                fprintf(f, "FRAME flow=%u valid=%u dt_ms=%.3f busy_ms=%.3f wait_ms=%.3f draws=%u ticks=%u\n",
                        v->flow, v->valid, v->timing.frame_ms, v->timing.busy_ms, v->timing.wait_ms, v->timing.draws, v->timing.ticks);
                if (v->render_valid) {
                    fprintf(f, "RENDER serial=%llu attempted_d3d_draws=%u deferred=%u unattributed=%u pass_overflow_draws=%u passes=%u",
                            (unsigned long long)v->render_serial, v->attempted, v->deferred,
                            v->unattributed, v->overflow, v->pass_count);
                    for (unsigned j = 0; j < v->pass_count; ++j)
                        fprintf(f, " %u:%u", v->pass[j].id, v->pass[j].draws);
                    fprintf(f, " failed=%u\n", v->failed);
                } else fprintf(f, "RENDER unavailable\n");
            } else if (k == 1) {
                Present *v = (void *)h;
                const char *stage = v->stage == BB_RENDER_SUBMIT ? "submit" : v->stage == BB_RENDER_GPU ? "frame_gpu" :
                    v->stage == BB_PRESENT_GPU ? "final_gpu" : v->stage == BB_PRESENT_SHOWN ? "shown" :
                    v->stage == BB_PRESENT_MISSING ? "missing" : v->stage == BB_HOST_ENCODER ? "host_encoder" : v->stage == BB_PRESENT_SUPPRESSED ? "not_scheduled" : v->stage == BB_PRESENT_TICKET_LOST ? "ticket_lost" : "unknown";
                if (v->stage == BB_RENDER_GPU || v->stage == BB_PRESENT_GPU)
                    fprintf(f, "PRESENT stage=%s status=%u gpu_start_ns=%llu gpu_end_ns=%llu error=%lld\n", stage, v->valid,
                            (unsigned long long)v->values[0], (unsigned long long)v->values[1], (long long)v->values[2]);
                else if (v->stage == BB_PRESENT_SHOWN)
                    fprintf(f, "PRESENT stage=%s status=%u shown_ns=%llu shown=%s\n", stage, v->valid,
                            (unsigned long long)v->values[2], v->valid ? "dropped" : "yes");
                else fprintf(f, "PRESENT stage=%s status=%u a=%llu b=%llu c=%llu\n", stage, v->valid,
                             (unsigned long long)v->values[0], (unsigned long long)v->values[1], (unsigned long long)v->values[2]);
            } else if (k == 2) {
                Event *v = (void *)h;
                if (v->kind == BB_FLOW) fprintf(f, "FLOW %u->%u\n", (unsigned)v->values[0], (unsigned)v->values[1]);
                else if (v->kind == BB_MENU) fprintf(f, "F10 %s\n", v->values[0] ? "open" : "close");
                else if (v->kind == BB_HEARTBEAT) fprintf(f, "STATE flow=%u unchanged_for=%.3fs\n", (unsigned)v->values[0], seconds(h->ns, v->values[1]));
                else if (v->kind == BB_FE || v->kind == BB_LOAD || v->kind == BB_PACKAGE || v->kind == BB_MW_FE)
                    dump_fe_event(f, v);
                else if (cinematic(v)) dump_cinematic_event(f, v, context ? s->anchor : h->ns);
                else if (v->kind == BB_INPUT) dump_input_event(f, v, s->anchor);
                else fprintf(f, "EVENT kind=%u valid=%u\n", v->kind, v->valid);
            } else {
                Event *v = (void *)h;
                static const char *const classes[] = {"unknown", "shader", "d3d", "metal", "asset", "assert", "fatal", "com"};
                static const char *const operations[] = {"unknown", "translate", "library", "pipeline", "hresult", "open", "read", "breakpoint", "fatal", "frame_command", "final_command", "function"};
                unsigned c = v->values[0], op = v->values[1];
                fprintf(f, "ERROR class=%s operation=%s code=%lld id=%016llX caller=%08X first_ns=%llu last_ns=%llu ",
                    c < sizeof classes / sizeof *classes ? classes[c] : "unknown",
                    op < sizeof operations / sizeof *operations ? operations[op] : "unknown",
                    (long long)v->values[2], (unsigned long long)v->values[3], (unsigned)v->values[4],
                    (unsigned long long)v->values[5], (unsigned long long)h->ns);
                if (h->ns > (saved ? s->captured_at : now))
                    fprintf(f, "repeats=unavailable observed_repeats=%llu capture_overlap=1 ", (unsigned long long)v->values[6]);
                else fprintf(f, "repeats=%llu ", (unsigned long long)v->values[6]);
                fprintf(f, "first_frame=%llu\n", (unsigned long long)v->values[7]);
            }
        }
        fprintf(f, "COVERAGE %s records=%llu retained=%.3fs overwritten=%llu lost=%llu unavailable=%llu capacity_short=%u\n",
                names[k], (unsigned long long)kept, oldest ? seconds(s->anchor, oldest) : 0,
                (unsigned long long)first, (unsigned long long)s->lost[k], (unsigned long long)s->unavailable[k],
                first && oldest > cutoff + 100000000ull);
    }
}
void blackbox_export_at(FILE *f, BlackboxTrigger trigger, uint64_t now) {
    if (!f) return;
    if (!blackbox_enabled()) { fputs("blackbox v1 disabled NFS_BLACKBOX=0\n", f); return; }
    if (trigger == BB_F10 && !atomic_flag_test_and_set_explicit(&snapshot_gate, memory_order_acquire)) {
        if (frozen && frozen->generation == atomic_load_explicit(&menu_generation, memory_order_acquire)) {
            dump(f, frozen, trigger, now, 1);
            atomic_flag_clear_explicit(&snapshot_gate, memory_order_release);
            return;
        }
        atomic_flag_clear_explicit(&snapshot_gate, memory_order_release);
    }
    Snapshot *s = capture();
    if (!s) { fputs("blackbox v1 snapshot_unavailable=allocation\n", f); return; }
    if (trigger == BB_F10) fputs("blackbox menu_snapshot_unavailable=1\n", f);
    dump(f, s, trigger, now, 0);
    free(s);
}
void blackbox_export(FILE *f, BlackboxTrigger trigger) { blackbox_export_at(f, trigger, blackbox_now_ns()); }
void blackbox_summary(FILE *f, BlackboxTrigger trigger) {
    if (trigger == BB_F10 && !atomic_flag_test_and_set_explicit(&snapshot_gate, memory_order_acquire)) {
        if (frozen && frozen->generation == atomic_load_explicit(&menu_generation, memory_order_acquire)) {
            fprintf(f, "blackbox: menu snapshot; observed flow=%u frame=%llu unchanged_for=%.3fs; history in blackbox.txt\n",
                    frozen->flow, (unsigned long long)frozen->frame, seconds(frozen->anchor, frozen->flow_since));
            atomic_flag_clear_explicit(&snapshot_gate, memory_order_release);
            return;
        }
        atomic_flag_clear_explicit(&snapshot_gate, memory_order_release);
    }
    fprintf(f, "blackbox: %s; observed flow=%u frame=%llu; 30 s history in blackbox.txt (unavailable feeds listed there)\n",
            blackbox_enabled() ? "on" : "off", atomic_load_explicit(&current_flow, memory_order_relaxed),
            (unsigned long long)atomic_load_explicit(&last_frame, memory_order_relaxed));
}
