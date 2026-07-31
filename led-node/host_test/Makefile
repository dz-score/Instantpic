# Host tests for led-node. Needs a C compiler and nothing else.
#
#     make          build and run everything
#     make check    same, for CI
#     make compile  compile only (useful where the host cannot run the binaries)
#
# Deliberately not an ESP-IDF linux-target build. That needs a full IDF install
# and a POSIX host toolchain; this needs cc. The components under test are pure
# by design, so the cheap option is the one that works.

CC      ?= cc
CFLAGS  ?= -std=c11 -O1 -g -Wall -Wextra -Wno-unused-parameter
LDLIBS  ?= -lm

ROOT     := ..
PROTOCOL := $(ROOT)/components/protocol
RENDER   := $(ROOT)/components/render

INCLUDES := -Istubs -I. \
            -I$(PROTOCOL)/include \
            -I$(RENDER)/include \
            -I$(RENDER)

BIN := build
TESTS := $(BIN)/test_parse $(BIN)/test_canvas $(BIN)/test_modes

.PHONY: all check compile clean
all: check

$(BIN):
	mkdir -p $(BIN)

$(BIN)/test_parse: test_parse.c $(PROTOCOL)/parse.c | $(BIN)
	$(CC) $(CFLAGS) $(INCLUDES) $^ -o $@ $(LDLIBS)

$(BIN)/test_canvas: test_canvas.c $(RENDER)/canvas.c | $(BIN)
	$(CC) $(CFLAGS) $(INCLUDES) $^ -o $@ $(LDLIBS)

# modes.c is #included by the test (its functions are static), so it is not
# listed as a source here — adding it would define everything twice.
$(BIN)/test_modes: test_modes.c $(PROTOCOL)/parse.c | $(BIN)
	$(CC) $(CFLAGS) $(INCLUDES) $^ -o $@ $(LDLIBS)

check: $(TESTS)
	@fail=0; for t in $(TESTS); do ./$$t || fail=1; done; exit $$fail

compile: $(TESTS)
	@echo "compiled: $(TESTS)"

clean:
	rm -rf $(BIN)
