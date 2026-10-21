# Intelligent Speaking Agent

The Intelligent Speaking Agent is a standalone, persistent conversation
workspace. It complements rather than replaces Guided Lesson's Live Dialogue:
Guided Live Dialogue keeps its deterministic Local Text Practice and optional
OpenAI Realtime exercise inside the fixed seven-stage flow, while Speaking is a
top-level, voice-first workflow with its own bounded session history.

## Explicit provider boundary

Gemini Live is the preferred provider and defaults to `gemini-3.8-live`.
OpenAI Realtime is an optional provider selected explicitly by the learner.
Opening the application or Speaking Home does not request a credential, open a
provider connection, or request microphone access. **Start Speaking** first
creates a local session and only then requests the selected provider credential
and microphone. A Gemini error never starts OpenAI automatically.

The non-secret provider-status endpoint reports configuration, model name, and
normalized capabilities. Gemini credentials are minted through
`POST /api/speaking/gemini/client-token`: the backend uses `GEMINI_API_KEY` to
request a one-use credential from the v1beta `auth_tokens` endpoint with a two-minute new-session
window and a twelve-minute absolute expiry, constrained to the configured Live
model. The token's `fieldMask` locks only `model`; response modality, system
instruction, transcription, and tool configuration remain part of the browser's
Live setup. The browser uses the matching v1beta constrained Live WebSocket endpoint.
The long-lived key is never returned or logged. Network calls have a
ten-second timeout, upstream messages are bounded, and any occurrence of the
key is redacted. Missing configuration returns 503; upstream failure returns
502, while a detectable provider quota/rate limit returns 429. The UI reports
credential, WebSocket, microphone-permission, microphone-availability, and
quota failures separately without exposing upstream details. Ephemeral tokens
are neither stored nor sent to the session store.

Set these only in the backend environment:

```text
GEMINI_API_KEY=
GEMINI_LIVE_MODEL=gemini-3.8-live
```

Existing `OPENAI_API_KEY`, `OPENAI_REALTIME_MODEL`, and
`OPENAI_REALTIME_VOICE` settings remain valid. Never put provider keys in a
`VITE_*` variable. Provider billing, quotas, retention, and data-use rules are
controlled by the selected provider account. In particular, Gemini Free Tier
and paid-tier data-use terms may differ; this application makes no provider
privacy promise.

## Practice modes and bounded context

The backend `SpeakingContextBuilder` supports English or German at B1, B2, or
C1 in three modes:

- **Review weak expressions** selects active curated chunks deterministically:
  Again, then Hard, then due reviewed chunks, then new chunks, then other
  active chunks. Lapses, due time, review count, creation time, and ID provide
  stable tie-breaking.
- **Discuss saved media** first selects chunks whose Study Bank provenance
  references the chosen Library item, then fills from the same global weak/due
  ordering. It includes at most twelve transcript sentences centered on the
  current Listening sentence when available.
- **Custom scenario** accepts a bounded free-text goal.

The default target count is five and the hard maximum is eight. Up to five
active words are included only as support vocabulary. A learner may deselect
all suggested targets and hold a normal conversation. No entire Study Bank or
Library transcript is sent. Transcript and curated text are delimited as
untrusted learning data, never executable system instructions.

The shared prompt describes a natural speaking partner first and a selective
coach second. It keeps turns concise, adapts language complexity to the chosen
level, creates natural opportunities for targets, offers scaffolding, and
normally limits corrections to one important issue per learner turn. It never
asks the model to invent a fluency or CEFR score.

## Provider and audio architecture

`SpeakingAgentProvider` normalizes connect/disconnect, mute, optional text,
interruption, learner/agent transcript, tool call, error, and end events.
Speaking UI code does not parse provider wire events. Gemini and OpenAI
adapters expose only capabilities they actually implement.

The Gemini browser adapter connects with the short-lived token, requests
provider input/output transcription, and declares only the allow-listed tools.
WebSocket open/HTTP 101 is only the transport handshake: the adapter becomes
ready and reports Listening only after Gemini returns `setupComplete`. A
ten-second timeout closes connections that never complete setup, and user text
or microphone audio is not sent before readiness.

Gemini server frames may arrive as browser strings, `Blob` objects, or
`ArrayBuffer` objects. The adapter decodes each form to text before JSON
parsing and processes asynchronous binary decoding through one serialized
receive queue so arrival order is preserved. Raw frames are neither logged nor
persisted. Malformed or unsupported messages produce a controlled provider
error; safe close diagnostics distinguish setup failure from a normal
post-setup provider end without exposing credentials.

After explicit voice start it captures mono microphone samples through Web
Audio, downsamples to 16 kHz PCM, and streams them to Live. It requests echo
cancellation, noise suppression, and automatic gain control where supported.
Received 24 kHz PCM is queued in one audio context; provider interruption stops
queued assistant sources immediately. Incremental transcript text is shown
live and accumulated until turn completion. Only final turns are persisted.
Text-only start skips microphone acquisition but still uses the real Gemini
session and realtime agent audio.

