
# 1. Single Responsibility

Every component has exactly one responsibility.

✅ Good

- `CountdownOverlay` only displays the countdown.
- `SessionGallery` only renders captured photos.
- `CameraPreview` only displays the video stream.

❌ Bad

A `CameraPage` component that:

- calls backend APIs
- manages state
- validates transitions
- renders UI
- starts countdown

---

# 2. UI Never Owns Business Logic

The frontend displays state.

The backend decides state.

The frontend requests.

The backend decides.

---

# 3. One Source of Truth

Never duplicate application state.

Example:

Current session

Good

```
Store ├── state ├── photos ├── countdown
```

Bad

```
CameraPage.stateGallery.stateCountdown.state
```

Only one owner.

---

# 4. Components are Pure

Rendering should depend only on props/state.

Good

```
<Countdown seconds={3}/>
```

Bad

```
const Countdown = () => {    fetch(...)}
```

Fetching belongs elsewhere.

---

# 5. Side Effects are Isolated

Network requests

Timers

WebSocket

Polling

Event listeners

should live in:

- hooks
- services
- stores

NOT inside presentation components.

---

# 6. Backend API Access Goes Through One Layer

Never do

```
fetch(...)
```

inside pages.

Instead

```
UI↓PhotoboothStore↓ApiClient↓Backend
```

If endpoints change,

only ApiClient changes.

---

# 7. No Backend Knowledge in Components

Component shouldn't know

```
GET /api/session/123
```

It should know

```
camera.start()
```

or

```
sessionStore.startCapture()
```

The transport layer stays hidden.

---

# 8. Dumb Components

Most components should only render.

Good

```
<Button/><Overlay/><Spinner/><Preview/><Gallery/>
```

Smart logic belongs elsewhere.

---

# 9. State is Immutable

Never mutate objects.

Bad

```
photos.push(photo)
```

Good

```
photos = [...photos, photo]
```

---

# 10. Derived State is Never Stored

Don't keep

```
photoCount
```

if

```
photos.length
```

already exists.

Compute instead of duplicating.

---

# 11. Backend Events Drive UI

The frontend should react to events.

Example

Backend emits

```
STATE_CHANGED
```

Frontend updates.

Not

```
sleep(3)thenswitch screen
```

The backend owns timing.

---

# 12. UI Never Polls When Events Exist

Prefer

```
WebSocketSSE
```

over

```
setInterval(fetch)
```

Polling only when unavoidable.

---

# 13. Keep Components Small

A practical rule:

~200 lines maximum.

Split when:

- multiple responsibilities
- multiple layouts
- unrelated logic

---

# 14. Reusable UI

If copied twice,

extract it.

Examples

```
LoadingOverlayErrorBannerCountdownModalButtonHeader
```

---

# 15. Explicit State Handling

Never assume.

Instead of

```
if (photos)
```

prefer

```
LoadingEmptyLoadedError
```

Every state is visible.

---

# 16. No Hidden Magic

Avoid

```
component mounts↓automatically starts session↓opens websocket↓starts timer
```

Instead

```
App initializes↓connect()↓receive state↓render
```

Explicit flows are easier to debug.

---

# 17. Visual Components Never Navigate Application State

For example

Bad

```
Gallery↓starts printing↓changes session↓returns home
```

Instead

```
Gallery↓emit PrintClicked↓Store↓API↓Backend↓New state↓UI updates
```

---

# 18. Errors Are First-Class State

Never

```
console.error(...)
```

and ignore.

Instead

```
LoadingReadyErrorOfflineDisconnected
```

The user should always know what is happening.

---

# 19. Consistent Design System

One source for:

- colors
- spacing
- typography
- icons
- buttons
- dialogs

No arbitrary styling scattered across components.

---

# 20. UI is a Projection of Backend State

This is the overarching rule that ties everything together.

```
Backend State        │        ▼Frontend Store        │        ▼Components        │        ▼Rendered UI
```

There should be no independent frontend state machine for the booth workflow. The frontend simply projects the backend's authoritative state into the appropriate UI.