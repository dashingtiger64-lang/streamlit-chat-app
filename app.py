import sqlite3
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

from dotenv import load_dotenv
load_dotenv()

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "chat_memory.db"

DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"

st.set_page_config(page_title="AI Multi System", page_icon="💬", layout="centered")


# -------------------- SECRETS --------------------
def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default) or default
    except:
        return default


# -------------------- DB --------------------
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

        conn.execute("""
        CREATE TABLE IF NOT EXISTS playground_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            created_at TEXT
        )
        """)

        conn.commit()


# -------------------- CHAT MEMORY --------------------
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

    return [{"role": r, "content": c, "provider": p or ""} for r, c, p in reversed(rows)]


def clear_messages(session_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.commit()


# -------------------- PLAYGROUND MEMORY --------------------
def save_playground_message(session_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO playground_messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        """, (session_id, role, content, datetime.utcnow().isoformat()))
        conn.commit()


def load_playground_messages(session_id, limit=25):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
        SELECT role, content
        FROM playground_messages
        WHERE session_id=?
        ORDER BY id DESC
        LIMIT ?
        """, (session_id, limit)).fetchall()

    return [{"role": r, "content": c} for r, c in reversed(rows)]


def clear_playground(session_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM playground_messages WHERE session_id=?", (session_id,))
        conn.commit()


# -------------------- GROQ --------------------
def groq_chat(messages, model):
    api_key = get_secret("GROQ_API_KEY")

    clean = [{"role": m["role"], "content": m["content"]} for m in messages]

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": clean, "temperature": 0.7},
        timeout=45
    )

    return response.json()["choices"][0]["message"]["content"]


# -------------------- HF (SAFE DIRECT API) --------------------
def hf_generate(prompt_text):

    api_key = get_secret("HUGGINGFACEHUB_API_TOKEN")

    model = "mistralai/Mistral-7B-Instruct-v0.2"

    response = requests.post(
        f"https://api-inference.huggingface.co/models/{model}",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "inputs": prompt_text,
            "parameters": {
                "max_new_tokens": 500,
                "temperature": 0.7
            }
        }
    )

    data = response.json()

    if isinstance(data, list):
        return data[0].get("generated_text", "")

    return str(data)


# -------------------- FALLBACK --------------------
def ask_with_fallback(messages, order, groq_model):
    for p in order:
        try:
            if p == "Groq":
                return groq_chat(messages, groq_model), "Groq"
        except:
            continue
    return "All failed", "None"


# -------------------- AUTH --------------------
def auth():
    users = st.secrets.get("users", {})

    if st.session_state.get("auth"):
        return True

    st.title("AI Multi System")

    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")

    if ok:
        if u in users and users[u] == p:
            st.session_state.auth = True
            st.session_state.session_id = u
            st.rerun()
        else:
            st.error("Wrong login")

    return False


# -------------------- PLAYGROUND --------------------
def playground():

    st.header("🧠 Unified Playground")

    mode = st.radio(
        "Mode",
        ["🧪 Prompt Generator", "💬 Chat AI"],
        horizontal=True
    )

    system_prompt = st.text_area(
        "System Prompt",
        "You are a helpful AI tutor for BCA and BTech students."
    )

    # ---------------- PROMPT MODE ----------------
    if mode == "🧪 Prompt Generator":

        topic = st.text_input("Topic", "Python Interview")
        level = st.selectbox("Level", ["easy", "moderate", "hard"])

        template = st.text_area(
            "Template",
            "Create Python questions for {topic} suitable for {level} students."
        )

        if st.button("Generate"):

            prompt = template.format(topic=topic, level=level)

            result = hf_generate(prompt)

            st.success("Generated")
            st.text_area("Output", result, height=250)

    # ---------------- CHAT MODE ----------------
    if mode == "💬 Chat AI":

        if st.button("Clear Chat"):
            clear_playground(st.session_state.session_id)
            st.rerun()

        msgs = load_playground_messages(st.session_state.session_id)

        for m in msgs:
            with st.chat_message(m["role"]):
                st.write(m["content"])

        user = st.chat_input("Ask something...")

        if not user:
            return

        save_playground_message(st.session_state.session_id, "user", user)

        history = load_playground_messages(st.session_state.session_id)

        text = system_prompt + "\n\n"

        for m in history:
            text += f"{m['role']}: {m['content']}\n"

        text += "assistant:"

        response = hf_generate(text)

        save_playground_message(st.session_state.session_id, "assistant", response)

        with st.chat_message("assistant"):
            st.write(response)


# -------------------- MAIN --------------------
def main():

    init_db()

    if not auth():
        return

    sid = st.session_state.session_id

    st.title("AI Multi System 🚀")

    with st.sidebar:

        page = st.radio("Navigation", ["Chat", "Playground"])

        groq_model = st.text_input("Groq model", DEFAULT_GROQ_MODEL)

        if st.button("Clear Chat"):
            clear_messages(sid)
            st.rerun()

        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

    # ---------------- CHAT ----------------
    if page == "Chat":

        history = load_messages(sid)

        for m in history:
            with st.chat_message(m["role"]):
                st.write(m["content"])

        msg = st.chat_input("Type message...")

        if not msg:
            return

        save_message(sid, "user", msg)

        ans, provider = ask_with_fallback(
            load_messages(sid),
            ["Groq"],
            groq_model
        )

        save_message(sid, "assistant", ans, provider)

        with st.chat_message("assistant"):
            st.write(ans)
            st.caption(provider)

    # ---------------- PLAYGROUND ----------------
    if page == "Playground":
        playground()


if __name__ == "__main__":
    main()
