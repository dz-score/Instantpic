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

## Architectural Goal

The system must continue operating correctly even when hardware disconnects, operations timeout, or a session is interrupted. The state machine remains the authoritative source of truth, services remain isolated, and every component has a single clear responsibility.
