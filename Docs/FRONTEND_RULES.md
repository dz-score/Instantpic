# Frontend Rules

Architecture and code rules for the gravity Booth frontend (`frontend/`).
They apply to all screens, components, stores, and services. Rules are hard
requirements unless a rule states its own escape hatch; exceptions must be
justified in the PR that introduces them.

Maintained alongside the frontend. Last revised 2026-07-08.

---

# 1. UI is a Projection of Backend State

**The overarching rule. Every other rule is a consequence of this one.**

The backend owns the booth workflow. The frontend does not run its own state
machine for it — it projects the backend's authoritative state into the
appropriate UI.

```
Backend state
    ↓
Frontend store
    ↓
Components
    ↓
Rendered UI
```

**Boundary — what state lives where:**

- **Workflow state** (current screen, session, photos, countdown, printing
  progress) is backend-owned. The frontend reads it from the store and never
  invents it.
- **Ephemeral view state** (hover, pressed, modal open, animation progress,
  scroll position) is presentation-only and may live in the component. If the
  backend restarted and reconnected, this state should be safely losable.

Litmus test: *would this state still matter if the UI were replaced by a
different client?* Yes → backend-owned. No → component-local is fine.

---

## Part I — State Ownership

# 2. UI Never Owns Business Logic

The frontend displays state. The backend decides state.
The frontend requests. The backend decides.

**Business logic** is any decision that would still matter if the UI were
replaced: session transitions, how many photos a session takes, whether a
print is allowed, retry policy for captures.

**Presentation logic** is not business logic and belongs in the frontend:
formatting a countdown as `0:03`, disabling a button while a request is
in flight, choosing which animation plays.

✅ Good

```js
sessionStore.requestPrint()   // backend decides if printing is allowed
```

❌ Bad

```js
if (photos.length === 4) {    // frontend deciding the workflow
  goToRevealScreen()
}
```

---

# 3. Backend Owns Workflow Timing; Frontend Owns Presentation Timing

The frontend never advances the workflow on its own clock.

✅ Good

```
Backend emits STATE_CHANGED
    ↓
Frontend updates
```

❌ Bad

```js
await sleep(3000)   // guessing when the backend is done
switchScreen()
```

**Exception — presentation timing is frontend-owned.** CSS transitions,
spinners, and tick animations are inherently frontend-timed. A countdown is
the canonical case: the backend anchors it (emits start + duration, and the
state change that ends it); the frontend animates the ticks in between.
Frontend timers may animate *within* a backend-defined window — they may
never *decide* when the window ends.

---

# 4. One Source of Truth

Never duplicate workflow state. Each piece of state has exactly one owner —
the store — and components read from it.

✅ Good

```
Store
├── state
├── photos
└── countdown
```

❌ Bad

```
CameraPage.state
Gallery.state
Countdown.state
```

