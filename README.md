# OpenRouter FastAPI

A small FastAPI service that forwards chat requests to OpenRouter.

## Setup

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Put your OpenRouter API key in the root `.env` file by replacing `your_openrouter_api_key_here`. The `.env` file is ignored by Git.
4. Start the API with `uvicorn app.main:app --reload`.
5. In a second terminal, start the test UI with `streamlit run streamlit_app.py`.

The default model is `openai/gpt-4o-mini`; set `OPENROUTER_MODEL` in `.env` to change it. Both `/chat` and `/ask` accept a model override.

## Endpoints

- `GET /health` returns `{"status":"ok"}`.
- `GET /ask?question=...` returns `answer`, `sources`, `confidence`, `model`, and `tokens_used`.
- `GET /ask/stream?question=...` streams generated JSON chunks as server-sent events and ends with a `complete` event containing the same response shape.
- `POST /chat` accepts `{"message":"Hello"}` and returns the selected model and reply.
- `GET /docs` opens the interactive API documentation.

The Streamlit UI lets you set the FastAPI base URL and model, check `/health`, and send questions to `/ask/stream`; it previews incoming chunks and then displays the completed schema response as JSON. `sources` is currently empty because no retrieval source is connected. `confidence` is the model's self-assessment, not a calibrated probability. Keep the FastAPI server running while using the UI.

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

- Streamlit: `http://localhost:8501`
- FastAPI docs: `http://localhost:8000/docs`

Stop the services with `docker compose down`.

## Vercel

Vercel detects the FastAPI application in `app/main.py` and deploys the API as a Vercel Function. The root URL returns API information, and `/docs` opens the API documentation. Add `OPENROUTER_API_KEY` and, optionally, `OPENROUTER_MODEL` under the Vercel project's Environment Variables, then redeploy. Do not commit `.env`.

Vercel does not run this Streamlit UI as a persistent server. Deploy the UI separately with the included Render Blueprint, or replace it with a frontend supported by Vercel.

## Render

The root `render.yaml` defines separate API and Streamlit web services. Push this project to a Git provider connected to Render, then choose **New + > Blueprint** and select that repository. During Blueprint creation, enter `OPENROUTER_API_KEY` when Render prompts for the secret. Render will assign public `onrender.com` URLs after deployment; the Streamlit service connects to the API over Render's private network.
