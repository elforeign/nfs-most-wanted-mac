#include "blackbox_errors.h"
#include <errno.h>
#include <string.h>

void blackbox_error(uint32_t subsystem, uint32_t operation, int64_t code,
                    uint64_t identifier, uint32_t caller, uint64_t serial) {
    int saved_errno = errno;
    BlackboxError error = {subsystem, operation, code, identifier, caller};
    blackbox_record_error(&error, serial);
    errno = saved_errno;
}

void blackbox_error_hresult(const char *method, uint32_t result, uint32_t caller) {
    if (!(result & 0x80000000u) || !blackbox_enabled() || !method) return;
    /* These are host vtable labels, never guest-supplied strings. Unknown
     * interfaces stay unclassified rather than treating arbitrary integers as HRESULTs. */
    int d3d = !strncmp(method, "IDirect3D", 9) || !strncmp(method, "ID3DXEffect", 11);
    int com = !strncmp(method, "IDirectSound", 12) || !strncmp(method, "IDirectInput", 12);
    const char *name = strstr(method, "::");
    if ((!d3d && !com) || !name) return;
    name += 2;
    static const char *const scalar[] = {
        "AddRef", "Release", "GetAvailableTextureMem", "GetAdapterCount",
        "GetAdapterModeCount", "GetNumberOfSwapChains", "GetAdapterMonitor",
        "GetSoftwareVertexProcessing", "GetNPatchMode", "ShowCursor",
        "GetType", "SetPriority", "GetPriority", "GetLevelCount",
        "SetLOD", "GetLOD", "GetAutoGenFilterType", "GetDataSize",
        "PreLoad", "GenerateMipSubLevels", "SetCursorPosition", "SetGammaRamp", "GetGammaRamp"
    };
    for (unsigned i = 0; i < sizeof scalar / sizeof *scalar; ++i)
        if (!strcmp(name, scalar[i])) return;
    if (!strncmp(method, "ID3DXEffect::", 13)) {
        static const char *const handles[] = {
            "GetParameter", "GetParameterByName", "GetParameterBySemantic", "GetParameterElement",
            "GetTechnique", "GetTechniqueByName", "GetPass", "GetPassByName",
            "GetFunction", "GetFunctionByName", "GetAnnotation", "GetAnnotationByName",
            "GetCurrentTechnique", "GetPool", "IsParameterUsed", "EndParameterBlock"
        };
        for (unsigned i = 0; i < sizeof handles / sizeof *handles; ++i)
            if (!strcmp(name, handles[i])) return;
    }
    /* A stable method ID, without copying vtable text into the recorder. */
    uint32_t id = 2166136261u;
    for (const unsigned char *p = (const void *)method; *p; ++p) id = (id ^ *p) * 16777619u;
    blackbox_error(d3d ? BB_ERR_D3D : BB_ERR_COM, BB_OP_HRESULT, result, id, caller, 0);
}
