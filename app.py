import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
from langchain_core.prompts import PromptTemplate
from dotenv import load_dotenv

load_dotenv()

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "chat_memory.db"

# ── Default models ──────────────────────────────────────────────────────────
DEFAULT_GROQ_MODEL    = "llama-3.1-8b-instant"
DEFAULT_GROQ_MODEL_2  = "mixtral-8x7b-32768"          # second Groq fallback
DEFAULT_HF_MODEL      = "mistralai/Mistral-7B-Instruct-v0.2"

PROVIDER_ORDER_DEFAULT = ["Groq (Primary)", "Groq (Fallback)", "HuggingFace"]

st.set_page_config(page_title="AI Chat + Playground", page_icon="💬", layout="centered")


# ╔══════════════════════════════════════════════════════════════╗
# ║                        SECRETS                               ║
# ╚══════════════════════════════════════════════════════════════╝
def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default) or default
    except Exception:
        return default


# ╔══════════════════════════════════════════════════════════════╗
# ║                        DATABASE                              ║
# ╚══════════════════════════════════════════════════════════════╝
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, role TEXT, content TEXT,
                provider TEXT, created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playground_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, role TEXT, content TEXT, created_at TEXT
            )
        """)
        conn.commit()


def save_message(session_id, role, content, provider=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (session_id,role,content,provider,created_at) VALUES (?,?,?,?,?)",
            (session_id, role, content, provider, datetime.utcnow().isoformat())
        )
        conn.commit()


def load_messages(session_id, limit=40):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT role,content,provider FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
    return [{"role": r, "content": c, "provider": p or ""} for r, c, p in reversed(rows)]


def clear_messages(session_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.commit()


def save_playground_message(session_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO playground_messages (session_id,role,content,created_at) VALUES (?,?,?,?)",
            (session_id, role, content, datetime.utcnow().isoformat())
        )
        conn.commit()


def load_playground_messages(session_id, limit=25):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT role,content FROM playground_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def clear_playground(session_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM playground_messages WHERE session_id=?", (session_id,))
        conn.commit()


# ╔══════════════════════════════════════════════════════════════╗
# ║                     PROVIDER FUNCTIONS                       ║
# ╚══════════════════════════════════════════════════════════════╝

# ── Groq ─────────────────────────────────────────────────────────────────────
def groq_chat(messages, model):
    api_key = get_secret("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in secrets.")

    clean = [{"role": m["role"], "content": m["content"]} for m in messages]
    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": clean, "temperature": 0.7},
        timeout=45
    )
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


# ── HuggingFace ───────────────────────────────────────────────────────────────
def call_hf_api(prompt: str, repo_id: str = DEFAULT_HF_MODEL, max_retries: int = 3) -> str:
    api_key = get_secret("HUGGINGFACEHUB_API_TOKEN")
    if not api_key:
        raise ValueError("HUGGINGFACEHUB_API_TOKEN not set in secrets.")

    url     = f"https://api-inference.huggingface.co/models/{repo_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 500, "temperature": 0.7, "return_full_text": False},
        "options":    {"wait_for_model": True}
    }

    for attempt in range(1, max_retries + 1):
        response = requests.post(url, headers=headers, json=payload, timeout=90)

        if response.status_code == 503:
            wait = response.json().get("estimated_time", 20)
            time.sleep(min(wait, 30))
            continue

        if response.status_code == 429:
            time.sleep(15)
            continue

        if response.status_code == 401:
            raise ValueError("Invalid HuggingFace API token.")

        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            if "loading" in err.lower() and attempt < max_retries:
                time.sleep(min(data.get("estimated_time", 20), 30))
                continue
            raise ValueError(f"HF error: {err}")

        if isinstance(data, list) and data:
            return data[0].get("generated_text", "").strip()

        raise ValueError(f"Unexpected HF response: {data}")

    raise ValueError("HF API failed after all retries.")


# ── Convert chat history → plain text prompt for HF ──────────────────────────
def messages_to_hf_prompt(messages: list) -> str:
    text = ""
    for m in messages:
        role = "User" if m["role"] == "user" else "Assistant"
        text += f"{role}: {m['content']}\n"
    text += "Assistant:"
    return text


# ╔══════════════════════════════════════════════════════════════╗
# ║              SMART FALLBACK ENGINE                           ║
# ║  Tries each provider in order; auto-skips on failure         ║
# ╚══════════════════════════════════════════════════════════════╝
def ask_with_fallback(messages, provider_order, groq_model_1, groq_model_2, hf_model):
    """
    provider_order: list from user-chosen priority, e.g.
        ["Groq (Primary)", "HuggingFace", "Groq (Fallback)"]

    Returns (answer_text, provider_name_used)
    """
    errors = []

    for provider in provider_order:
        try:
            if provider == "Groq (Primary)":
                answer = groq_chat(messages, groq_model_1)
                return answer, f"Groq · {groq_model_1}"

            elif provider == "Groq (Fallback)":
                answer = groq_chat(messages, groq_model_2)
                return answer, f"Groq · {groq_model_2}"

            elif provider == "HuggingFace":
                prompt = messages_to_hf_prompt(messages)
                answer = call_hf_api(prompt, hf_model)
                return answer, f"HuggingFace · {hf_model}"

        except Exception as e:
            errors.append(f"**{provider}** failed → {e}")
            continue

    # All failed — show what went wrong
    error_summary = "\n\n".join(errors)
    return f"⚠️ All providers failed:\n\n{error_summary}", "None"


# ╔══════════════════════════════════════════════════════════════╗
# ║                    PROMPT PLAYGROUND                         ║
# ║              (unchanged from your original)                  ║
# ╚══════════════════════════════════════════════════════════════╝
def prompt_playground():
    st.header("🧪 Prompt Playground")

    topic = st.text_input("Topic", "Python Interview")
    level = st.selectbox("Level", ["easy", "moderate", "hard"])

    template = st.text_area(
        "Prompt Template",
        "Create Python questions for {topic} suitable for {level} students. "
        "These are for BCA and BTech students."
    )

    repo_id = st.text_input("HF Model", DEFAULT_HF_MODEL)

    if st.button("Generate"):
        prompt_tmpl  = PromptTemplate(input_variables=["topic", "level"], template=template)
        final_prompt = str(prompt_tmpl.invoke({"topic": topic, "level": level}))

        with st.spinner("Generating..."):
            try:
                output = call_hf_api(final_prompt, repo_id)
                st.success("Generated Output")
                st.text_area("Result", output, height=250)
            except Exception as e:
                st.error(f"❌ {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                       CHAT PAGE                              ║
# ╚══════════════════════════════════════════════════════════════╝
def chat_page(sid, groq_model_1, groq_model_2, hf_model):

    st.header("💬 Chat")

    # ── Provider priority picker ─────────────────────────────────
    with st.expander("⚙️ Provider Settings", expanded=False):
        st.caption("Drag to reorder — top provider is tried first, others are automatic fallbacks.")

        all_providers = ["Groq (Primary)", "Groq (Fallback)", "HuggingFace"]

        # Simple checkbox order selector (Streamlit has no drag-drop natively)
        st.markdown("**Priority order (checked = enabled, top = first tried)**")

        if "provider_order" not in st.session_state:
            st.session_state.provider_order = all_providers.copy()

        new_order = []
        for p in st.session_state.provider_order:
            if st.checkbox(p, value=True, key=f"chk_{p}"):
                new_order.append(p)

        # Add any unchecked ones to the end (disabled but keep list complete)
        for p in all_providers:
            if p not in new_order:
                new_order.append(p)

        st.session_state.provider_order = new_order

        enabled_providers = [
            p for p in st.session_state.provider_order
            if st.session_state.get(f"chk_{p}", True)
        ]

        st.info(f"Active order: {' → '.join(enabled_providers)}")

    # ── Chat history ─────────────────────────────────────────────
    history = load_messages(sid)
    for m in history:
        with st.chat_message(m["role"]):
            st.write(m["content"])
            if m["role"] == "assistant" and m.get("provider"):
                st.caption(f"via {m['provider']}")

    # ── Input ────────────────────────────────────────────────────
    msg = st.chat_input("Type your message...")
    if not msg:
        return

    with st.chat_message("user"):
        st.write(msg)

    save_message(sid, "user", msg)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            ans, provider_used = ask_with_fallback(
                load_messages(sid),
                enabled_providers,
                groq_model_1,
                groq_model_2,
                hf_model
            )

        st.write(ans)
        st.caption(f"via {provider_used}")

    save_message(sid, "assistant", ans, provider_used)


# ╔══════════════════════════════════════════════════════════════╗
# ║                          AUTH                                ║
# ╚══════════════════════════════════════════════════════════════╝
def auth():
    users = st.secrets.get("users", {})

    if st.session_state.get("auth"):
        return True

    st.title("AI Chat System")

    with st.form("login"):
        u  = st.text_input("Username")
        p  = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")

    if ok:
        if u in users and users[u] == p:
            st.session_state.auth       = True
            st.session_state.session_id = u
            st.rerun()
        else:
            st.error("Wrong username or password.")

    return False


# ╔══════════════════════════════════════════════════════════════╗
# ║                           MAIN                               ║
# ╚══════════════════════════════════════════════════════════════╝
def main():
    init_db()

    if not auth():
        return

    sid = st.session_state.session_id
    st.title("AI Chat + Playground 🚀")

    with st.sidebar:
        page = st.radio("Navigation", ["Chat", "Prompt Playground"])

        st.divider()
        st.subheader("🔧 Model Settings")
        groq_model_1 = st.text_input("Groq Primary model",  DEFAULT_GROQ_MODEL)
        groq_model_2 = st.text_input("Groq Fallback model", DEFAULT_GROQ_MODEL_2)
        hf_model     = st.text_input("HuggingFace model",   DEFAULT_HF_MODEL)

        st.divider()
        if st.button("🗑️ Clear Chat History"):
            clear_messages(sid)
            st.rerun()

        if st.button("🚪 Logout"):
            st.session_state.clear()
            st.rerun()

    if page == "Chat":
        chat_page(sid, groq_model_1, groq_model_2, hf_model)

    elif page == "Prompt Playground":
        prompt_playground()


if __name__ == "__main__":
    main()
