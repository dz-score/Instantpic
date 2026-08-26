/* command_parse() against the table in Docs/LED_PROTOCOL.md.
 *
 * The parser is the one component both transports share, so a divergence here
 * would be a divergence everywhere. It is also pure, which is why it can be
 * tested with nothing but a compiler.
 */
#include "command.h"
#include "test.h"

static command_t parse(const char *line)
{
    command_t out;
    command_parse(line, strlen(line), &out);
    return out;
}

static int accepts(const char *line)
{
    command_t out;
    return command_parse(line, strlen(line), &out);
}

TEST(every_documented_verb_parses)
{
    CHECK_INT(parse("IDLE").verb, CMD_IDLE);
    CHECK_INT(parse("PHASE 280").verb, CMD_PHASE);
    CHECK_INT(parse("READY").verb, CMD_READY);
    CHECK_INT(parse("COUNTDOWN 3000").verb, CMD_COUNTDOWN);
    CHECK_INT(parse("CAPTURE").verb, CMD_CAPTURE);
    CHECK_INT(parse("RELEASE").verb, CMD_RELEASE);
    CHECK_INT(parse("PRINTING").verb, CMD_PRINTING);
    CHECK_INT(parse("FINISHED 4000").verb, CMD_FINISHED);
    CHECK_INT(parse("ERROR 3").verb, CMD_ERROR);
    CHECK_INT(parse("PING").verb, CMD_PING);
}

TEST(arguments_are_captured)
{
    CHECK_INT(parse("PHASE 280").arg, 280);
    CHECK_INT(parse("PHASE 280").has_arg, 1);
    CHECK_INT(parse("COUNTDOWN 3000").arg, 3000);
    CHECK_INT(parse("IDLE").has_arg, 0);
}

TEST(verbs_are_case_insensitive)
{
    CHECK_INT(parse("idle").verb, CMD_IDLE);
    CHECK_INT(parse("ready").verb, CMD_READY);
    CHECK_INT(parse("Phase 90").verb, CMD_PHASE);
    CHECK_INT(parse("cApTuRe").verb, CMD_CAPTURE);
}

TEST(surrounding_whitespace_is_tolerated)
{
    /* A line typed into a serial monitor from Windows arrives with \r\n, and a
     * human typing it adds spaces. Neither should be a protocol error. */
    CHECK_INT(parse("  IDLE  ").verb, CMD_IDLE);
    CHECK_INT(parse("IDLE\r\n").verb, CMD_IDLE);
    CHECK_INT(parse("\tPHASE\t90\t").arg, 90);
    CHECK_INT(parse("PHASE    90").arg, 90);
}

TEST(malformed_lines_are_rejected)
{
    CHECK_INT(accepts(""), 0);
    CHECK_INT(accepts("   "), 0);
    CHECK_INT(accepts("NOTAVERB"), 0);
    CHECK_INT(accepts("PHASE"), 0);          /* argument required */
    CHECK_INT(accepts("READY 1"), 0);        /* takes none */
    CHECK_INT(accepts("PHASE abc"), 0);
    CHECK_INT(accepts("PHASE 1.5"), 0);
    CHECK_INT(accepts("PHASE 0x10"), 0);
}

TEST(negative_arguments_are_malformed_not_out_of_range)
{
    /* The parser accepts digits only, so a negative never reaches the range
     * check in apply(). This is why ERR UNKNOWN, not ERR RANGE, comes back —
     * a distinction a client is entitled to rely on. */
    CHECK_INT(accepts("PHASE -1"), 0);
    CHECK_INT(accepts("COUNTDOWN -5"), 0);
}

TEST(overflow_is_rejected)
{
    CHECK_INT(accepts("COUNTDOWN 2147483647"), 1);
    CHECK_INT(accepts("COUNTDOWN 2147483648"), 0);
    CHECK_INT(accepts("COUNTDOWN 99999999999999999999"), 0);
}

TEST(extra_tokens_after_a_no_arg_verb_are_rejected)
{
    /* Rejected rather than ignored: a caller sending them believes it is
     * speaking a protocol we do not have, and silently doing something adjacent
     * is worse than saying no. */
    CHECK_INT(accepts("IDLE now"), 0);
    CHECK_INT(accepts("CAPTURE 1"), 0);
    CHECK_INT(accepts("PING PONG"), 0);
}

TEST(rejected_lines_leave_the_command_empty)
{
    command_t out;
    out.verb = CMD_CAPTURE;
    CHECK_INT(command_parse("GARBAGE", 7, &out), 0);
    CHECK_INT(out.verb, CMD_NONE);
}

TEST(verb_names_round_trip_for_ok_replies)
{
    CHECK_STR(command_verb_name(CMD_IDLE), "IDLE");
    CHECK_STR(command_verb_name(CMD_COUNTDOWN), "COUNTDOWN");
    CHECK_STR(command_verb_name(CMD_PING), "PING");
    CHECK_STR(command_verb_name(CMD_NONE), "NONE");
}

TEST(length_limit_matches_the_documented_one)
{
    CHECK_INT(CMD_LINE_MAX, 64);
    CHECK_INT(CMD_REPLY_MAX, 48);
}

int main(void)
{
    RUN(every_documented_verb_parses);
    RUN(arguments_are_captured);
    RUN(verbs_are_case_insensitive);
    RUN(surrounding_whitespace_is_tolerated);
    RUN(malformed_lines_are_rejected);
    RUN(negative_arguments_are_malformed_not_out_of_range);
    RUN(overflow_is_rejected);
    RUN(extra_tokens_after_a_no_arg_verb_are_rejected);
    RUN(rejected_lines_leave_the_command_empty);
    RUN(verb_names_round_trip_for_ok_replies);
    RUN(length_limit_matches_the_documented_one);
    return test_report("parse");
}
