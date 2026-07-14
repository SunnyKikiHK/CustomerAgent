# Customer Success Agent — Frontend

Vite + React UI with two views:

- **Chat** — talks to the conversation agent (`POST /chat/turn`).
- **Signal Dashboard** — lists customers and signals, and runs the detector scan
  (`POST /signals/scan`).

## Run (dev)

The API gateway must be running on `http://localhost:8000` (via Docker Compose or
`uvicorn apps.api_gateway.src.app:app`).

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Vite proxies `/api/*` to the gateway (see `vite.config.js`; override the target
with `VITE_API_TARGET`).

The tenant/customer default to the demo IDs seeded by
`scripts/seed_playbooks.py`. Change the Tenant ID field to point at another tenant.
