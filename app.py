import sqlite3
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "chat_memory.db"

GROQ_MODEL = "llama-3.1-8b-instant"

st.set_page_config(page_title="Groq Chat", page_icon="💬", layout="centered")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_message(session_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
    """, (session_id, role, content, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def load_messages(session_id, limit=20):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT role, content FROM messages
        WHERE session_id=?
        ORDER BY id DESC
        LIMIT ?
    """, (session_id, limit)).fetchall()
    conn.close()

    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]


def clear_messages(session_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


def groq_chat(messages):
    api_key = st.secrets["GROQ_API_KEY"]

    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 800
        },
        timeout=60
    )

    if res.status_code != 200:
        raise Exception(res.text)

    return res.json()["choices"][0]["message"]["content"]


def login():
    users = st.secrets.get("users", {})

    if st.session_state.get("logged_in"):
        return True

    st.title("Login")

    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        if u in users and users[u] == p:
            st.session_state.logged_in = True
            st.session_state.session_id = u
            st.rerun()
        else:
            st.error("Wrong login")

    return False


def main():
    init_db()

    if not login():
        return

    session_id = st.session_state.session_id

    st.title("💬 Groq Chat")

    with st.sidebar:
        limit = st.slider("Memory", 5, 50, 20)

        if st.button("Clear Chat"):
            clear_messages(session_id)
            st.rerun()

        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

    history = load_messages(session_id, limit)

    for m in history:
        with st.chat_message(m["role"]):
            st.write(m["content"])

    prompt = st.chat_input("Type message...")

    if prompt:
        save_message(session_id, "user", prompt)

        with st.chat_message("user"):
            st.write(prompt)

        messages = load_messages(session_id, limit)

        with st.chat_message("assistant"):
            try:
                reply = groq_chat(messages)
                st.write(reply)
                save_message(session_id, "assistant", reply)
            except Exception as e:
                st.error(str(e))


if __name__ == "__main__":
    main()