(Ephemeral view state is exempt — see Rule 1's boundary.)

---

# 5. State is Immutable as Observed by Consumers

Consumers of the store must never see an object change under them; updates
produce new references so change detection works.

✅ Good

```js
photos = [...photos, photo]
```

❌ Bad

```js
photos.push(photo)   // mutating the array consumers already hold
```

If the store uses a library with a mutable-draft API (e.g. Immer), `push`
inside a producer is fine — the rule is about what consumers observe, not
about syntax.

---

# 6. Derived State is Never Stored

Compute instead of duplicating.

✅ Good

```js
const photoCount = photos.length
```

❌ Bad

```js
store.photoCount = 4   // now it can disagree with photos.length
```

---

## Part II — Data Flow

# 7. Backend Events Drive UI — Never Poll When Events Exist

The frontend reacts to pushed events.

✅ Good

```
WebSocket / SSE
```

❌ Bad

```js
setInterval(() => fetch('/api/state'), 1000)
```

Escape hatch: polling is acceptable only when no event channel exists for
that data, and the PR must say so.

---

# 8. Backend Access Goes Through One Layer, and Components Never See It

All backend traffic flows through a single path:

```
UI
 ↓
Store
 ↓
ApiClient
 ↓
Backend
```

If endpoints change, only `ApiClient` changes.

Components never know transport details — no URLs, verbs, or `fetch` calls.

✅ Good

```js
sessionStore.startCapture()
```

❌ Bad

```js
fetch('/api/session/123')   // inside a component
```

---

# 9. No Hidden Magic

Flows are explicit. Nothing important happens as an invisible side effect of
mounting.

✅ Good

```
App initializes
    ↓
connect()
    ↓
receive state
    ↓
render
```

❌ Bad

```
component mounts
    ↓
automatically starts session
    ↓
opens websocket
    ↓
starts timer
```

Explicit flows are easier to debug.

---

# 10. Visual Components Never Drive Application State

A presentation component reports intent; it never performs the transition
itself.

✅ Good

```
Gallery
    ↓
emit PrintClicked
    ↓
Store → API → Backend
    ↓
new state arrives
    ↓
UI updates
```

❌ Bad

```
Gallery
    ↓
starts printing
    ↓
changes session
    ↓
returns home
```

---

## Part III — Component Design

# 11. Small, Single-Purpose Components

Every component has exactly one responsibility, and most components should
only render.

✅ Good

- `CountdownOverlay` only displays the countdown.
- `SessionGallery` only renders captured photos.
- `CameraPreview` only displays the video stream.
- `<Button/>`, `<Overlay/>`, `<Spinner/>` — dumb, render-only.

❌ Bad

A `CameraPage` component that calls backend APIs, manages state, validates
transitions, renders UI, and starts the countdown.

**Size guideline:** ~200 lines per component file (everything in the file —
imports, types, JSX). This is a smell threshold, not a hard gate: split when
a component has multiple responsibilities, multiple layouts, or unrelated
logic — not merely to duck under the number.

---

# 12. No Side Effects During Render

Rendering depends only on props and state. Side effects — network requests,
timers, WebSocket handling, polling, event listeners — live in hooks,
services, or stores, never inline in presentation components.

✅ Good

```jsx
<Countdown seconds={3} />
```

❌ Bad

```jsx
const Countdown = () => {
  fetch(...)   // side effect in render
}
```

---

# 13. Reusable UI — Rule of Three

Duplicated UI is a candidate for extraction on the second copy and is
extracted on the third. Two occurrences that are only coincidentally similar
may stay duplicated — premature extraction couples things that will diverge.

Examples of extracted components:

```
LoadingOverlay
ErrorBanner
CountdownModal
Button
Header
```

---

# 14. One Design System

One source of truth for colors, spacing, typography, icons, buttons, and
dialogs: **`frontend/src/design-tokens.css`**. Components consume tokens;
no arbitrary hardcoded values scattered across component styles.

✅ Good

```css
color: var(--color-cta);
```

❌ Bad

```css
color: #ff2d78;   /* hardcoded, drifts from the token */
```

---

## Part IV — UX States

# 15. Explicit State Handling

Every async resource models its full lifecycle. Never infer status from the
shape of the data.

✅ Good

```ts
type PhotosState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'loaded'; photos: Photo[] }
  | { status: 'error'; error: string }
```

❌ Bad

```js
if (photos) { ... }   // conflates loading, empty, and error
```

Every state is visible in the UI.

---

# 16. Errors Are First-Class State

Errors are rendered, not swallowed.

✅ Good

```
loading · ready · error · offline · disconnected
```

— each one a real state with a real screen or banner.

❌ Bad

```js
console.error(err)   // and the guest stares at a frozen screen
```

The guest should always know what is happening.

---

# 17. Disconnection Has a Defined Recovery Path

The booth runs unattended; the WebSocket *will* drop. The frontend must:

- show a distinct disconnected state (Rule 16) rather than freezing on stale
  UI;
- auto-reconnect with backoff, indefinitely — a kiosk never gives up and
  never requires a keyboard;
- on reconnect, discard local assumptions and re-project whatever state the
  backend reports (Rule 1) — never resume from a remembered screen.

❌ Bad

```
socket.onclose = () => {}   // UI silently freezes on the last frame
```

---

# 18. Kiosk and Media Constraints

This frontend runs full-screen on a touch kiosk and handles large images.

- **Touch:** interactive targets are finger-sized; no hover-only
  affordances; browser zoom, text selection, and context menus are
  suppressed in kiosk mode.
- **Media memory:** a booth accumulates large photos fast. Revoke object
  URLs when images leave the screen, prefer backend-served thumbnails to
  full-resolution originals in galleries, and never hold whole sessions of
  full-size images in memory across sessions.
