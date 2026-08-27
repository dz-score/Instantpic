# Photobooth Software Architecture Rules

## Core Principles

1. The application is state-machine driven.
2. The state machine is the only authority that decides workflow progression.
3. All hardware is treated as unreliable and must be isolated behind services.
4. The frontend is a view layer only and contains no business logic.
5. Every operation must be recoverable after failure.
6. There must be a single source of truth for application state.
7. Services execute actions; they never decide workflow.

---

## State Management

### Rule 1: Single Source of Truth

All application state must exist in a single BoothState object.

No service may maintain its own authoritative copy of:

* current screen
* captured photos
* layout selection
* session information
* print status
... etc

State changes must occur through explicit state transitions.

---

### Rule 2: State Machine Owns Navigation

Only the state machine may change screens.

Forbidden:

* camera service changing screens
* frontend changing screens directly
* image processing service changing screens
...etc

Required:

StateMachine -> transition(next_state)

---

### Rule 3: Valid Transitions Only

All workflow transitions must be explicitly defined.


Unexpected transitions are rejected and logged.

---

## Service Architecture

### Rule 4: Services Execute, State Machine Decides

Services perform actions only.


Services must never decide what happens next.

---

### Rule 5: Single Hardware Owner

Every hardware device has exactly one owner service.

Examples:

CameraService owns camera access.

PrinterService owns printer access.

LedService owns LED access.

No other component may directly access hardware APIs.

---

### Rule 6: No Direct gPhoto2 Access Outside CameraService

All camera communication must go through CameraService.



Only CameraService may communicate with the camera.

---

## Event Architecture

### Rule 7: Event Driven Communication

Components communicate through events.


Components must not directly orchestrate one another.

---

### Rule 8: Async First

All hardware operations are asynchronous.

Examples:

* camera capture
* autofocus
* image download
* printing
* reconnect attempts

No hardware operation may block the main application loop.

---

## Reliability

### Rule 9: Hardware Failures Are Expected

The system must assume:

* camera disconnects
* printer disconnects
* USB failures
* storage failures
* Raspberry Pi reboot

Recovery paths must be implemented for every hardware dependency.

---

### Rule 10: Timeouts Whenever Necessary

All hardware operations require explicit timeouts.

No operation may wait indefinitely.

Failures must produce events and logs.

---

### Rule 11: Idempotent Commands

Commands should be safe when executed multiple times.

Examples:

start_preview()

stop_preview()

connect_camera()

disconnect_camera()

Repeated execution must not crash the system.

---

## Session Management

### Rule 12: Session Isolation

Each customer session is independent.

A session owns:

* captured images
* generated collage
* temporary files
* metadata

Sessions must never reuse previous session resources.

---

### Rule 13: Deterministic Cleanup

At session end:

* temporary files removed
* camera returned to known state
* timers cancelled
* state reset

The booth must return to ATTRACT state.

---

## Frontend Rules

### Rule 14: Frontend Is Dumb

Frontend responsibilities:

* display state
* send user actions

Frontend must not contain:

* workflow logic
* camera logic
* printing logic
* business rules

All decisions belong to backend.

---

### Rule 15: Backend Is Authoritative

Frontend state is derived from backend state.

If frontend and backend disagree, backend wins.

---

## Logging

### Rule 16: Log All Significant Events

Log:

* state transitions
* captures
* prints
* errors
* reconnect attempts
* session lifecycle

Logs must allow complete reconstruction of a customer session.

---

## Code Organization

### Rule 17: Side Effects at the Edges

Business logic should be pure whenever possible.

Side effects belong only in services.

---

### Rule 18: Dependency Direction

Allowed:

UI
→ State Machine
→ Services
→ Hardware

Forbidden:

Hardware
→ State Machine

Services
→ UI

UI
→ Hardware

Dependencies must always point downward.

---

### Rule 19: Wiring Happens at the Composition Root

Services are constructed and connected in exactly one place: the application
entrypoint's startup path.

Forbidden:

* module-level singletons that other modules import to reach a service
* work in a constructor — threads, I/O, timers, device handles
* two wiring mechanisms for the same edge (an injected dependency that also
  falls back to a global)

Importing a module must have no side effects. If constructing an object starts
a thread or opens a device, the object cannot be tested, replaced, or shut down
on demand.

Required: the entrypoint constructs each service, passes collaborators in, and
owns their lifecycle. Everything below the entrypoint receives its dependencies
and never reaches for them.

Sanctioned exception: the logger (`backend/logger.py`, the `log` singleton). It
is the one dependency every module needs including during import, threading it
through every constructor would be noise for no testability gain, and its only
construction side effect (opening log files) is redirectable via the
`BOOTH_LOG_DIR` environment variable — which the test suite must set before any
backend import (see conftest.py). Any other module claiming this exception
needs this paragraph amended first.

---

### Rule 20: One Reason to Change per Module

Split modules by responsibility, not by length.

