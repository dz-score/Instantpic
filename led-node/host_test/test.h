/* A test framework small enough to read in one sitting.
 *
 * No dependency, no discovery magic, no build-system integration. The point of
 * host tests here is that they run anywhere with a C compiler and nothing else.
 */
#pragma once

#include <math.h>
#include <stdio.h>
#include <string.h>

static int tests_run;
static int tests_failed;
static const char *current_test;

#define TEST(name)                                     \
    static void name(void);                            \
    static void run_##name(void) {                     \
        current_test = #name;                          \
        tests_run++;                                   \
        name();                                        \
    }                                                  \
    static void name(void)

#define RUN(name) run_##name()

#define FAILED(fmt, ...)                                              \
    do {                                                              \
        tests_failed++;                                               \
        printf("  FAIL %s\n    " fmt "\n", current_test, __VA_ARGS__); \
        return;                                                       \
    } while (0)

#define CHECK(cond)                                          \
    do {                                                     \
        if (!(cond)) { FAILED("%s:%d: %s", __FILE__, __LINE__, #cond); } \
    } while (0)

#define CHECK_INT(actual, expected)                                        \
    do {                                                                   \
        long a_ = (long)(actual), e_ = (long)(expected);                   \
        if (a_ != e_) {                                                    \
            FAILED("%s:%d: %s == %ld, expected %ld",                       \
                   __FILE__, __LINE__, #actual, a_, e_);                   \
        }                                                                  \
    } while (0)

#define CHECK_STR(actual, expected)                                        \
    do {                                                                   \
        const char *a_ = (actual), *e_ = (expected);                       \
        if (strcmp(a_, e_) != 0) {                                         \
            FAILED("%s:%d: %s == \"%s\", expected \"%s\"",                 \
                   __FILE__, __LINE__, #actual, a_, e_);                   \
        }                                                                  \
    } while (0)

#define CHECK_NEAR(actual, expected, tol)                                  \
    do {                                                                   \
        double a_ = (double)(actual), e_ = (double)(expected);             \
        if (fabs(a_ - e_) > (tol)) {                                       \
            FAILED("%s:%d: %s == %f, expected %f (+/- %f)",                \
                   __FILE__, __LINE__, #actual, a_, e_, (double)(tol));    \
        }                                                                  \
    } while (0)

static int test_report(const char *suite)
{
    if (tests_failed == 0) {
        printf("%s: %d passed\n", suite, tests_run);
        return 0;
    }
    printf("%s: %d/%d FAILED\n", suite, tests_failed, tests_run);
    return 1;
}
