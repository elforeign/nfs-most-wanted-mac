// Shared settings/switch Boolean spelling. -1 means unset or invalid.
#pragma once
#include <ctype.h>
#include <string.h>
#include <strings.h>
static inline int setting_bool_parse(const char *s) {
    if (!s) return -1;
    while (isspace((unsigned char)*s)) ++s;
    size_t n = strlen(s);
    while (n && isspace((unsigned char)s[n - 1])) --n;
    if ((n == 1 && s[0] == '1') || (n == 2 && !strncasecmp(s, "on", n)) ||
        (n == 4 && !strncasecmp(s, "true", n))) return 1;
    if ((n == 1 && s[0] == '0') || (n == 3 && !strncasecmp(s, "off", n)) ||
        (n == 5 && !strncasecmp(s, "false", n))) return 0;
    return -1;
}
