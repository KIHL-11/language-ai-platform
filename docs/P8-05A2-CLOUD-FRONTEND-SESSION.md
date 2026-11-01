# P8-05A2 cloud frontend identity boundary

The entrypoint wraps the Personal application with CloudSessionProvider. It
waits for non-secret, no-store GET /api/deployment metadata before mounting any
learner view. Cloud mode then reads same-origin GET /api/account/me. Failure
never falls back to a local UUID or Personal records. The backend deployment
mode is authoritative; no query or localStorage switch exists.

CloudAuthClient keeps identity and CSRF in memory only. The HttpOnly cookie is
never read by JavaScript. All migrated Library, Study Bank, Daily Review,
AI Chunk, Topic and Integration clients use apiFetch. In cloud it enforces exact
same-origin URLs, credentials=same-origin, redirect=error and X-CSRF-Token on
unsafe methods. Browser-generated Origin is not manually forged. Legacy owner
claims must match the in-memory server identity; backend verification remains
authoritative. No unsafe request is automatically retried.

401 clears identity and renders expired state; 403 clears identity and renders
authorization/CSRF-invalid state. 409 and 503 retain status information for
controlled conflict/unavailable UI handling. Logout clears the browser view even
when server revocation cannot be confirmed; checking the session again is an
explicit action. Rotation refreshes identity/CSRF. Provider login remains
unavailable and has no key-entry/test-login form.

Every refresh invalidates a generation and aborts old requests. Responses and
JSON parsing are checked against their originating generation. The cloud view is
keyed by server owner plus generation, so Library, candidate batches, review
cards, topic attempts and all component caches are discarded on identity change.
Late requests carrying old owner claims are rejected locally. Window focus
rechecks an active session; cross-tab invalidation uses BroadcastChannel and
carries no identity, token or learning data. A cloud failure never mounts the
Personal application or clears/migrates its local storage.

Successful private responses carry a server-authenticated X-Authenticated-User
header. The client verifies it before exposing the response body, catching cookie
account changes even for Library requests without a claimed user_id. Private
requests and authenticated responses use no-store, preventing cross-account
browser cache reuse. A missing or mismatched owner header invalidates the view.

Enabled cloud UI surfaces: Study Bank/AI Chunk Organizer, Daily Review, Topic
Mock and a read-only Library transcript preview. Today joins, Learn/Guided,
Listening, Shadowing, local media, Library curation joins and live Speaking stay
gated because their cloud contracts are not migrated. Existing Personal Desktop
keeps its full UI, local learner identity and original request behavior.

Cloud deployment requires same-origin HTTPS and reviewed ASGI TLS/proxy trust.
The configured API base must resolve to that origin (empty base for standard
production builds). A cross-origin development API may serve Personal Desktop,
but cannot receive cloud credentials. Browser sessions still require separately
approved provider login/account mapping, abuse controls, managed migrations,
backup/restore and operational review. This stage creates no accounts, imports,
provider traffic, paid AI calls or production deployment.