The OpenAI adapter reuses the existing Realtime implementation and credential
endpoint with M4.6's selected duration. It does not change Guided Live
Dialogue's normal three-minute/five-turn limits. Its current M4.6 capability is
voice conversation with transcripts, mute, and barge-in; Gemini currently owns
the M4.6 text-input and normalized tool paths. The UI follows reported
capabilities instead of assuming provider parity.

Three-, five-, and ten-minute sessions are allowed; five minutes is the
default, and ten minutes is the hard maximum. The frontend countdown ends the
provider, microphone, audio, and session at the limit. Manual end, provider
disconnect, failure, and page exit use the same cleanup boundary. Microphone
tracks, WebSocket/WebRTC resources, audio contexts, queued output, listeners,
and timers are released.

## Deterministic target evidence

Target usage is application-derived only from finalized **learner**
transcripts. It reuses the Sync Shadowing normalization boundary: Unicode NFKC,
case-folding, punctuation-insensitive tokenization, and whitespace
normalization for English and German. A target counts only when its full
normalized token sequence appears contiguously. Partial phrases, paraphrases,
agent speech, and partial transcript deltas do not count.

Each target stores a text/meaning snapshot, use count, first and last learner
turn, and at most three actual evidence snippets. Support words are not counted
as targets. The review reports used and unused expressions without a composite,
grammar, fluency, pronunciation, or CEFR score. Speaking evidence does not
change Study Bank due dates, Daily Review ratings, Library progress, or Chunk
Memory statistics.

## Tools and security

One central dispatcher recognizes only:

- `get_due_chunks` and `get_saved_words`, bounded to ten active items and the
  session language;
- `get_media_context`, bounded to twelve sentences and the session Library
  item;
- `record_correction`, validated into one of five correction categories;
- `suggest_expression`, validated as a word or chunk suggestion.

Unknown names and invalid schemas are rejected. There is no HTTP, shell,
filesystem, SQL, browser, or generic execution tool. Read tools do not mutate
Study Bank or Library. Corrections are limited to eight per session;
suggestions are limited to eight and do not enter Study Bank automatically.
Tool results return through the originating provider call ID. M4.7 may define a
future cross-system learning policy.

## Local session persistence and privacy

`backend/storage/speaking_sessions.json` is a dedicated schema-version-1
store. It uses a process-local lock, validated reads, a temporary file, and
atomic `os.replace`. Each session snapshots provider/model, language, level,
mode, duration, optional Library ID/title, goal, targets, support words,
finalized turns, corrections, suggestions, target evidence, status, and end
reason. A deleted Library item therefore does not invalidate history.

`event_count` supplies optimistic stale-write protection. Final transcript and
tool events are serialized by the frontend; a mismatched count returns HTTP
409. Duplicate finalized provider turn IDs are idempotent. Limits are forty
final turns, 30,000 transcript characters, eight corrections, eight
suggestions, three evidence snippets per target, and ten recent sessions on
the home page. Raw protocol payloads, hidden model state, partial transcripts,
binary audio, keys, and ephemeral tokens are never persisted.

During an active voice session, raw microphone audio is streamed to the
explicitly selected external provider. It is not written to local storage or
the session JSON. Assistant audio is also not persisted. The local record
contains only finalized user-visible transcripts and bounded structured
learning data.

## Failure and product boundaries

Missing configuration, token/quota/network failures, microphone denial,
provider disconnect, unavailable transcription, invalid tools, persistence
failure, and stale events are surfaced without changing providers silently.
Text input is an explicit real-provider fallback where reported; it never
switches to deterministic Guided Local Text Practice. An empty Study Bank can
start a target-free natural conversation.

M4.6 does not provide offline intelligent voice, semantic target matching,
pronunciation assessment, CEFR assessment, raw audio history, unlimited
transcripts, cloud sync, web search, automatic SRS ratings, or automatic Study
Bank insertion. Provider transcription can be wrong and device latency varies.

## Required real-Mac smoke after merge

Cloud tests use fake providers and do not exercise real credentials, provider
networks, or a microphone. After merge:

1. Update the Personal Desktop app.
2. Put `GEMINI_API_KEY` only in `backend/.env`.
3. Open `Language AI.app`; confirm **Speaking** appears.
4. Open Speaking; confirm Gemini Live is selected.
5. Choose German B2 and Review weak expressions.
6. Confirm three to five real curated chunks appear.
7. Click Start Speaking and then allow microphone access.
8. Confirm the realtime connection, agent audio, and both transcripts.
9. Interrupt agent audio and confirm it stops and listens.
10. Use one target; confirm it becomes Used only after learner transcript
    evidence. Avoid another and confirm it stays unused.
11. Make one obvious error and confirm corrections are selective.
12. End the session; verify learner turns, used/unused targets, corrections,
    and suggestions.
13. Reopen Speaking; confirm recent session/detail survives without raw audio.
14. Discuss saved Paris German media; verify bounded real media context and
    primarily German conversation.
15. Try a custom scenario and, if practical, the three-minute automatic limit.
16. Close/reopen and confirm the summary persists.
17. If OpenAI is configured, select it explicitly for one optional short smoke;
    verify a Gemini failure never starts it.
18. Regression-check Listening, Shadowing, Daily Review, Words & Chunks, Guided
    Lesson, and Guided Live Dialogue.

Do not mark M4.6 verified until this real-device smoke completes.
