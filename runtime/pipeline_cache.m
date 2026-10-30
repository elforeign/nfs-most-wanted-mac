// A descriptor catalogue for known scene passes, replayed after shader readiness.
#include "pipeline_cache.h"
#include "d3d9_internal.h"
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

typedef struct { uint32_t format, offset, buffer; } PipelineAttribute;
typedef struct { uint32_t stride, step, rate; } PipelineLayout;
typedef struct {
    uint64_t vertex, fragment;
    uint32_t samples, a2c, color, depth, stencil;
    uint32_t blend, mask, srcRGB, dstRGB, opRGB, srcAlpha, dstAlpha, opAlpha;
    PipelineAttribute attributes[31];
    PipelineLayout layouts[31];
    uint32_t reserved; // explicit tail: descriptor identity has no implicit padding
} PipelineRecipe;
_Static_assert(sizeof(PipelineRecipe)==816, "recipe identity must have no implicit padding");

// Union of one race and one front-end to free-roam entry: shader identities and API state only.
// samples=0 follows the configured scene AA; samples=1 identifies a single-sample pass.
static const PipelineRecipe recipes[] = {
    {.vertex=0x6291D8735225B783ull,.fragment=0xE7F7F28253557CFCull,.samples=0,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=15,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0x6291D8735225B783ull,.fragment=0xE7F7F28253557CFCull,.samples=0,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=15,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0x6291D8735225B783ull,.fragment=0xE7F7F28253557CFCull,.samples=0,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=15,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0x8B3188FD11BFAA3Aull,.fragment=0xDB3FFC804BD92681ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x181B67F3660CFA84ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x179D279C6B67E6CAull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x3DB3EADEE293CEC0ull,.fragment=0xAC2DFFD88DB7282Eull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=8,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},},.layouts={[0]={12,1,1},}}, // draw
    {.vertex=0x8B3188FD11BFAA3Aull,.fragment=0xDB3FFC804BD92681ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={12,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x181B67F3660CFA84ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={12,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x179D279C6B67E6CAull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={12,1,1},}}, // draw
    {.vertex=0x8B3188FD11BFAA3Aull,.fragment=0xDB3FFC804BD92681ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=1,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={12,1,1},}}, // draw
    {.vertex=0x10EC14D8F769FFF2ull,.fragment=0x6926EBA765BD123Aull,.samples=1,.a2c=0,.color=80,.depth=0,.stencil=0,.blend=0,.mask=15,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,16,0},[2]={29,24,0},[3]={29,32,0},[4]={29,40,0},},.layouts={[0]={48,1,1},}}, // draw
    {.vertex=0x10EC14D8F769FFF2ull,.fragment=0xECA21B0A3ABE0E50ull,.samples=1,.a2c=0,.color=80,.depth=0,.stencil=0,.blend=0,.mask=15,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,16,0},[2]={29,24,0},[3]={29,32,0},[4]={29,40,0},},.layouts={[0]={48,1,1},}}, // draw
    {.vertex=0x10EC14D8F769FFF2ull,.fragment=0x1216F54625AD74A3ull,.samples=1,.a2c=0,.color=80,.depth=0,.stencil=0,.blend=0,.mask=15,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,16,0},[2]={29,24,0},[3]={29,32,0},[4]={29,40,0},},.layouts={[0]={48,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x9095C4EB915DDCF8ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0xA02E2C799617F2D4ull,.fragment=0x82B69DB5625202B2ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x276CD94301517EABull,.fragment=0x5EB25C30C21555B5ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0xA02E2C799617F2D4ull,.fragment=0x82B69DB5625202B2ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=0,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=0,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x276CD94301517EABull,.fragment=0x5EB25C30C21555B5ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=0,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0xFC40D79EA2195B93ull,.fragment=0xAC200C82792EE467ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=8,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xFC40D79EA2195B93ull,.fragment=0xAC200C82792EE467ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=8,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x8B3188FD11BFAA3Aull,.fragment=0xDB3FFC804BD92681ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x181B67F3660CFA84ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x181B67F3660CFA84ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // ao
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x179D279C6B67E6CAull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x5EC4C82A53FE1A4Full,.fragment=0x179D279C6B67E6CAull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // ao
    {.vertex=0x97049AED6CCB4FA7ull,.fragment=0xA8117D233D987B61ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xD23D672E82035832ull,.fragment=0x12C36106BE70F51Cull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0xCD4F73B949AEBF49ull,.fragment=0xE35BED5E66F8B15Cull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0x43565BBBA8937634ull,.fragment=0xC5FEB0D6DD064F00ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x41E307FC0865E15Aull,.fragment=0xDC737DE47EE47CE6ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0xA02E2C799617F2D4ull,.fragment=0x82B69DB5625202B2ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x8FF57350C91BB1E1ull,.fragment=0xDFBD40529DC4C45Bull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // material
    {.vertex=0x8FF57350C91BB1E1ull,.fragment=0xDFBD40529DC4C45Bull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // material
    {.vertex=0xA02E2C799617F2D4ull,.fragment=0x82B69DB5625202B2ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // ao
    {.vertex=0x8FF57350C91BB1E1ull,.fragment=0xDFBD40529DC4C45Bull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=14,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // ao_material
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2603CBF617A84215ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x17CBDEEB355864FEull,.fragment=0xADE81D735C0E07Full,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0xE7C433EDE57DF193ull,.fragment=0xA8E607594F21B395ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2831A1E4BCEDFBA7ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x17CBDEEB355864FEull,.fragment=0x6C2BFCC28133F85Eull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0xE7C433EDE57DF193ull,.fragment=0x63945F44A614DA61ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x9095C4EB915DDCF8ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={24,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x9095C4EB915DDCF8ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={24,1,1},}}, // ao
    {.vertex=0xA3BEABE96BAC71A7ull,.fragment=0x9F5F744034EFE50Dull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xF8BA847D12151E05ull,.fragment=0xDC87CFDFD800390Dull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xF8BA847D12151E05ull,.fragment=0xDC87CFDFD800390Dull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // ao
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x9095C4EB915DDCF8ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x9095C4EB915DDCF8ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // ao
    {.vertex=0x8B3188FD11BFAA3Aull,.fragment=0xDB3FFC804BD92681ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=1,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={29,28,0},[2]={29,36,0},},.layouts={[0]={44,1,1},}}, // draw
    {.vertex=0x4F7FA17E9A753F92ull,.fragment=0x831FF309826D8129ull,.samples=1,.a2c=0,.color=80,.depth=0,.stencil=0,.blend=1,.mask=15,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=1,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={24,1,1},}}, // draw
    {.vertex=0x4F7FA17E9A753F92ull,.fragment=0xC9C9F2C04A3D9198ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={24,1,1},}}, // ao
    {.vertex=0x6291D8735225B783ull,.fragment=0xE7F7F28253557CFCull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=0,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0x6291D8735225B783ull,.fragment=0xE7F7F28253557CFCull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=15,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0x6291D8735225B783ull,.fragment=0xE7F7F28253557CFCull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // ao
    {.vertex=0x43565BBBA8937634ull,.fragment=0xC5FEB0D6DD064F00ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x41E307FC0865E15Aull,.fragment=0xDC737DE47EE47CE6ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0xF8BA847D12151E05ull,.fragment=0xDC87CFDFD800390Dull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xF8BA847D12151E05ull,.fragment=0xDC87CFDFD800390Dull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x4F7FA17E9A753F92ull,.fragment=0x831FF309826D8129ull,.samples=1,.a2c=0,.color=80,.depth=0,.stencil=0,.blend=1,.mask=15,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=0,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={24,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x9095C4EB915DDCF8ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xAD48AC75000CD218ull,.fragment=0xC2379A4BFC53380Cull,.samples=0,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},[3]={29,24,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0xAD48AC75000CD218ull,.fragment=0xC2379A4BFC53380Cull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},[3]={29,24,0},},.layouts={[0]={32,1,1},}}, // ao
    {.vertex=0x97049AED6CCB4FA7ull,.fragment=0xA8117D233D987B61ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x97049AED6CCB4FA7ull,.fragment=0xA8117D233D987B61ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // ao
    {.vertex=0x97049AED6CCB4FA7ull,.fragment=0xA8117D233D987B61ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xC73474161E8B19C6ull,.fragment=0x2C1F5EBBE35EE526ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xF72413A29F02BF0Cull,.fragment=0x97F8793DFF33706Cull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0xC73474161E8B19C6ull,.fragment=0x2C1F5EBBE35EE526ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xF72413A29F02BF0Cull,.fragment=0x97F8793DFF33706Cull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0xF690369B26DB6581ull,.fragment=0xE0993A6CB2DF28B3ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x43AC44EF29367C2Bull,.fragment=0x3518435C700AC37Dull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0xF690369B26DB6581ull,.fragment=0xE0993A6CB2DF28B3ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x43AC44EF29367C2Bull,.fragment=0x3518435C700AC37Dull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0x4F7FA17E9A753F92ull,.fragment=0x831FF309826D8129ull,.samples=1,.a2c=0,.color=80,.depth=0,.stencil=0,.blend=1,.mask=15,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=1,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // draw
    {.vertex=0x4F7FA17E9A753F92ull,.fragment=0xC9C9F2C04A3D9198ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={32,1,1},}}, // ao
    {.vertex=0xCD4F73B949AEBF49ull,.fragment=0xE35BED5E66F8B15Cull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[4]={31,44,0},},.layouts={[0]={60,1,1},}}, // draw
    {.vertex=0x4F7FA17E9A753F92ull,.fragment=0xD1D251012267C742ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,12,0},[2]={29,16,0},},.layouts={[0]={24,1,1},}}, // draw
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2831A1E4BCEDFBA7ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xF690369B26DB6581ull,.fragment=0xE37C3C0C643B3765ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x43AC44EF29367C2Bull,.fragment=0xD516279E56D3CE4Full,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},[29]={28,0,29},},.layouts={[0]={36,1,1},[29]={4,1,1},}}, // material
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=0,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2831A1E4BCEDFBA7ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2831A1E4BCEDFBA7ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // ao
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=1,.mask=0,.srcRGB=4,.dstRGB=5,.opRGB=0,.srcAlpha=4,.dstAlpha=5,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0xA3BEABE96BAC71A7ull,.fragment=0x1CA10513B3B3ABB7ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=0,.mask=14,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2603CBF617A84215ull,.samples=0,.a2c=0,.color=115,.depth=260,.stencil=260,.blend=1,.mask=14,.srcRGB=4,.dstRGB=1,.opRGB=0,.srcAlpha=4,.dstAlpha=1,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
    {.vertex=0x178AC465012A8B89ull,.fragment=0x2603CBF617A84215ull,.samples=0,.a2c=0,.color=10,.depth=250,.stencil=0,.blend=1,.mask=15,.srcRGB=0,.dstRGB=0,.opRGB=0,.srcAlpha=0,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={30,12,0},[2]={42,24,0},[3]={29,28,0},},.layouts={[0]={36,1,1},}}, // ao
    {.vertex=0xB04ADA7FB6F80A4Full,.fragment=0x79095094E9DB4CD9ull,.samples=1,.a2c=0,.color=80,.depth=260,.stencil=260,.blend=0,.mask=8,.srcRGB=1,.dstRGB=0,.opRGB=0,.srcAlpha=1,.dstAlpha=0,.opAlpha=0,.attributes={[0]={30,0,0},[1]={42,24,0},[2]={29,28,0},},.layouts={[0]={36,1,1},}}, // draw
};
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static NSMutableDictionary<NSNumber *, id<MTLFunction>> *functions;
static NSMutableDictionary<NSData *, id<MTLRenderPipelineState>> *states;
static int loading = 1;
static pthread_t frame_thread;
static _Thread_local int frame_scope;
static uint64_t frame_builds, frame_ns, known_builds, last_builds, last_known, last_tick;
static NSUInteger scene_sample_count=1;
static PipelineRecipe resolve_recipe(PipelineRecipe r) {
    if (!r.samples) r.samples=(uint32_t)scene_sample_count;
    return r;
}
static int enabled(void) { const char *s=getenv("NFS_PSO_PREWARM"); return s && atoi(s); }
void pipeline_frame_scope(int active) { frame_scope=active; }
static int on_frame_path(void) { return diag_on && !loading && (frame_scope || pthread_equal(frame_thread,pthread_self())); }

