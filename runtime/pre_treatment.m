#include "pre_treatment.h"

int pre_treatment_validate(const PreTreatmentGraph *graph) {
    if (!graph || graph->count<0 || graph->count>POST_MAX_NODES) return 0;
    const unsigned resources=(1u<<POST_RESOURCE_COUNT)-1;
    if (graph->external_inputs&~resources) return 0;
    unsigned available=graph->external_inputs,ids=0,colour=POST_SCENE,internal=0;
    for (int i=0;i<graph->count;++i) {
        const PreTreatmentNode *n=&graph->nodes[i];
        if (!n->name || !*n->name || !n->encode || n->id>=POST_MAX_NODES ||
            (ids&(1u<<n->id)) || (n->after&~ids) ||
            ((n->inputs|n->outputs|n->intermediates)&~resources) ||
            (n->inputs&~available) || !n->outputs ||
            (n->intermediates&(available|internal|n->outputs)) ||
            n->colour_in!=colour || n->colour_out>=POST_RESOURCE_COUNT ||
            !(n->inputs&POST_BIT(n->colour_in)) || !(n->outputs&POST_BIT(n->colour_out))) return 0;
        if (n->in_place) {
            if (n->colour_out!=colour || n->outputs!=POST_BIT(colour)) return 0;
        } else if (n->colour_out==colour || (n->outputs&(available|internal))) return 0;
        available|=n->outputs;internal|=n->intermediates;ids|=1u<<n->id;colour=n->colour_out;
    }
    return !graph->count || (graph->external_inputs&POST_BIT(POST_SCENE));
}
int pre_treatment_register(PreTreatmentGraph *graph,const PreTreatmentNode *node) {
    if (!graph || !node || graph->count<0 || graph->count>=POST_MAX_NODES) return 0;
    PreTreatmentGraph next=*graph;next.nodes[next.count++]=*node;
    if (!pre_treatment_validate(&next)) return 0;
    *graph=next;return 1;
}
id<MTLTexture> pre_treatment_run(const PreTreatmentGraph *graph,id<MTLTexture> scene,const PreTreatmentFrame *frame) {
    if (!graph->count) return scene;
    for (int i=0;i<graph->count;++i) {
        const PreTreatmentNode *n=&graph->nodes[i];
        id<MTLTexture> result=n->encode(scene,frame);
        // A neighbour read must not alias its render attachment. Encoding and
        // alpha preservation remain the node's contract, checked by its fixture.
        if (!result || (!n->in_place && result==scene)) return nil;
        scene=result;
    }
    return scene;
}
