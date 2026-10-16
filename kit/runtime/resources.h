// Shared reader for the loaded PE image's resource directory. Returned handles
// and payloads are guest addresses. No module or host pointer is substituted.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

struct ResourceName {
    bool is_string = false;
    uint32_t id = 0;
    std::string name; // UTF-8, decoded from the directory's length-prefixed UTF-16.
};

// Type and name accept integer IDs, "#123", or names: UTF-16 strings, or ANSI ones
// when ansi is set (FindResourceA). Select the first language and return its
// IMAGE_RESOURCE_DATA_ENTRY address, or zero.
uint32_t resource_find(uint32_t type_id_or_name, uint32_t name_id_or_name,
                       std::string *why = nullptr, bool ansi = false);
// Validate a data-entry handle and return its image-backed payload and size.
uint32_t resource_data(uint32_t entry, uint32_t *size, std::string *why = nullptr);
// Snapshot the names before guest callbacks run; no references into the directory
// or temporary guest buffers escape this reader. False explains failure in why.
bool resource_names(uint32_t type_id_or_name, std::vector<ResourceName> *names,
                    std::string *why = nullptr);
