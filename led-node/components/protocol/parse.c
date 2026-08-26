#include "command.h"

#include <ctype.h>
#include <string.h>
#include <strings.h>   /* strncasecmp */

typedef struct {
    const char *name;
    cmd_verb_t  verb;
    bool        wants_arg;
} verb_entry_t;

static const verb_entry_t VERBS[] = {
    {"IDLE",      CMD_IDLE,      false},
    {"PHASE",     CMD_PHASE,     true },
    {"READY",     CMD_READY,     false},
    {"COUNTDOWN", CMD_COUNTDOWN, true },
    {"CAPTURE",   CMD_CAPTURE,   false},
    {"RELEASE",   CMD_RELEASE,   false},
    {"PRINTING",  CMD_PRINTING,  false},
    {"FINISHED",  CMD_FINISHED,  true },
    {"ERROR",     CMD_ERROR,     true },
    {"TEST",      CMD_TEST,      true },
    {"PING",      CMD_PING,      false},
};

static const size_t VERB_COUNT = sizeof(VERBS) / sizeof(VERBS[0]);

static bool is_space(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

/* Parse a non-negative decimal integer. Returns false on overflow, on a
 * non-digit, or on an empty span. */
static bool parse_int(const char *s, size_t len, int32_t *out)
{
    if (len == 0) {
        return false;
    }
    int64_t v = 0;
    for (size_t i = 0; i < len; i++) {
        if (!isdigit((unsigned char)s[i])) {
            return false;
        }
        v = v * 10 + (s[i] - '0');
        if (v > INT32_MAX) {
            return false;
        }
    }
    *out = (int32_t)v;
    return true;
}

bool command_parse(const char *line, size_t len, command_t *out)
{
    out->verb    = CMD_NONE;
    out->arg     = 0;
    out->has_arg = false;

    if (line == NULL || out == NULL) {
        return false;
    }

    /* Trim both ends. */
    size_t start = 0;
    while (start < len && is_space(line[start])) {
        start++;
    }
    while (len > start && is_space(line[len - 1])) {
        len--;
    }
    if (start == len) {
        return false;
    }

    /* Split verb from argument at the first run of whitespace. */
    size_t vend = start;
    while (vend < len && !is_space(line[vend])) {
        vend++;
    }
    const size_t vlen = vend - start;

    size_t astart = vend;
    while (astart < len && is_space(line[astart])) {
        astart++;
    }
    const size_t alen = len - astart;

    for (size_t i = 0; i < VERB_COUNT; i++) {
        const size_t nlen = strlen(VERBS[i].name);
        if (nlen != vlen || strncasecmp(line + start, VERBS[i].name, nlen) != 0) {
            continue;
        }

        if (VERBS[i].wants_arg) {
            int32_t arg = 0;
            if (!parse_int(line + astart, alen, &arg)) {
                return false;
            }
            out->arg     = arg;
            out->has_arg = true;
        } else if (alen != 0) {
            /* Extra tokens on a no-arg verb mean the caller thinks it is
             * speaking a protocol we do not have. Better to reject than to
             * silently do something adjacent. */
            return false;
        }

        out->verb = VERBS[i].verb;
        return true;
    }

    return false;
}

const char *command_verb_name(cmd_verb_t verb)
{
    for (size_t i = 0; i < VERB_COUNT; i++) {
        if (VERBS[i].verb == verb) {
            return VERBS[i].name;
        }
    }
    return "NONE";
}
