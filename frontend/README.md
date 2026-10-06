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
