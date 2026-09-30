import json
import os

import httpx
import streamlit as st


st.set_page_config(page_title="FastAPI Ask Tester", page_icon="💬", layout="centered")
st.title("FastAPI Ask Tester")
st.caption("Send a question to your local FastAPI service and inspect its JSON response.")

with st.sidebar:
    st.header("API settings")
    default_api_url = os.getenv("API_BASE_URL")
    if not default_api_url:
        service_hostport = os.getenv("API_SERVICE_HOSTPORT", "127.0.0.1:8000")
        default_api_url = f"http://{service_hostport}"
    api_url = st.text_input(
        "FastAPI base URL",
        value=default_api_url,
    ).rstrip("/")
    model = st.text_input("Model", value="openai/gpt-4o-mini")

    if st.button("Check API health", use_container_width=True):
        try:
            response = httpx.get(f"{api_url}/health", timeout=5.0)
            response.raise_for_status()
            st.success("FastAPI is healthy")
        except httpx.HTTPError as exc:
            st.error(f"Could not reach FastAPI: {exc}")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and isinstance(message["content"], dict):
            st.json(message["content"])
        else:
            st.markdown(message["content"])

prompt = st.chat_input("Ask a question")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Waiting for the FastAPI response..."):
            partial = st.empty()
            streamed_text = ""
            result = None
            try:
                with httpx.stream(
                    "GET",
                    f"{api_url}/ask/stream",
                    params={"question": prompt, "model": model},
                    timeout=90.0,
                ) as response:
                    response.raise_for_status()
                    current_event = "message"
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            current_event = line.removeprefix("event:").strip()
                        elif line.startswith("data:"):
                            payload = json.loads(line.removeprefix("data:").strip())
                            if current_event == "delta":
                                streamed_text += payload.get("text", "")
                                partial.code(
                                    streamed_text,
                                    language="json",
                                )
                            elif current_event == "complete":
                                result = payload
                if result is None:
                    raise ValueError("Stream ended before a complete response arrived.")
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get("detail", exc.response.text)
                except ValueError:
                    detail = exc.response.text
                result = {"error": f"API returned {exc.response.status_code}: {detail}"}
            except (httpx.HTTPError, ValueError) as exc:
                result = {"error": f"Request failed: {exc}"}

            partial.empty()
        if "error" in result:
            st.error(result["error"])
        else:
            st.json(result)
    st.session_state.messages.append({"role": "assistant", "content": result})