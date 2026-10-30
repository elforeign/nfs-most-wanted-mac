// UPnP IGD text codecs. Inputs are bounded, NUL-terminated HTTP/XML messages.
#include "natmap.h"
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
int upnp_header(const char *http, const char *key, char *out, size_t n) {
    size_t k = strlen(key);
    for (const char *p = http; *p;) {
        const char *e = strstr(p, "\r\n"); if (!e) e = p + strlen(p);
        if (e == p) break;
        if ((size_t)(e - p) > k && !strncasecmp(p, key, k) && p[k] == ':') {
            p += k + 1; while (p < e && isspace((unsigned char)*p)) ++p;
            while (e > p && isspace((unsigned char)e[-1])) --e;
            if ((size_t)(e - p) >= n) return -1;
            memcpy(out, p, (size_t)(e - p)); out[e - p] = 0; return 0;
        }
        if (!*e) break;
        p = e + 2;
    }
    return -1;
}
static int unescape(const char *p, size_t len, char *out, size_t cap) {
    size_t j = 0;
    for (size_t i = 0; i < len; ++i) {
        char c = p[i];
        if (c == '&') {
            static const char *const e[] = {"&amp;", "&lt;", "&gt;", "&quot;", "&apos;"};
            static const char v[] = "&<>\"'"; int found = 0;
            for (int q = 0; q < 5; ++q) if (len - i >= strlen(e[q]) && !memcmp(p + i, e[q], strlen(e[q]))) {
                c = v[q]; i += strlen(e[q]) - 1; found = 1; break;
            }
            if (!found) return -1;
        }
        if (j + 1 >= cap) return -1;
        out[j++] = c;
    }
    if (!cap) return -1;
    out[j] = 0; return 0;
}
int upnp_xml(const char *xml, const char *key, char *out, size_t n) {
    for (const char *p = xml; (p = strchr(p, '<'));) {
        ++p; if (*p == '/' || *p == '!' || *p == '?') continue;
        const char *e = p; while (*e && *e != '>' && !isspace((unsigned char)*e)) ++e;
        const char *local = p; for (const char *q = p; q < e; ++q) if (*q == ':') local = q + 1;
        if ((size_t)(e - local) != strlen(key) || memcmp(local, key, strlen(key))) continue;
        const char *start = strchr(e, '>'); if (!start) return -1;
        ++start; const char *end = strchr(start, '<'); if (!end || end[1] != '/') return -1;
        const char *close = end + 2;
        if (strncmp(close, p, (size_t)(e - p)) || close[e - p] != '>') return -1;
        while (start < end && isspace((unsigned char)*start)) ++start;
        while (end > start && isspace((unsigned char)end[-1])) --end;
        return unescape(start, (size_t)(end - start), out, n);
    }
    return -1;
}
int upnp_service(const char *xml, char *service, size_t sn, char *control, size_t cn) {
    for (const char *p = xml; (p = strstr(p, "<service>"));) {
        const char *e = strstr(p, "</service>"); if (!e) return -1;
        size_t len = (size_t)(e + 10 - p); if (len >= 4096) return -1;
        char entry[4096]; memcpy(entry, p, len); entry[len] = 0;
        if (!upnp_xml(entry, "serviceType", service, sn) &&
            (!strcmp(service, "urn:schemas-upnp-org:service:WANIPConnection:1") ||
             !strcmp(service, "urn:schemas-upnp-org:service:WANIPConnection:2") ||
             !strcmp(service, "urn:schemas-upnp-org:service:WANPPPConnection:1")) &&
            !upnp_xml(entry, "controlURL", control, cn)) return 0;
        p = e + 10;
    }
    return -1;
}
int upnp_soap(char *out, size_t n, const char *service, const char *action, const char *args) {
    // service/action are selected locally or validated by upnp_service, never arbitrary XML.
    if (strpbrk(service, "<>&\"'") || strpbrk(action, "<>&\"'")) return -1;
    int m = snprintf(out, n, "<?xml version=\"1.0\"?>"
        "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\">"
        "<s:Body><u:%s xmlns:u=\"%s\">%s</u:%s></s:Body></s:Envelope>", action, service, args, action);
    return m < 0 || (size_t)m >= n ? -1 : m;
}
