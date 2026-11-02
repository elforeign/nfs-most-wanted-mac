#include "sm_hlsl_c.h"
#include "sm_ir.h"
#include <cstring>
#include <exception>
namespace sm { bool emit_hlsl(const Shader &, std::string &, std::string &); }
namespace {
void message(char *out, size_t capacity, const char *text) {
    if (!out || !capacity) return;
    size_t n = std::strlen(text);
    if (n >= capacity) n = capacity - 1;
    std::memcpy(out, text, n);
    out[n] = 0;
}
}
extern "C" sm_hlsl_status sm_hlsl_translate(const void *bytecode, size_t bytes,
    char *source, size_t capacity, size_t *required, sm_hlsl_interface *info,
    char *error, size_t error_capacity) {
    if (source && capacity) source[0] = 0;
    if (required) *required = 0;
    if (info) *info = {};
    message(error, error_capacity, "");
    if (!required || !bytecode || bytes < 8 || bytes > SM_HLSL_MAX_BYTECODE || bytes % 4 ||
        (!source && capacity) || (!error && error_capacity)) {
        message(error, error_capacity, "invalid HLSL translation argument");
        return SM_HLSL_INVALID_ARGUMENT;
    }
    try {
        const auto *data = static_cast<const unsigned char *>(bytecode);
        std::vector<uint32_t> tokens(bytes / 4);
        for (size_t k = 0; k < tokens.size(); ++k)
            tokens[k] = uint32_t(data[k*4]) | (uint32_t(data[k*4+1]) << 8) |
                (uint32_t(data[k*4+2]) << 16) | (uint32_t(data[k*4+3]) << 24);
        sm::Shader shader;
        std::string text, why;
        if (!sm::parse(tokens.data(), tokens.size(), shader)) {
            message(error, error_capacity, shader.error.c_str());
            return SM_HLSL_REJECTED;
        }
        // Runtime creation accepts exactly one program, not trailing token data.
        if (shader.end_token + 1 != tokens.size() || !sm::emit_hlsl(shader, text, why)) {
            message(error, error_capacity, why.empty() ? "trailing shader tokens" : why.c_str());
            return SM_HLSL_REJECTED;
        }
        if (text.size() + 1 > SM_HLSL_MAX_SOURCE) {
            message(error, error_capacity, "HLSL source limit exceeded");
            return SM_HLSL_REJECTED;
        }
        sm_hlsl_interface bindings = {};
        bindings.stage = shader.ps ? 1 : 0;
        for (const auto &i : shader.code) {
            if (i.op == sm::OP_DCL) {
                bindings.input_mask |= 1u << i.dst.num;
                bindings.input_usage[i.dst.num] = i.usage;
                bindings.input_usage_index[i.dst.num] = i.usage_index;
            }
            if ((shader.ps && i.op == sm::OP_TEX) ||
                (!shader.ps && i.has_dst && i.dst.type == sm::R_TEXCRDOUT))
                bindings.texture_mask |= 1u << i.dst.num;
        }
        *required = text.size() + 1;
        if (info) *info = bindings;
        if (capacity < *required) {
            message(error, error_capacity, "HLSL source buffer too small");
            return SM_HLSL_BUFFER_TOO_SMALL;
        }
        std::memcpy(source, text.c_str(), *required);
        return SM_HLSL_OK;
    } catch (const std::exception &) {
        message(error, error_capacity, "HLSL translation allocation failure");
    } catch (...) {
        message(error, error_capacity, "HLSL translation internal failure");
    }
    return SM_HLSL_INTERNAL_ERROR;
}
