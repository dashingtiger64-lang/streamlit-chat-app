import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
from langchain_core.prompts import PromptTemplate

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "chat_memory.db"

DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
DEFAULT_HF_MODEL = "HuggingFaceH4/zephyr-7b-beta"

st.set_page_config(page_title="API Fallback Chat", page_icon="💬", layout="centered")


# -------------------- SECRETS --------------------
def get_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    return value or default


# -------------------- DATABASE --------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                provider TEXT,
                created_at TEXT
            )
        """)
        conn.commit()


def save_message(session_id, role, content, provider=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO messages (session_id, role, content, provider, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, role, content, provider, datetime.utcnow().isoformat()))
        conn.commit()


def load_messages(session_id, limit=40):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT role, content, provider
            FROM messages
            WHERE session_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (session_id, limit)).fetchall()

    return [
        {"role": r, "content": c, "provider": p or ""}
        for r, c, p in reversed(rows)
    ]


def clear_messages(session_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.commit()


# -------------------- GROQ --------------------
def groq_chat(messages, model):
    api_key = get_secret("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY")

    clean_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": model,
            "messages": clean_messages,
            "temperature": 0.7,
            "max_tokens": 900
        },
        timeout=45
    )

    if res.status_code != 200:
        raise RuntimeError(f"Groq error {res.status_code}: {res.text[:300]}")

    return res.json()["choices"][0]["message"]["content"]


# -------------------- HUGGING FACE (YOUR ORIGINAL WORKING LOGIC) --------------------
def huggingface_chat(messages, model):
    api_key = get_secret("HUGGINGFACEHUB_API_TOKEN")
    if not api_key:
        raise RuntimeError("Missing HF token")

    prompt = ""
    for m in messages:
        prompt += f"{m['role']}: {m['content']}\n"
    prompt += "assistant:"

    res = requests.post(
        f"https://api-inference.huggingface.co/models/{model}",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "inputs": prompt,
            "parameters": {"max_new_tokens": 500, "temperature": 0.7}
        },
        timeout=60
    )

    if res.status_code != 200:
        raise RuntimeError(f"HF error {res.status_code}: {res.text[:300]}")

    data = res.json()
    if isinstance(data, list):
        return data[0].get("generated_text", "")
    return str(data)


# -------------------- FALLBACK --------------------
def ask_with_fallback(messages, providers, groq_model, hf_model):
    errors = []

    for p in providers:
        try:
            if p == "Groq":
                return groq_chat(messages, groq_model), "Groq", errors

            if p == "Hugging Face":
                return huggingface_chat(messages, hf_model), "Hugging Face", errors

        except Exception as e:
            errors.append(f"{p}: {e}")
            time.sleep(0.3)

    raise RuntimeError("All providers failed:\n\n" + "\n\n".join(errors))


# -------------------- LOGIN --------------------
def authenticate():
    users = st.secrets.get("users", {})

    if st.session_state.get("auth"):
        return True

    st.title("API Fallback Chat")

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")

    if ok:
        if username in users and users[username] == password:
            st.session_state.auth = True
            st.session_state.session_id = username
            st.success("Login successful 🎉")
            st.rerun()
        else:
            st.error("Wrong username or password")

    return False


# -------------------- PROMPT PLAYGROUND (NEW ADDITION) --------------------
def prompt_playground(hf_model):

    st.title("🧪 Prompt Playground")

    topic = st.text_input("Topic", "Python Interview")
    level = st.selectbox("Level", ["easy", "moderate", "hard"])

    template = st.text_area(
        "Prompt Template",
        "Create 5 questions for {topic} suitable for {level} students."
    )

    api_choice = st.selectbox("Choose Model", ["Groq", "Hugging Face"])

    if st.button("Generate"):

        prompt = PromptTemplate(
            input_variables=["topic", "level"],
            template=template
        ).format(topic=topic, level=level)

        try:
            if api_choice == "Groq":
                result = groq_chat(
                    [{"role": "user", "content": prompt}],
                    DEFAULT_GROQ_MODEL
                )
            else:
                result = huggingface_chat(
                    [{"role": "user", "content": prompt}],
                    hf_model
                )

            st.success("Generated")
            st.text_area("Output", result, height=300)

        except Exception as e:
            st.error(str(e))


# -------------------- MAIN --------------------
def main():
    init_db()

    if not authenticate():
        return

    session_id = st.session_state.session_id

    st.title("API Fallback Chat")

    with st.sidebar:

        page = st.radio("Navigation", ["Chat", "Prompt Playground"])

        first = st.selectbox("First API", ["Groq", "Hugging Face"])
        fallback = "Hugging Face" if first == "Groq" else "Groq"

        groq_model = st.text_input("Groq model", DEFAULT_GROQ_MODEL)
        hf_model = st.text_input("HF model", DEFAULT_HF_MODEL)

        limit = st.slider("Memory size", 6, 60, 20)

        if st.button("Clear chat"):
            clear_messages(session_id)
            st.rerun()

        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

    # ---------------- CHAT ----------------
    if page == "Chat":

        history = load_messages(session_id, limit)

        for m in history:
            with st.chat_message(m["role"]):
                st.write(m["content"])

        prompt = st.chat_input("Type message...")

        if not prompt:
            return

        save_message(session_id, "user", prompt)

        fresh = load_messages(session_id, limit)
        order = [first, fallback]

        with st.chat_message("assistant"):
            try:
                ans, provider, errors = ask_with_fallback(
                    fresh, order, groq_model, hf_model
                )
            except Exception as e:
                st.error(str(e))
                return

            st.write(ans)
            st.caption(f"Answered by {provider}")

        save_message(session_id, "assistant", ans, provider)

    # ---------------- PLAYGROUND ----------------
    if page == "Prompt Playground":
        prompt_playground(hf_model)


if __name__ == "__main__":
    main()
