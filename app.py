import sqlite3
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

APP_DIR = Path(**file**).parent
DB_PATH = APP_DIR / "chat_memory.db"

DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"

st.set_page_config(
page_title="API Chat",
page_icon="💬",
layout="centered"
)

def get_secret(name: str, default: str = "") -> str:
try:
value = st.secrets.get(name, default)
except Exception:
value = default
return value or default

def init_db():
with sqlite3.connect(DB_PATH) as conn:
conn.execute(
"""
CREATE TABLE IF NOT EXISTS messages (
id INTEGER PRIMARY KEY AUTOINCREMENT,
session_id TEXT,
role TEXT,
content TEXT,
provider TEXT,
created_at TEXT
)
"""
)
conn.commit()

def save_message(session_id, role, content, provider=""):
with sqlite3.connect(DB_PATH) as conn:
conn.execute(
"""
INSERT INTO messages
(session_id, role, content, provider, created_at)
VALUES (?, ?, ?, ?, ?)
""",
(
session_id,
role,
content,
provider,
datetime.utcnow().isoformat(),
),
)
conn.commit()

def load_messages(session_id, limit=40):
with sqlite3.connect(DB_PATH) as conn:
rows = conn.execute(
"""
SELECT role, content, provider
FROM messages
WHERE session_id=?
ORDER BY id DESC
LIMIT ?
""",
(session_id, limit),
).fetchall()

```
return [
    {
        "role": role,
        "content": content,
        "provider": provider or "",
    }
    for role, content, provider in reversed(rows)
]
```

def clear_messages(session_id):
with sqlite3.connect(DB_PATH) as conn:
conn.execute(
"DELETE FROM messages WHERE session_id=?",
(session_id,),
)
conn.commit()

def groq_chat(messages):
api_key = get_secret("GROQ_API_KEY")

```
if not api_key:
    raise RuntimeError("Missing GROQ_API_KEY in secrets.toml")

clean_messages = [
    {
        "role": m["role"],
        "content": m["content"],
    }
    for m in messages
]

response = requests.post(
    "https://api.groq.com/openai/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": DEFAULT_GROQ_MODEL,
        "messages": clean_messages,
        "temperature": 0.7,
        "max_tokens": 900,
    },
    timeout=45,
)

if response.status_code != 200:
    raise RuntimeError(
        f"Groq Error {response.status_code}\n\n{response.text}"
    )

data = response.json()
return data["choices"][0]["message"]["content"]
```

def authenticate():
users = st.secrets.get("users", {})

```
if st.session_state.get("auth"):
    return True

st.title("API Chat")

with st.form("login_form"):
    username = st.text_input("Username")
    password = st.text_input(
        "Password",
        type="password",
    )

    login_button = st.form_submit_button("Login")

if login_button:
    if username in users and users[username] == password:
        st.session_state.auth = True
        st.session_state.session_id = username
        st.success("Login successful")
        st.rerun()
    else:
        st.error("Invalid username or password")

return False
```

def main():
init_db()

```
if not authenticate():
    return

session_id = st.session_state.session_id

st.title("💬 API Chat")

with st.sidebar:
    st.subheader("Settings")

    limit = st.slider(
        "Memory Size",
        min_value=6,
        max_value=60,
        value=20,
    )

    if st.button("Clear Chat"):
        clear_messages(session_id)
        st.rerun()

    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

history = load_messages(session_id, limit)

for msg in history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

prompt = st.chat_input("Type your message...")

if not prompt:
    return

save_message(
    session_id,
    "user",
    prompt,
    "user",
)

with st.chat_message("user"):
    st.write(prompt)

messages = load_messages(session_id, limit)

with st.chat_message("assistant"):
    try:
        answer = groq_chat(messages)

        st.write(answer)
        st.caption(
            f"Powered by Groq | {DEFAULT_GROQ_MODEL}"
        )

        save_message(
            session_id,
            "assistant",
            answer,
            "Groq",
        )

    except Exception as e:
        st.error(str(e))

        save_message(
            session_id,
            "assistant",
            str(e),
            "system",
        )
```

if **name** == "**main**":
main()
