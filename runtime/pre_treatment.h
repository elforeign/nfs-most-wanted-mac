// Ordered, pre-X360-treatment nodes. Registration/validation never creates Metal
// objects. A node owns any intermediates and encoding; the graph binds nothing.
#pragma once
#import <Metal/Metal.h>
#include "shared_depth.h"

enum {
    POST_SCENE, POST_DEPTH, POST_NORMALS, POST_MINMAX,
    POST_HISTORY_COLOUR, POST_HISTORY_DEPTH,
    POST_AO_MASK, POST_AO_PING, POST_AO_PONG, POST_SMOKE, POST_SMOKE_DEPTH,
    POST_COLOUR_0, POST_COLOUR_1, POST_COLOUR_2, POST_COLOUR_3, POST_RESOURCE_COUNT,
    POST_MAX_NODES=8
};
#define POST_BIT(resource) (1u<<(resource))
#define POST_SPATIAL_INPUTS (POST_BIT(POST_SCENE)|POST_BIT(POST_DEPTH)|POST_BIT(POST_NORMALS)|POST_BIT(POST_MINMAX))
typedef struct {
    id<MTLTexture> depth, history_colour, history_depth;
    const SharedDepthSet *geometry; // borrowed, current frame and local view only
    const X360PostParams *params;
    const X360PostView *view;        // NULL=SP; mirror views never enter this graph
    void *user;
} PreTreatmentFrame;
typedef id<MTLTexture> (*PreTreatmentEncode)(id<MTLTexture> scene,const PreTreatmentFrame *frame);
typedef struct {
    const char *name;
    unsigned id, after;             // after: bitset of node IDs, all must precede
    unsigned inputs, outputs, intermediates;
    unsigned colour_in, colour_out;
    int in_place;                  // RGB-only attachment blend; no scene sampling
    PreTreatmentEncode encode;
} PreTreatmentNode;
typedef struct {
    int count;                     // empty path: one integer test, no frame access
    unsigned external_inputs;
    PreTreatmentNode nodes[POST_MAX_NODES];
} PreTreatmentGraph;

// Validates the declared order, dependencies, single writers and colour chain.
// Neighbour-reading nodes must declare a distinct colour output. History is
// explicitly external and may only be declared when a history provider exists.
int pre_treatment_validate(const PreTreatmentGraph *graph);
int pre_treatment_register(PreTreatmentGraph *graph,const PreTreatmentNode *node);
// Register on the render thread before drawing, never concurrently with run.
// Declared POST_NORMALS / POST_MINMAX inputs request current-view preparation
// before dispatch, independently of AO. The built-in composite's optional AO
// geometry follows its live setting, so AO Off alone requests no geometry.
int render_pre_treatment_register(const PreTreatmentNode *node);
id<MTLTexture> pre_treatment_run(const PreTreatmentGraph *graph,id<MTLTexture> scene,
    const PreTreatmentFrame *frame);
// Scene RGB is linear in its existing SDR/HDR format, alpha is the bloom mask.
// Every node preserves alpha; sampling is bounded to the local player's crop.
// No fusion or history allocation is implicit. Future haze/shafts/heat/TAA nodes
// register in their desired dependency order and own separate targets as needed.
