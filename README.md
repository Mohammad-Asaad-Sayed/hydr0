# HydraMem (minimal scaffold)


This repository focuses on HydraDB-driven memory retrieval and a deterministic temporal resolver.

HydraDB integration

Get your API key from [https://app.hydradb.com](https://app.hydradb.com), then:

```bash
export HYDRA_DB_API_KEY=your_api_key
```

Run the demo (ingests facts, compares Graph OFF vs ON):

```bash
python3 scripts/run_demo.py
```

Run integration checks (skips when `HYDRA_DB_API_KEY` is not set):

```bash
PYTHONPATH=src:. python3 tests/run_integration_tests.py
```

Quickstart (unit tests only, no HydraDB needed):

```bash
PYTHONPATH=src:. python3 tests/run_unit_tests.py
```

Run the API server (development):

```bash
pip install -r requirements.txt
uvicorn src.hydramem.api.app:app --reload --port 8000
```

Example API call (from another terminal while server is running):

```bash
curl -X POST http://localhost:8000/memory/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Where does the user live?",
    "graph_context": true
  }'
```

API endpoints:

- `GET /memory/health` — returns `{"status": "ok", "ok": true}`
- `POST /memory/ingest` — submit facts to HydraDB
- `POST /memory/query` — query with Graph ON/OFF comparison

Example query request:

```json
{
  "query": "Where does the user live?",
  "database": "default",
  "collection": "default",
  "type": "memory",
  "graph_context": true
}
```
