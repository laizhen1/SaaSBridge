# SaaSBridge support console

The Vue frontend provides the SaaSBridge support console.
It includes chat, request/tool traces, knowledge management, evaluation and
monitoring details. SaaSBridge uses the Python backend.

## Local development

Start the Python backend on port 8000, then:

```powershell
cd D:\SaaSBridge\frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5175. `/api/python` is proxied to http://127.0.0.1:8000.
Use Node.js 22.12+ or a supported newer release. Set the `VITE_PYTHON_API_URL`
environment variable before starting Vite to use a different backend address.

```powershell
npm test
npm run build
```

## Docker

Run the integrated stack from the project root:

```powershell
cd D:\SaaSBridge
docker compose -f compose.yaml -f compose.app.yaml up -d --build
```

The frontend container proxies `/api/python/` to the `saasbridge` backend service.
The root `.env` supplies backend model credentials. Never put model keys in Vite
variables or browser runtime configuration. The frontend connection dialog
supports the backend Bearer token, which is kept out of persisted settings.
