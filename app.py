import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st
import requests

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "chat_memory.db"

GROQ_MODEL = "llama-3.1-8b-instant"

st.set_page_config(
    page_title="Harshit Chat Bot",
    page_icon="🤖",
    layout="centered"
)


# ---------------- DATABASE ----------------
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


# ---------------- GROQ ----------------
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
            "max_tokens": 900
        },
        timeout=60
    )

    if res.status_code != 200:
        raise Exception(res.text)

    return res.json()["choices"][0]["message"]["content"]


# ---------------- LOGIN ----------------
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
            st.error("Wrong credentials")

    return False


# ---------------- PROMPT TEMPLATES ----------------
def build_prompt(mode, user_input):
    if mode == "Question Generator":
        return f"""
You are a question generator.

Create 5 questions for:
Topic: {user_input.get('subject')}
Level: {user_input.get('level')}
"""

    elif mode == "Coding Assistant":
        return f"""
You are a python expert programmer.

Write code only, no explanation.

Task:
{user_input.get('task')}
"""

    return user_input.get("task")


# ---------------- MAIN ----------------
def main():
    init_db()

    if not login():
        return

    session_id = st.session_state.session_id

    st.title("🤖 Harshit Chat Bot")

    with st.sidebar:
        mode = st.selectbox(
            "Choose Mode",
            ["Normal Chat", "Question Generator", "Coding Assistant"]
        )

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

    # ---------------- INPUT UI ----------------
    if mode == "Question Generator":
        subject = st.text_input("Subject")
        level = st.selectbox("Level", ["easy", "moderate", "hard"])
        user_input = {"subject": subject, "level": level}

    elif mode == "Coding Assistant":
        task = st.text_area("Programming Task")
        user_input = {"task": task}

    else:
        user_input = {"task": st.chat_input("Type message...")}

    if not user_input or (mode == "Normal Chat" and not user_input["task"]):
        return

    # build final prompt
    final_prompt = build_prompt(mode, user_input)

    save_message(session_id, "user", final_prompt)

    with st.chat_message("user"):
        st.write(final_prompt)

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
