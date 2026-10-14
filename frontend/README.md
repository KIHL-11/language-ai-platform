# Language AI Platform Frontend

React and TypeScript frontend powered by Vite and Tailwind CSS.

Live Dialogue uses the deterministic local text provider by default. To make
Realtime Voice Practice available, set `VITE_LIVE_DIALOGUE_PROVIDER=realtime`
and configure `OPENAI_API_KEY`, `OPENAI_REALTIME_MODEL`, and
`OPENAI_REALTIME_VOICE` in the backend environment. The standard OpenAI API key
must never be placed in a `VITE_*` variable.

```bash
npm install
npm run dev
```

Production validation:

```bash
npm run lint
npm run build
```

## Local learner memory

The browser stores one random opaque learner identifier in local storage under
`language_ai_learner_id_v1`. It is a persistence key for lesson chunk memory,
not an account, login, credential, or cross-device identity. It contains no
personal information and is sent only to this application's backend when a
lesson saves chunks or Active Recall reads and rates earlier chunks. Clearing
site data creates a new local learner identity and does not affect lesson use.