A module that owns a device typically accumulates several distinct jobs — the
background loop, the consumer-facing stream, the one-shot command runner, the
settings interface. Each is a separate reason to change and belongs in its own
unit, even when they share a device.

The strongest signal that a boundary is missing: two or more concurrency
domains (a worker thread, a callback thread, the event loop) coordinating
through shared mutable flags on the same object. Every defensive comment
explaining why a guard is duplicated in a second place is that missing boundary
asking to be drawn.

When choosing what to refactor, prefer the tangled module over the merely long
one.

---

### Rule 21: Configuration Has One In-Memory Authority

Configuration is loaded from disk once and held in memory. Memory is the source
of truth; the file is where it is persisted. Writes go through the authority,
which updates memory and flushes to disk.

Forbidden:

* re-reading and re-parsing the config file per request, per event, or per
  operation
* a second path to the values that bypasses the authority — keep the disk read
  private so no call site can reach past it by accident

Cost is not the reason. The reason is that a value re-derived at every call site
has no single authority, no place to change how it is obtained, and nowhere to
put a lock — a read-modify-write against the file races two concurrent writers
into a lost update.

#### Passing config down is not a violation — re-deriving it is

There are two reasons a settings object travels as a parameter, and only one of
them is the smell:

* **Accidental** — it is threaded through layers *because no layer is allowed to
  ask for it*. That is the missing authority, and it is what this rule forbids.
* **Deliberate** — a long-running operation takes a **snapshot** at entry and
  works from it for its whole duration, so a mid-flight config change cannot
  alter behaviour halfway through. That is a feature, and this rule requires it.

Required, for any operation that spans multiple steps or outlives the request
that started it (a capture sequence, a print run, a session):

* the operation reads config **once, at entry**, and passes that snapshot down
* the authority **rebinds** its cached object on write rather than mutating it in
  place, so every in-flight holder keeps the snapshot it started with
* re-reading the authority at each step of such an operation is a bug: an admin
  save mid-sequence would retime or reconfigure a session already underway

Immutability is what makes this safe. A settings object handed to a running
operation must never be mutated by a later write.

---

## Code and Documentation Hygiene

### Rule 22: Comments State Live Constraints, Not History

A comment explains what the code cannot: a constraint, an invariant, a
non-obvious reason this value and not another.

Forbidden:

* narrating what the code used to be
* explaining why a past approach was wrong
* eulogies for deleted code

Version control holds history; investigation notes hold the archaeology. A hot
file whose comments are mostly obituaries cannot be read for what it does now.

---

### Rule 23: Documentation Records Why, Not What

Prose documentation earns its place only when it holds knowledge the code
cannot express: hardware behavior, root causes, constraints discovered the hard
way, decisions and their rejected alternatives.

Forbidden:

* hand-maintained documents that restate an interface the code already declares
  or a tool already generates
* prose transcriptions of a transition table, a schema, or a route list

Anything derivable from the code will drift from it, and a drifted document is
worse than none.

---

### Rule 24: Diagnostic Code Is Temporary

Probes, one-off harnesses, and instrumentation written to characterize a
specific failure are deleted once that failure is understood and fixed. The
finding is recorded in the notes; the scaffolding is not kept.

Instrumentation may stay in a production path only if it earns its cost as a
permanent signal — and then it is documented as such, not left behind by
accident.

No diagnostic tool should outgrow the production module it was written to
investigate.

---

### Rule 25: Every Fact Has One Home

A decision, a constraint, or a piece of hardware knowledge is written down once.
Everything that needs it links to it.

Two places this goes wrong, and they are the same mistake at two scales:

* a code comment that re-argues what a document already argues
* two documents that both argue the same point

A comment carries only what breaks if you change **this** line — a sentence or
two — and names the document for the rest. A fact belongs to the file whose
stated job it is: rules to CONSTRAINTS, hardware knowledge to the relevant
`*_NOTES` file, structure to ARCHITECTURE, cross-component sequences to
WORKFLOWS, the frontend/backend contract to API_PROTOCOL. Everywhere else, a
link.

Cost is not the reason; drift is. Two copies of a reason diverge, and the one
nobody is looking at goes stale first — after which a reader who finds the stale
copy is worse off than if it had never been written at all.

Diagnostic: a comment longer than about three lines that is *arguing* rather
than *warning*. Warnings stay — "do not read job states here", "keep the dpi
tag" — because they are what stops the next edit breaking something. Arguments
move to the document that owns them.

Required when a change touches both code and `Docs/`: grep your own distinctive
phrases across both before committing. A phrase that appears in two files means
one of them should be a link. This is not optional diligence — writing code and
its documentation in one sitting is exactly when the same justification gets
written twice, because the author has it in mind in both places and no prompt to
ask where it already lives.

---

## Architectural Goal

The system must continue operating correctly even when hardware disconnects, operations timeout, or a session is interrupted. The state machine remains the authoritative source of truth, services remain isolated, and every component has a single clear responsibility.
