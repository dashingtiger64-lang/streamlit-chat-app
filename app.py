import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st
import requests

# ================= CONFIG =================
APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "chat_memory.db"

GROQ_MODEL = "llama-3.1-8b-instant"

st.set_page_config(
    page_title="Harshit Chat Bot",
    page_icon="🤖",
    layout="centered"
)

# ================= CSS FIX (IMPORTANT) =================
st.markdown("""
<style>
.block-container {
    padding-bottom: 120px;
    padding-top: 20px;
}

/* fix input bottom */
.stChatInputContainer {
    position: fixed;
    bottom: 20px;
    width: 50%;
    left: 50%;
    transform: translateX(-50%);
    z-index: 999;
}
</style>
""", unsafe_allow_html=True)


# ================= DATABASE =================
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


# ================= GROQ API =================
def groq_chat(messages):
    api_key = st.secrets["GROQ_API_KEY"]

    response = requests.post(
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

    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


# ================= LOGIN =================
def login():
    users = st.secrets.get("users", {})

    if st.session_state.get("logged_in"):
        return True

    st.title("🔐 Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username in users and users[username] == password:
            st.session_state.logged_in = True
            st.session_state.session_id = username
            st.rerun()
        else:
            st.error("Invalid credentials")

    return False


# ================= PROMPTS =================
def build_prompt(mode, data):
    if mode == "Question Generator":
        return f"""
You are a question generator.

Create 5 questions.

Topic: {data['subject']}
Difficulty: {data['level']}
"""

    if mode == "Coding Assistant":
        return f"""
You are a senior Python developer.

Return ONLY code.

Task:
{data['task']}
"""

    return data["task"]


# ================= MAIN APP =================
def main():
    init_db()

    if not login():
        return

    session_id = st.session_state.session_id

    st.title("🤖 Harshit Chat Bot")

    # ================= SIDEBAR =================
    with st.sidebar:
        st.header("⚙️ Settings")

        limit = st.slider("Memory Size", 5, 50, 20)

        if st.button("🗑 Clear Chat"):
            clear_messages(session_id)
            st.rerun()

        if st.button("🚪 Logout"):
            st.session_state.clear()
            st.rerun()

    # ================= LOAD HISTORY =================
    history = load_messages(session_id, limit)

    if not history:
        st.info("Start chatting with your AI assistant 🚀")

    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ================= TABS =================
    tab1, tab2, tab3 = st.tabs([
        "💬 Chat",
        "❓ Question Generator",
        "💻 Coding Assistant"
    ])

    user_prompt = None
    mode = None

    # ================= TAB 1 (CHAT VIEW ONLY) =================
    with tab1:
        st.write("Chat mode active. Type below 👇")
        mode = "Chat"

    # ================= TAB 2 =================
    with tab2:
        with st.form("q_form"):
            subject = st.text_input("Subject")
            level = st.selectbox("Difficulty", ["Easy", "Moderate", "Hard"])
            submit = st.form_submit_button("Generate Questions")

        if submit:
            user_prompt = build_prompt("Question Generator", {
                "subject": subject,
                "level": level
            })
            mode = "Question Generator"

    # ================= TAB 3 =================
    with tab3:
        with st.form("c_form"):
            task = st.text_area("Programming Task", height=150)
            submit2 = st.form_submit_button("Generate Code")

        if submit2:
            user_prompt = build_prompt("Coding Assistant", {
                "task": task
            })
            mode = "Coding Assistant"

    # ================= GLOBAL CHAT INPUT (IMPORTANT FIX) =================
    user_input = st.chat_input("Type your message...")

    # If normal chat mode input
    if user_input and tab1:
        user_prompt = user_input
        mode = "Chat"

    # ================= PROCESS INPUT =================
    if not user_prompt:
        return

    user_prompt = user_prompt.strip()
    if not user_prompt:
        return

    save_message(session_id, "user", user_prompt)

    with st.chat_message("user"):
        st.markdown(user_prompt)

    messages = load_messages(session_id, limit)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking... 🤔"):
                reply = groq_chat(messages)

            st.markdown(reply)
            save_message(session_id, "assistant", reply)

        except requests.exceptions.Timeout:
            st.error("Request timed out.")

        except requests.exceptions.ConnectionError:
            st.error("Network error.")

        except Exception as e:
            st.error(f"Error: {str(e)}")


if __name__ == "__main__":
    main()
