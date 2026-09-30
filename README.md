# OpenRouter Ask

A FastAPI app and lightweight web UI that streams questions to OpenRouter.

## Setup

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Put your OpenRouter API key in the root `.env` file by replacing `your_openrouter_api_key_here`. The `.env` file is ignored by Git.
4. Start the API with `uvicorn app.main:app --reload`.
5. Open `http://127.0.0.1:8000` for the web UI.

The default model is `openai/gpt-4o-mini`; set `OPENROUTER_MODEL` in `.env` to change it. Both `/chat` and `/ask` accept a model override.

## Endpoints

- `GET /` serves the web UI.
- `GET /api` returns API service information.
- `GET /health` returns `{"status":"ok"}`.
- `GET /ask?question=...` returns `answer`, `sources`, `confidence`, `model`, and `tokens_used`.
- `GET /ask/stream?question=...` streams generated JSON chunks as server-sent events and ends with a `complete` event containing the same response shape.
- `POST /chat` accepts `{"message":"Hello"}` and returns the selected model and reply.
- `GET /docs` opens the interactive API documentation.

The web UI checks `/health`, lets you select a model, streams question responses from `/ask/stream`, and presents the final response fields. `sources` is currently empty because no retrieval source is connected. `confidence` is the model's self-assessment, not a calibrated probability.

Example request:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Hello"}'
```

Run the test with `pytest`.

## Docker

With Docker Engine and the Compose plugin installed, build and start both services from the project root:

```bash
docker compose up --build
```

Compose reads `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` from the root `.env` file and passes them to the API container at runtime. `.env` is excluded from the Docker build context and is not copied into the image.

- Web UI: `http://localhost:8000`
- FastAPI docs: `http://localhost:8000/docs`

Stop the services with `docker compose down`.

## Vercel

Vercel detects the FastAPI application in `app/main.py` and deploys it as a Python Function. The root URL serves the web UI and `/docs` opens the API documentation. `vercel.json` includes the HTML asset in the function bundle and allows time for streamed responses. Add `OPENROUTER_API_KEY` and, optionally, `OPENROUTER_MODEL` under the Vercel project's Environment Variables, then redeploy. Do not commit `.env`.

## Render

The root `render.yaml` defines one web service that serves both the UI and API. Push this project to a Git provider connected to Render, then sync the Blueprint. Add `OPENROUTER_API_KEY` as a secret environment variable in Render. The service's public URL opens the UI; `/docs` opens API documentation.
