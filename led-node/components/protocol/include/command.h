/* command.h — the one command vocabulary.
 *
 * Both transports parse into this. Keeping the parser here rather than in
 * either transport is what structurally prevents a second vocabulary from
 * appearing and drifting; see Docs/LED_NODE_ARCHITECTURE.md.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CMD_LINE_MAX  64
#define CMD_REPLY_MAX 48

typedef enum {
    CMD_NONE = 0,
    CMD_IDLE,       /* IDLE                */
    CMD_PHASE,      /* PHASE <hue 0..359>  */
    CMD_COUNTDOWN,  /* COUNTDOWN <ms>      */
    CMD_CAPTURE,    /* CAPTURE             */
    CMD_RELEASE,    /* RELEASE             */
    CMD_PRINTING,   /* PRINTING            */
    CMD_FINISHED,   /* FINISHED <ms>       */
    CMD_ERROR,      /* ERROR <code>        */
    CMD_PING,       /* PING                */
} cmd_verb_t;

typedef struct {
    cmd_verb_t verb;
    int32_t    arg;      /* hue, duration in ms, or error code */
    bool       has_arg;
} command_t;

typedef struct {
    char text[CMD_REPLY_MAX];
} cmd_reply_t;

/* What the transports put on the queue. The render task is the sole owner of
 * mode state; transports never touch it, they only produce these. */
typedef struct {
    command_t     cmd;
    QueueHandle_t reply_q;   /* NULL for fire-and-forget */
} cmd_req_t;

/* Parse one line (no trailing newline required). Returns false on an unknown
 * verb or a malformed argument, in which case *out is left with CMD_NONE.
 *
 * Case-insensitive, tolerates leading/trailing whitespace and a trailing \r so
 * that a line typed into a serial monitor from Windows still works. */
bool command_parse(const char *line, size_t len, command_t *out);

/* Verb spelling for building "OK <verb>" replies. Never NULL. */
const char *command_verb_name(cmd_verb_t verb);

#ifdef __cplusplus
}
#endif