void pipeline_register_shader(uint64_t key, id<MTLFunction> function) {
    if (!function) return;
    pthread_mutex_lock(&lock);
    if (!functions) functions=[NSMutableDictionary new];
    functions[@(key)]=function;
    pthread_mutex_unlock(&lock);
}
static PipelineRecipe describe(MTLRenderPipelineDescriptor *d, uint64_t v, uint64_t f) {
    PipelineRecipe r={0};
    r.vertex=v; r.fragment=f; r.samples=(uint32_t)d.rasterSampleCount; r.a2c=d.alphaToCoverageEnabled;
    r.color=(uint32_t)d.colorAttachments[0].pixelFormat; r.depth=(uint32_t)d.depthAttachmentPixelFormat;
    r.stencil=(uint32_t)d.stencilAttachmentPixelFormat;
    MTLRenderPipelineColorAttachmentDescriptor *c=d.colorAttachments[0];
    r.blend=c.blendingEnabled; r.mask=(uint32_t)c.writeMask;
    r.srcRGB=(uint32_t)c.sourceRGBBlendFactor; r.dstRGB=(uint32_t)c.destinationRGBBlendFactor; r.opRGB=(uint32_t)c.rgbBlendOperation;
    r.srcAlpha=(uint32_t)c.sourceAlphaBlendFactor; r.dstAlpha=(uint32_t)c.destinationAlphaBlendFactor; r.opAlpha=(uint32_t)c.alphaBlendOperation;
    for (int i=0;i<31;++i) {
        MTLVertexAttributeDescriptor *a=d.vertexDescriptor.attributes[i];
        if (a.format) r.attributes[i]=(PipelineAttribute){(uint32_t)a.format,(uint32_t)a.offset,(uint32_t)a.bufferIndex};
        MTLVertexBufferLayoutDescriptor *l=d.vertexDescriptor.layouts[i];
        if (l.stride) r.layouts[i]=(PipelineLayout){(uint32_t)l.stride,(uint32_t)l.stepFunction,(uint32_t)l.stepRate};
    }
    return r;
}
static MTLRenderPipelineDescriptor *descriptor(const PipelineRecipe *r) {
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=functions[@(r->vertex)]; d.fragmentFunction=functions[@(r->fragment)];
    if (!d.vertexFunction || !d.fragmentFunction) return nil;
    d.rasterSampleCount=r->samples; d.alphaToCoverageEnabled=r->a2c;
    d.depthAttachmentPixelFormat=r->depth; d.stencilAttachmentPixelFormat=r->stencil;
    MTLRenderPipelineColorAttachmentDescriptor *c=d.colorAttachments[0];
    c.pixelFormat=r->color; c.blendingEnabled=r->blend; c.writeMask=r->mask;
    c.sourceRGBBlendFactor=r->srcRGB; c.destinationRGBBlendFactor=r->dstRGB; c.rgbBlendOperation=r->opRGB;
    c.sourceAlphaBlendFactor=r->srcAlpha; c.destinationAlphaBlendFactor=r->dstAlpha; c.alphaBlendOperation=r->opAlpha;
    MTLVertexDescriptor *vd=[MTLVertexDescriptor vertexDescriptor];
    for (int i=0;i<31;++i) {
        if (r->attributes[i].format) {
            vd.attributes[i].format=r->attributes[i].format; vd.attributes[i].offset=r->attributes[i].offset;
            vd.attributes[i].bufferIndex=r->attributes[i].buffer;
        }
        if (r->layouts[i].stride) {
            vd.layouts[i].stride=r->layouts[i].stride; vd.layouts[i].stepFunction=r->layouts[i].step; vd.layouts[i].stepRate=r->layouts[i].rate;
        }
    }
    d.vertexDescriptor=vd;
    return d;
}
static void record(const PipelineRecipe *r, const char *pass) {
    char text[4096]; int n=snprintf(text,sizeof text,
        "{.vertex=0x%llXull,.fragment=0x%llXull,.samples=%u,.a2c=%u,.color=%u,.depth=%u,.stencil=%u,.blend=%u,.mask=%u,.srcRGB=%u,.dstRGB=%u,.opRGB=%u,.srcAlpha=%u,.dstAlpha=%u,.opAlpha=%u,.attributes={",
        (unsigned long long)r->vertex,(unsigned long long)r->fragment,r->samples,r->a2c,r->color,r->depth,r->stencil,r->blend,r->mask,r->srcRGB,r->dstRGB,r->opRGB,r->srcAlpha,r->dstAlpha,r->opAlpha);
    for (int i=0;i<31;++i) if(r->attributes[i].format) n+=snprintf(text+n,sizeof text-n,"[%d]={%u,%u,%u},",i,r->attributes[i].format,r->attributes[i].offset,r->attributes[i].buffer);
    n+=snprintf(text+n,sizeof text-n,"},.layouts={");
    for (int i=0;i<31;++i) if(r->layouts[i].stride) n+=snprintf(text+n,sizeof text-n,"[%d]={%u,%u,%u},",i,r->layouts[i].stride,r->layouts[i].step,r->layouts[i].rate);
    snprintf(text+n,sizeof text-n,"}},");
    diag_event("pipeline_recipe: %s %s",pass,text);
}
id<MTLRenderPipelineState> pipeline_build_device(id<MTLDevice> device, MTLRenderPipelineDescriptor *d, const char *pass, NSError **error) {
    int measure=on_frame_path();
    if (measure) {
        __atomic_add_fetch(&frame_builds,1,__ATOMIC_RELAXED);
        if (!strcmp(pass,"edr") || !strcmp(pass,"ao_depth") || !strcmp(pass,"x_contact_smoke_multiply") || !strcmp(pass,"x_pso")) {
            __atomic_add_fetch(&known_builds,1,__ATOMIC_RELAXED);
            diag_event("pipeline_known_miss: pass=%s",pass);
        }
        diag_event("pipeline_build: pass=%s",pass);
    }
    uint64_t start=measure ? host_ticks() : 0;
    id<MTLRenderPipelineState> p=[device newRenderPipelineStateWithDescriptor:d error:error];
    if(measure)__atomic_add_fetch(&frame_ns,host_ticks()-start,__ATOMIC_RELAXED);
    return p;
}
id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *d, const char *pass, NSError **error) {
    return pipeline_build_device(mtl,d,pass,error);
}
id<MTLRenderPipelineState> pipeline_cached(MTLRenderPipelineDescriptor *d, uint64_t v, uint64_t f, const char *pass, NSError **error) {
    PipelineRecipe r=describe(d,v,f);
    NSData *key=[NSData dataWithBytes:&r length:sizeof r];
    pthread_mutex_lock(&lock);
    id<MTLRenderPipelineState> p=states[key];
    pthread_mutex_unlock(&lock);
    if(p) return p;
    if(on_frame_path()) {
        record(&r,pass);
        for(size_t i=0;i<sizeof recipes/sizeof *recipes;++i) {
            PipelineRecipe listed=resolve_recipe(recipes[i]);
            if(memcmp(&r,&listed,sizeof r))continue;
            __atomic_add_fetch(&known_builds,1,__ATOMIC_RELAXED);
            diag_event("pipeline_known_miss: recipe=%zu pass=%s",i,pass); break;
        }
    }
    p=pipeline_build(d,pass,error);
    if(p) {
        pthread_mutex_lock(&lock);
        if(!states) states=[NSMutableDictionary new];
        states[key]=p;
        pthread_mutex_unlock(&lock);
    }
    return p;
}
void pipeline_prewarm(dispatch_queue_t __strong queues[4], NSUInteger scene_samples) {
    scene_sample_count=scene_samples;
    if(!enabled()) return;
    NSMutableSet<NSData *> *scheduled=[NSMutableSet new];
    for(size_t i=0;i<sizeof recipes/sizeof *recipes;++i) {
        PipelineRecipe r=resolve_recipe(recipes[i]); if(!r.vertex)continue;
        NSData *key=[NSData dataWithBytes:&r length:sizeof r];
        if([scheduled containsObject:key])continue;
        [scheduled addObject:key];
        // Functions are published before this call, and immutable for the load.
        MTLRenderPipelineDescriptor *d=descriptor(&r);
        if(!d) { if(diag_on)diag_event("pipeline_prewarm: missing_shader=%zu",i); continue; }
        dispatch_async(queues[i%4], ^{ @autoreleasepool {
            NSError *error=nil;
            if(!pipeline_cached(d,r.vertex,r.fragment,"prewarm",&error) && diag_on)
                diag_event("pipeline_prewarm: failed=%zu %s",i,error.localizedDescription.UTF8String);
        }});
    }
}
void pipeline_loading_done(void) {
    frame_thread=pthread_self();
    loading=0;
    if(diag_on) { last_tick=host_ticks(); diag_event("pipeline_prewarm: ready=%lu enabled=%d",(unsigned long)states.count,enabled()); }
}
void pipeline_diag_tick(void) {
    if(!diag_on) return;
    uint64_t now=host_ticks(); if(now-last_tick<1000000000ull)return;
    uint64_t builds=__atomic_load_n(&frame_builds,__ATOMIC_RELAXED),known=__atomic_load_n(&known_builds,__ATOMIC_RELAXED);
    diag_event("pipeline_frame: builds=%llu known=%llu total=%llu known_total=%llu",(unsigned long long)(builds-last_builds),(unsigned long long)(known-last_known),(unsigned long long)builds,(unsigned long long)known);
    last_builds=builds; last_known=known; last_tick=now;
}

void pipeline_frame_stats(unsigned long long *count, unsigned long long *ns) {
    *count=__atomic_load_n(&frame_builds,__ATOMIC_RELAXED); *ns=__atomic_load_n(&frame_ns,__ATOMIC_RELAXED);
}
