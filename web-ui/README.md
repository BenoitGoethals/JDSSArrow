# JDSSArrow Web UI

React + TypeScript (Vite) dashboard for the JDSSArrow backend: AEP-76 volume map, pluggable
configuration view, live status, message injection, and a WebSocket live message feed.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api + /ws to the FastAPI backend on :8000
```

Run the backend first: `uvicorn jdssarrow.web.app:app` from the repo root.
