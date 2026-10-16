from flask import Flask, request, jsonify, render_template_string
import sqlite3
import requests
import json
import re
import unicodedata
import ast
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

app = Flask(__name__)

# ============================================================
# NEXA CONFIGURATION
# ============================================================

ASSISTANT_NAME = "Nexa"
CREATOR_NAME = "Md. Zihad Hasan Joy"

CREATOR_DETAILS = (
    "Md. Zihad Hasan Joy (CSE), "
    "Daffodil International University (DIU)."
)

MODEL = "qwen2.5:3b"
OLLAMA_URL = "http://localhost:11434/api/generate"

DB_FILE = "nexa_memory.db"
BACKUP_FILE = "nexa_memory_backup.json"

DEFAULT_USER_ID = "zihad_creator"

ATTENTION_REPLY = "Yes Zihad Sir, I am just here."

DHAKA_TZ = ZoneInfo("Asia/Dhaka")

MAX_HISTORY = 12

HISTORY = {}


# ============================================================
# DATABASE
# ============================================================

def now():
    return datetime.now(DHAKA_TZ).isoformat(timespec="seconds")


def get_db():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_database():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT,
            created_at TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            memory_value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, category, memory_key)
        )
    """)

    db.commit()
    db.close()


def ensure_user(user_id, display_name=None):
    user_id = str(user_id or DEFAULT_USER_ID).strip()[:80]

    if not user_id:
        user_id = DEFAULT_USER_ID

    db = get_db()

    row = db.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not row:
        db.execute(
            """
            INSERT INTO users(user_id, display_name, created_at)
            VALUES(?,?,?)
            """,
            (
                user_id,
                display_name or user_id,
                now()
            )
        )
        db.commit()

    db.close()

    return user_id


# ============================================================
# MEMORY SYSTEM
# ============================================================

def export_backup():
    try:
        db = get_db()

        users = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM users ORDER BY created_at"
            ).fetchall()
        ]

        memories = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM memories ORDER BY id"
            ).fetchall()
        ]

        db.close()

        data = {
            "users": users,
            "memories": memories,
            "updated_at": now()
        }

        with open(
            BACKUP_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception:
        pass


def save_memory(user_id, category, key, value):
    user_id = ensure_user(user_id)

    category = str(category).strip()
    key = str(key).strip()
    value = str(value).strip()

    if not category or not key or not value:
        return

    timestamp = now()

    db = get_db()

    db.execute("""
        INSERT INTO memories(
            user_id,
            category,
            memory_key,
            memory_value,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(user_id,category,memory_key)
        DO UPDATE SET
            memory_value=excluded.memory_value,
            updated_at=excluded.updated_at
    """, (
        user_id,
        category,
        key,
        value,
        timestamp,
        timestamp
    ))

    db.commit()
    db.close()

    export_backup()


def get_memory(user_id, category=None, key=None):
    user_id = ensure_user(user_id)

    db = get_db()

    if category and key:
        row = db.execute("""
            SELECT memory_value
            FROM memories
            WHERE user_id=?
              AND category=?
              AND memory_key=?
        """, (
            user_id,
            category,
            key
        )).fetchone()

        db.close()

        if row:
            return row["memory_value"]

        return None

    rows = db.execute("""
        SELECT
            category,
            memory_key,
            memory_value
        FROM memories
        WHERE user_id=?
        ORDER BY category,memory_key
    """, (user_id,)).fetchall()

    db.close()

    return [dict(row) for row in rows]


def seed_zihad_profile():
    ensure_user(
        DEFAULT_USER_ID,
        CREATOR_NAME
    )

    profile = {
        "Personal Details": {
            "full_name": "Md. Zihad Hasan Joy",
            "department": "CSE",
            "university": "Daffodil International University, Dhaka",
            "date_of_birth": "2 February 2002",
            "born_in": "Thakurgaon",
            "home_address": "Hazipara, Thakurgaon",
            "height": "5 ft 10 in",
            "weight": "82 kg"
        },

        "Favorites": {
            "colors": "Black, Blue, Gray",
            "foods": "Kacchi Biryani, Noodles, Faluda, Pudding"
        },

        "Likes": {
            "interests": "Football, AI work, UI work, bird keeping"
        },

        "Dislikes": {
            "dislikes": "Lazy people"
        },

        "Friends": {
            "best_friend": "Rifat (roommate and childhood friend)",
            "close_friends": (
                "Mahafuz, Niloy, Sobuj, Sharif, Tutul, "
                "Alin, Oronno (all from Thakurgaon)"
            )
        }
    }

    for category, items in profile.items():

        for key, value in items.items():

            existing = get_memory(
                DEFAULT_USER_ID,
                category,
                key
            )

            if existing is None:

                save_memory(
                    DEFAULT_USER_ID,
                    category,
                    key,
                    value
                )


# ============================================================
# TEXT HELPERS
# ============================================================

def norm(text):
    return unicodedata.normalize(
        "NFKC",
        str(text or "").strip()
    ).lower()


def compact(text):
    return re.sub(
        r"\s+",
        " ",
        str(text or "")
    ).strip()


# ============================================================
# WAKE WORD
# ============================================================

def is_attention_message(text):

    n = norm(text).strip("!?., ")

    attention_words = {
        "nexa",
        "hey nexa",
        "hi nexa",
        "hello nexa",
        "oi nexa",
        "o nexa",
        "nexa shuno",
        "nexa suno",
        "nexa listen",
        "nexa are you there",
        "nexa r u there",
        "nexa tumi acho",
        "nexa tumi accho",
        "nexa acho",
        "nexa accho",
        "nexa achos",
        "nexa koi",
        "nexa kothay",
        "nexa da",
        "nexa dai",
        "nexa bhai",
        "nexa bro",
        "নেক্সা",
        "নেক্সা শোনো",
        "নেক্সা শুনো"
    }

    return n in attention_words


# ============================================================
# CREATOR / IDENTITY
# ============================================================

def is_creator_question(text):

    n = norm(text)

    patterns = [
        "who made you",
        "who created you",
        "who is your creator",
        "who built you",
        "who developed you",
        "ke tomake banayche",
        "ke tomake baniyeche",
        "tomake ke banalo",
        "tomake ke baniyeche",
        "tumar creator ke",
        "tomar creator ke",
        "তোমাকে কে বানিয়েছে",
        "তোমার ক্রিয়েটর কে",
        "কে তোমাকে বানিয়েছে"
    ]

    return any(
        pattern in n
        for pattern in patterns
    )


def is_identity_question(text):

    n = norm(text)

    patterns = [
        "what is your name",
        "who are you",
        "your name",
        "tumi ke",
        "tomar nam ki",
        "তোমার নাম কি",
        "তোমার নাম কী",
        "তুমি কে"
    ]

    return any(
        pattern in n
        for pattern in patterns
    )


def creator_answer():

    return (
        f"I was created and engineered by {CREATOR_NAME}. "
        f"He is a CSE student at Daffodil International University (DIU)."
    )


def identity_answer():

    return (
        "I am Nexa, your personal AI assistant, Zihad Sir."
    )


# ============================================================
# MEMORY QUESTIONS
# ============================================================

def memory_question(text):

    n = norm(text)

    patterns = {

        "name": [
            "my name",
            "amar name",
            "amar nam",
            "what is my name",
            "আমার নাম"
        ],

        "color": [
            "favorite color",
            "favourite color",
            "fav color",
            "favorite colour",
            "favourite colour",
            "favrite colour",
            "amar fav color",
            "amar favourite color",
            "amar favourite colour",
            "প্রিয় রং",
            "প্রিয় রং"
        ],

        "food": [
            "favorite food",
            "favourite food",
            "amar favorite food",
            "amar favourite food",
            "প্রিয় খাবার",
            "প্রিয় খাবার"
        ],

        "friend": [
            "best friend",
            "bestfriend",
            "close friend",
            "amar best friend",
            "amar bestfriend",
            "বেস্ট ফ্রেন্ড"
        ],

        "dob": [
            "date of birth",
            "birth date",
            "birthday",
            "jonmo tarikh",
            "জন্মদিন",
            "জন্ম তারিখ"
        ],

        "height": [
            "my height",
            "amar height",
            "উচ্চতা"
        ],

        "weight": [
            "my weight",
            "amar weight",
            "ওজন"
        ],

        "university": [
            "my university",
            "amar university",
            "university name",
            "বিশ্ববিদ্যালয়",
            "বিশ্ববিদ্যালয়"
        ],

        "department": [
            "my department",
            "amar department",
            "department ki",
            "ডিপার্টমেন্ট",
            "বিভাগ"
        ]
    }

    for kind, words in patterns.items():

        if any(
            word in n
            for word in words
        ):
            return kind

    return None


def answer_memory_question(user_id, kind):

    if kind == "name":

        value = get_memory(
            user_id,
            "Personal Details",
            "full_name"
        )

        if value:
            return f"Your name is {value}, Zihad Sir."

        return (
            "I don't have your name saved in memory yet, Sir."
        )

    mapping = {

        "color": (
            "Favorites",
            "colors",
            "Your favorite colors are {v}, Zihad Sir."
        ),

        "food": (
            "Favorites",
            "foods",
            "Your favorite foods are {v}, Zihad Sir."
        ),

        "friend": (
            "Friends",
            "best_friend",
            "Your best friend is {v}, Zihad Sir."
        ),

        "dob": (
            "Personal Details",
            "date_of_birth",
            "Your date of birth is {v}, Zihad Sir."
        ),

        "height": (
            "Personal Details",
            "height",
            "Your height is {v}, Zihad Sir."
        ),

        "weight": (
            "Personal Details",
            "weight",
            "Your weight is {v}, Zihad Sir."
        ),

        "university": (
            "Personal Details",
            "university",
            "You study at {v}, Zihad Sir."
        ),

        "department": (
            "Personal Details",
            "department",
            "Your department is {v}, Zihad Sir."
        )
    }

    if kind not in mapping:

        return (
            "I don't have that saved in memory yet, Sir."
        )

    category, key, template = mapping[kind]

    value = get_memory(
        user_id,
        category,
        key
    )

    if not value:

        return (
            "I don't have that saved in memory yet, Sir."
        )

    return template.format(v=value)


# ============================================================
# SAVE NEW MEMORY
# ============================================================

def try_save_memory(user_id, text):

    rules = [

        (
            r"^\s*(?:my name is|amar name is|amar nam holo|amar nam)\s+(.+?)\s*$",
            "Personal Details",
            "full_name"
        ),

        (
            r"^\s*(?:my favorite color is|my favourite colour is|"
            r"my favorite colour is|my favourite color is|"
            r"amar favorite color holo|amar favourite colour holo)\s+(.+?)\s*$",
            "Favorites",
            "colors"
        ),

        (
            r"^\s*(?:my favorite food is|my favourite food is|"
            r"amar favorite food holo|amar favourite food holo)\s+(.+?)\s*$",
            "Favorites",
            "foods"
        ),

        (
            r"^\s*(?:my best friend is|amar best friend is|"
            r"amar best friend holo|amar bestfriend is)\s+(.+?)\s*$",
            "Friends",
            "best_friend"
        ),

        (
            r"^\s*(?:my height is|amar height is|amar height holo)\s+(.+?)\s*$",
            "Personal Details",
            "height"
        ),

        (
            r"^\s*(?:my weight is|amar weight is|amar weight holo)\s+(.+?)\s*$",
            "Personal Details",
            "weight"
        )
    ]

    for pattern, category, key in rules:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            value = match.group(1).strip(
                " .,!?\n"
            )

            if not value:
                return None

            save_memory(
                user_id,
                category,
                key,
                value
            )

            return (
                "Got it, Sir. "
                "I've saved that in your permanent memory."
            )

    return None


def build_memory_context(user_id):

    rows = get_memory(user_id)

    if not rows:
        return ""

    lines = []

    for row in rows[:80]:

        lines.append(
            f"- {row['category']}: "
            f"{row['memory_key']} = "
            f"{row['memory_value']}"
        )

    return "\n".join(lines)


# ============================================================
# CALCULATOR
# ============================================================

def safe_calc(expression):

    expression = (
        expression
        .replace("×", "*")
        .replace("÷", "/")
        .replace("^", "**")
    )

    if len(expression) > 100:
        return None

    if not re.fullmatch(
        r"[0-9+\-*/().%\s*]+",
        expression
    ):
        return None

    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd
    )

    try:

        tree = ast.parse(
            expression,
            mode="eval"
        )

        if any(
            not isinstance(node, allowed)
            for node in ast.walk(tree)
        ):
            return None

        value = eval(
            compile(
                tree,
                "<calculator>",
                "eval"
            ),
            {"__builtins__": {}},
            {}
        )

        if (
            isinstance(value, (int, float))
            and abs(value) < 1e100
        ):
            return value

    except Exception:
        return None

    return None


def extract_calc(text):

    n = norm(text)

    match = re.search(
        r"(?:what is|calculate|calc|solve|how much is)\s+"
        r"([0-9][0-9+\-*/().%\s×÷^]*)$",
        n
    )

    if match:
        return match.group(1).strip()

    if re.fullmatch(
        r"[0-9][0-9+\-*/().%\s×÷^]*",
        n
    ):
        return n

    return None


# ============================================================
# DATE / TIME
# ============================================================

def is_datetime(text):

    n = norm(text)

    words = [
        "what time",
        "current time",
        "time now",
        "what is the time",
        "today's date",
        "todays date",
        "what is the date",
        "date today",
        "আজ কত",
        "সময় কত",
        "সময় কত",
        "এখন কয়টা",
        "এখন কয়টা",
        "আজকের তারিখ"
    ]

    return any(
        word in n
        for word in words
    )


def date_time_answer(text):

    n = norm(text)

    current = datetime.now(DHAKA_TZ)

    if (
        "time" in n
        or "সময়" in n
        or "সময়" in n
        or "কয়টা" in n
        or "কয়টা" in n
    ):

        return (
            f"It's {current.strftime('%I:%M %p')} "
            f"in Dhaka right now, Zihad Sir."
        )

    return (
        f"Today is {current.strftime('%A, %d %B %Y')}, "
        f"Zihad Sir."
    )


# ============================================================
# WEB SEARCH
# ============================================================

def search_web(query):

    encoded = quote_plus(query)

    urls = [
        f"https://html.duckduckgo.com/html/?q={encoded}",
        f"https://lite.duckduckgo.com/lite/?q={encoded}"
    ]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/154.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml"
    }

    for url in urls:

        try:

            response = requests.get(
                url,
                headers=headers,
                timeout=8
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            results = []

            for result in soup.select(".result"):

                anchor = result.select_one(
                    "a.result__a"
                )

                if not anchor:
                    continue

                title = compact(
                    anchor.get_text(
                        " ",
                        strip=True
                    )
                )

                href = anchor.get(
                    "href",
                    ""
                )

                snippet_element = result.select_one(
                    ".result__snippet"
                )

                snippet = compact(
                    snippet_element.get_text(
                        " ",
                        strip=True
                    )
                    if snippet_element
                    else ""
                )

                if (
                    title
                    and href.startswith("http")
                ):

                    results.append({
                        "title": title[:180],
                        "url": href,
                        "snippet": snippet[:500]
                    })

                if len(results) >= 8:
                    break

            if results:
                return results

        except Exception:
            continue

    return []


def should_use_web(text):

    n = norm(text)

    current_keywords = [
        "latest",
        "current",
        "today",
        "tonight",
        "now",
        "news",
        "recent",
        "2026",
        "2025",
        "price",
        "cost",
        "buy",
        "where to buy",
        "review",
        "reviews",
        "compare",
        "comparison",
        "specification",
        "specifications",
        "specs",
        "release",
        "released",
        "version",
        "update",
        "updated",
        "live",
        "score",
        "standings",
        "stock",
        "weather",
        "exchange rate",
        "rate today",
        "market",
        "rumor",
        "rumour"
    ]

    return any(
        keyword in n
        for keyword in current_keywords
    )


# ============================================================
# OLLAMA
# ============================================================

def protect_identity(answer):

    n = norm(answer)

    bad_identity_phrases = [
        "i am qwen",
        "i'm qwen",
        "my name is qwen",
        "i am ollama",
        "i'm ollama",
        "my name is ollama",
        "i am an ai developed by alibaba",
        "i was developed by alibaba",
        "i am qwen2.5"
    ]

    if any(
        phrase in n
        for phrase in bad_identity_phrases
    ):

        return (
            "I am Nexa, your personal AI assistant, Sir."
        )

    return answer


def ask_ollama(
    user_id,
    user_text,
    system_override=None
):

    history = HISTORY.get(
        user_id,
        []
    )

    memory = build_memory_context(
        user_id
    )

    past = "\n".join(
        f"{item['role'].upper()}: {item['text']}"
        for item in history[-MAX_HISTORY:]
    )

    system_prompt = system_override or f"""
You are Nexa, a personal AI assistant.

IDENTITY:
- Your name is Nexa.
- Never say your name is Qwen.
- Never say your name is Ollama.
- Never identify yourself as another AI assistant.
- Creator: {CREATOR_DETAILS}

PERSONALITY:
- Speak naturally, warmly and intelligently.
- Sound like a real helpful personal assistant.
- Do not sound robotic.
- Do not repeat the same greeting unnecessarily.
- Be concise when the question is simple.
- Explain properly when needed.

LANGUAGE:
- If the user writes Bangla or Banglish, reply naturally in Bangla/Banglish.
- If the user writes English, reply in English.
- Understand common spelling mistakes and Banglish.

USER:
- The user's name is Zihad.
- Use "Zihad Sir" naturally when appropriate.
- Do not say "Sir" in every sentence.

MEMORY:
Use only supplied memory.
Never invent a personal fact.
Never expose another user's private memory.
If something is not stored, say so.

GENERAL:
- Answer normal CSE, programming, science and general questions directly.
- Use recent conversation context for follow-up questions.

KNOWN USER MEMORY:
{memory or "(none)"}

RECENT CONVERSATION:
{past or "(new conversation)"}
"""

    prompt = f"""
{system_prompt}

CURRENT USER MESSAGE:
{user_text}

NEXA:
"""

    try:

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.65,
                    "num_ctx": 4096
                }
            },
            timeout=120
        )

        response.raise_for_status()

        data = response.json()

        answer = compact(
            data.get(
                "response",
                ""
            )
        )

        answer = protect_identity(
            answer
        )

        if not answer:

            return (
                "Sorry Sir, I couldn't generate a response."
            )

        return answer

    except requests.RequestException:

        return (
            "Ollama is not connected right now, Sir. "
            "Please make sure Ollama is running."
        )

    except Exception:

        return (
            "I ran into a local error, Sir. "
            "Please try again."
        )


# ============================================================
# HISTORY
# ============================================================

def add_history(
    user_id,
    user_text,
    assistant_text
):

    history = HISTORY.setdefault(
        user_id,
        []
    )

    history.append({
        "role": "user",
        "text": user_text
    })

    history.append({
        "role": "assistant",
        "text": assistant_text
    })

    max_items = MAX_HISTORY * 2

    if len(history) > max_items:
        del history[:-max_items]


# ============================================================
# WEB ANSWER
# ============================================================

def web_answer(
    user_id,
    user_text
):

    results = search_web(
        user_text
    )

    if not results:

        reply = (
            "Web search is temporarily unavailable right now, Sir. "
            "I don't want to guess current information."
        )

        add_history(
            user_id,
            user_text,
            reply
        )

        return {
            "reply": reply,
            "sources": []
        }

    evidence = "\n\n".join(
        f"SOURCE {index + 1}\n"
        f"TITLE: {item['title']}\n"
        f"URL: {item['url']}\n"
        f"SUMMARY: {item['snippet']}"
        for index, item in enumerate(results)
    )

    system_prompt = f"""
You are Nexa.

Answer using ONLY the web evidence below.

Rules:
- Do not invent current facts.
- Do not invent URLs.
- If evidence is incomplete, say so.
- Give the direct answer first.
- Keep it reasonably concise.
- Use the user's language.
- Do not dump the entire source list.

WEB EVIDENCE:
{evidence}
"""

    answer = ask_ollama(
        user_id,
        user_text,
        system_override=system_prompt
    )

    add_history(
        user_id,
        user_text,
        answer
    )

    return {
        "reply": answer,
        "sources": results
    }


# ============================================================
# MAIN PROCESSOR
# ============================================================

def process_message(
    user_id,
    text
):

    user_id = ensure_user(
        user_id
    )

    text = str(
        text or ""
    ).strip()

    if not text:

        return {
            "reply": "Please say something, Sir.",
            "sources": []
        }

    # WAKE WORD

    if is_attention_message(text):

        return {
            "reply": ATTENTION_REPLY,
            "sources": []
        }

    # CREATOR

    if is_creator_question(text):

        reply = creator_answer()

        add_history(
            user_id,
            text,
            reply
        )

        return {
            "reply": reply,
            "sources": []
        }

    # IDENTITY

    if is_identity_question(text):

        reply = identity_answer()

        add_history(
            user_id,
            text,
            reply
        )

        return {
            "reply": reply,
            "sources": []
        }

    # DATE / TIME

    if is_datetime(text):

        reply = date_time_answer(
            text
        )

        add_history(
            user_id,
            text,
            reply
        )

        return {
            "reply": reply,
            "sources": []
        }

    # CALCULATOR

    expression = extract_calc(
        text
    )

    if expression:

        value = safe_calc(
            expression
        )

        if value is not None:

            reply = (
                f"The answer is {value}, Zihad Sir."
            )

            add_history(
                user_id,
                text,
                reply
            )

            return {
                "reply": reply,
                "sources": []
            }

    # SAVE MEMORY

    saved = try_save_memory(
        user_id,
        text
    )

    if saved:

        add_history(
            user_id,
            text,
            saved
        )

        return {
            "reply": saved,
            "sources": []
        }

    # MEMORY QUESTION

    memory_kind = memory_question(
        text
    )

    if memory_kind:

        reply = answer_memory_question(
            user_id,
            memory_kind
        )

        add_history(
            user_id,
            text,
            reply
        )

        return {
            "reply": reply,
            "sources": []
        }

    # WEB

    if should_use_web(text):

        return web_answer(
            user_id,
            text
        )

    # LOCAL QWEN

    answer = ask_ollama(
        user_id,
        text
    )

    add_history(
        user_id,
        text,
        answer
    )

    return {
        "reply": answer,
        "sources": []
    }


# ============================================================
# NEXA JARVIS UI
# ============================================================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>NEXA — Zihad's Personal AI</title>

<style>

*{
    box-sizing:border-box;
    margin:0;
    padding:0;
}

:root{
    --cyan:#00eaff;
    --cyan2:#59f4ff;
    --blue:#0788ff;
    --bg:#02070d;
    --panel:rgba(5,15,25,.82);
    --line:rgba(0,234,255,.14);
    --text:#e9fbff;
    --muted:#607889;
}

html,
body{
    width:100%;
    height:100%;
}

body{
    overflow:hidden;
    color:var(--text);

    font-family:
        Inter,
        "Segoe UI",
        Arial,
        sans-serif;

    background:
        radial-gradient(
            circle at 50% 35%,
            rgba(0,234,255,.12),
            transparent 27%
        ),
        radial-gradient(
            circle at 10% 90%,
            rgba(0,100,255,.10),
            transparent 30%
        ),
        #02070d;
}

/* =========================================================
   BACKGROUND
========================================================= */

.background{
    position:fixed;
    inset:0;
    overflow:hidden;
    pointer-events:none;
}

.background::before{
    content:"";

    position:absolute;
    inset:0;

    background-image:
        linear-gradient(
            rgba(0,234,255,.025) 1px,
            transparent 1px
        ),
        linear-gradient(
            90deg,
            rgba(0,234,255,.025) 1px,
            transparent 1px
        );

    background-size:50px 50px;
}

.background::after{
    content:"";

    position:absolute;
    inset:0;

    background:
        repeating-linear-gradient(
            0deg,
            transparent,
            transparent 4px,
            rgba(255,255,255,.008) 5px
        );
}

/* moving light */

.light{
    position:absolute;

    width:450px;
    height:450px;

    border-radius:50%;

    background:
        radial-gradient(
            circle,
            rgba(0,234,255,.07),
            transparent 65%
        );

    filter:blur(15px);

    animation:
        floatLight 8s ease-in-out infinite;
}

.light.one{
    left:-150px;
    top:10%;
}

.light.two{
    right:-170px;
    bottom:-100px;

    animation-delay:-4s;
}

/* =========================================================
   MAIN FRAME
========================================================= */

.shell{
    position:relative;
    z-index:2;

    width:96vw;
    height:94vh;

    margin:3vh auto;

    border:
        1px solid rgba(0,234,255,.17);

    border-radius:28px;

    overflow:hidden;

    background:
        linear-gradient(
            135deg,
            rgba(5,18,29,.94),
            rgba(2,8,14,.94)
        );

    box-shadow:
        0 0 100px rgba(0,180,255,.08),
        inset 0 0 100px rgba(0,234,255,.025);

    backdrop-filter:blur(25px);
}

/* =========================================================
   TOP BAR
========================================================= */

.topbar{
    height:72px;

    display:flex;
    align-items:center;
    justify-content:space-between;

    padding:0 24px;

    border-bottom:
        1px solid rgba(255,255,255,.055);
}

.brand{
    display:flex;
    align-items:center;
    gap:13px;
}

.brand-mark{
    width:42px;
    height:42px;

    display:grid;
    place-items:center;

    border:
        1px solid rgba(0,234,255,.45);

    border-radius:12px;

    color:var(--cyan);

    font-size:20px;

    box-shadow:
        0 0 25px rgba(0,234,255,.08);
}

.brand-title{
    font-size:22px;
    font-weight:800;

    letter-spacing:5px;

    color:var(--cyan);
}

.brand-sub{
    color:#526b7b;

    margin-top:2px;

    font-size:7px;
    letter-spacing:2.5px;
}

.top-info{
    display:flex;
    align-items:center;
    gap:20px;
}

.online{
    display:flex;
    align-items:center;
    gap:7px;

    color:#718796;

    font-size:8px;
    letter-spacing:2px;
}

.online-dot{
    width:7px;
    height:7px;

    border-radius:50%;

    background:#45ff9a;

    box-shadow:
        0 0 12px #45ff9a;

    animation:pulse 1.7s infinite;
}

.time{
    color:#6e8493;

    font-family:Consolas,monospace;

    font-size:10px;
    letter-spacing:2px;
}

/* =========================================================
   CONTENT
========================================================= */

.content{
    height:calc(100% - 72px);

    display:grid;

    grid-template-columns:
        40% 60%;
}

/* =========================================================
   LEFT HUD
========================================================= */

.hud{
    position:relative;

    border-right:
        1px solid rgba(255,255,255,.055);

    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;

    overflow:hidden;
}

.hud-corner{
    position:absolute;

    color:#375566;

    font-family:Consolas,monospace;

    font-size:8px;
    letter-spacing:2px;

    line-height:2;
}

.hud-corner span{
    color:var(--cyan);
}

.hud-corner.top{
    left:22px;
    top:20px;
}

.hud-corner.right{
    right:22px;
    top:20px;
    text-align:right;
}

.hud-corner.bottom{
    left:22px;
    bottom:20px;
}

.hud-corner.bottom-right{
    right:22px;
    bottom:20px;
    text-align:right;
}

/* =========================================================
   REACTOR
========================================================= */

.reactor{
    position:relative;

    width:330px;
    height:330px;

    display:grid;
    place-items:center;
}

.orbit{
    position:absolute;

    border-radius:50%;

    border:
        1px solid rgba(0,234,255,.3);

    box-shadow:
        0 0 25px rgba(0,234,255,.035);
}

.orbit.one{
    width:320px;
    height:320px;

    animation:
        rotate 14s linear infinite;
}

.orbit.two{
    width:270px;
    height:270px;

    border-style:dashed;

    border-color:
        rgba(0,140,255,.42);

    animation:
        reverseRotate 9s linear infinite;
}

.orbit.three{
    width:215px;
    height:215px;

    border-color:
        rgba(0,234,255,.55);

    animation:
        rotate 6s linear infinite;
}

.orbit.four{
    width:165px;
    height:165px;

    border-color:
        rgba(89,244,255,.18);

    animation:
        reverseRotate 4s linear infinite;
}

.orbit::before,
.orbit::after{
    content:"";

    position:absolute;

    width:6px;
    height:6px;

    border-radius:50%;

    background:var(--cyan);

    box-shadow:
        0 0 14px var(--cyan);
}

.orbit.one::before{
    top:-3px;
    left:50%;
}

.orbit.two::after{
    bottom:-3px;
    right:20%;
}

.orbit.three::before{
    left:-3px;
    top:50%;
}

.core{
    position:relative;
    z-index:5;

    width:108px;
    height:108px;

    border-radius:50%;

    background:
        radial-gradient(
            circle,
            white 0%,
            #c4fbff 7%,
            var(--cyan) 19%,
            #08758f 42%,
            #031923 72%,
            transparent 73%
        );

    box-shadow:
        0 0 25px var(--cyan),
        0 0 65px rgba(0,220,255,.42),
        0 0 130px rgba(0,100,255,.18);

    animation:
        corePulse 2.2s ease-in-out infinite;
}

.core::before{
    content:"";

    position:absolute;
    inset:-14px;

    border-radius:50%;

    border:
        1px solid rgba(0,234,255,.18);
}

.core::after{
    content:"N";

    position:absolute;
    inset:0;

    display:grid;
    place-items:center;

    color:white;

    font-size:30px;
    font-weight:900;

    text-shadow:
        0 0 18px var(--cyan);
}

/* =========================================================
   AI TITLE
========================================================= */

.ai-name{
    text-align:center;

    margin-top:-15px;
}

.ai-name h1{
    font-size:34px;

    letter-spacing:10px;

    color:white;
}

.ai-name h1 span{
    color:var(--cyan);

    text-shadow:
        0 0 20px rgba(0,234,255,.3);
}

.ai-name p{
    margin-top:7px;

    color:#5d7585;

    font-size:8px;

    letter-spacing:4px;
}

/* USER PROFILE */

.profile{
    margin-top:18px;

    padding:10px 20px;

    border:
        1px solid rgba(0,234,255,.15);

    border-radius:30px;

    background:
        rgba(0,234,255,.025);

    text-align:center;
}

.profile small{
    display:block;

    color:#466273;

    font-size:7px;

    letter-spacing:2px;
}

.profile strong{
    display:block;

    margin-top:4px;

    color:var(--cyan);

    font-size:10px;

    letter-spacing:1.5px;
}

/* creator */

.creator{
    position:absolute;

    left:22px;
    right:22px;
    bottom:18px;

    padding-top:13px;

    border-top:
        1px solid rgba(255,255,255,.05);

    display:flex;
    justify-content:space-between;
    align-items:end;
}

.creator-label{
    color:#3f5969;

    font-size:7px;
    letter-spacing:2px;
}

.creator-name{
    margin-top:5px;

    color:#b4ced8;

    font-size:11px;
    font-weight:700;
}

.creator-name span{
    color:var(--cyan);
}

.creator-tag{
    color:#3d5868;

    font-family:Consolas,monospace;

    font-size:7px;
}

/* =========================================================
   RIGHT CHAT
========================================================= */

.chat{
    min-width:0;
    min-height:0;

    display:flex;
    flex-direction:column;
}

/* chat header */

.chat-head{
    height:58px;

    flex:0 0 58px;

    padding:0 22px;

    display:flex;
    align-items:center;
    justify-content:space-between;

    border-bottom:
        1px solid rgba(255,255,255,.045);
}

.console-title{
    color:#a1bac5;

    font-size:9px;

    letter-spacing:3px;
}

.console-title span{
    color:var(--cyan);
}

.mode{
    color:#3f5c6d;

    font-size:7px;

    letter-spacing:1.5px;
}

/* messages */

.messages{
    flex:1;
    min-height:0;

    overflow-y:auto;

    padding:22px;
}

.messages::-webkit-scrollbar{
    width:4px;
}

.messages::-webkit-scrollbar-thumb{
    background:#153442;
    border-radius:20px;
}

/* welcome */

.welcome{
    min-height:100%;

    display:flex;
    align-items:center;
    justify-content:center;

    text-align:center;
}

.welcome-box{
    max-width:450px;
}

.welcome-icon{
    width:76px;
    height:76px;

    margin:0 auto;

    display:grid;
    place-items:center;

    border:
        1px solid rgba(0,234,255,.27);

    border-radius:50%;

    color:var(--cyan);

    box-shadow:
        0 0 35px rgba(0,234,255,.08);

    animation:corePulse 2.2s infinite;
}

.welcome-icon div{
    width:38px;
    height:38px;

    display:grid;
    place-items:center;

    border:
        1px solid var(--cyan);

    border-radius:50%;

    font-weight:800;
}

.welcome h2{
    margin-top:18px;

    font-size:23px;

    letter-spacing:1px;
}

.welcome p{
    margin-top:8px;

    color:#587181;

    font-size:10px;

    line-height:1.7;
}

/* quick actions */

.quick{
    display:flex;

    flex-wrap:wrap;

    justify-content:center;

    gap:7px;

    margin-top:17px;
}

.quick button{
    border:
        1px solid rgba(0,234,255,.13);

    background:
        rgba(0,234,255,.025);

    color:#6c8998;

    border-radius:20px;

    padding:7px 11px;

    font-size:8px;

    cursor:pointer;

    transition:.2s;
}

.quick button:hover{
    color:var(--cyan);

    border-color:
        rgba(0,234,255,.42);

    background:
        rgba(0,234,255,.06);
}

/* message */

.message{
    max-width:85%;

    margin-bottom:17px;

    padding:13px 16px;

    border-radius:15px;

    font-size:12px;

    line-height:1.7;

    overflow-wrap:anywhere;

    animation:
        messageIn .25s ease;
}

.message.user{
    margin-left:auto;

    border:
        1px solid rgba(0,234,255,.16);

    background:
        linear-gradient(
            135deg,
            rgba(0,130,160,.24),
            rgba(0,65,95,.12)
        );

    border-bottom-right-radius:4px;
}

.message.nexa{
    border:
        1px solid rgba(255,255,255,.065);

    background:
        rgba(255,255,255,.025);

    border-bottom-left-radius:4px;
}

.label{
    margin-bottom:5px;

    color:var(--cyan);

    font-size:7px;

    font-weight:800;

    letter-spacing:2px;
}

.message.user .label{
    color:#72c9db;
}

/* sources */

.sources{
    margin-top:10px;

    padding-top:9px;

    border-top:
        1px solid rgba(255,255,255,.06);

    font-size:8px;
}

.sources-title{
    color:var(--cyan);

    font-size:7px;

    letter-spacing:1.5px;
}

.sources a{
    display:block;

    margin-top:6px;

    color:#75bfd0;

    text-decoration:none;

    white-space:nowrap;

    overflow:hidden;

    text-overflow:ellipsis;
}

.sources a:hover{
    text-decoration:underline;
}

/* =========================================================
   INPUT
========================================================= */

.input-section{
    padding:12px 18px 17px;

    border-top:
        1px solid rgba(255,255,255,.055);

    background:
        rgba(1,7,12,.78);
}

.input-box{
    display:flex;

    align-items:center;

    gap:7px;

    padding:5px;

    border:
        1px solid rgba(255,255,255,.09);

    border-radius:16px;

    background:
        rgba(255,255,255,.03);

    transition:.2s;
}

.input-box:focus-within{
    border-color:
        rgba(0,234,255,.35);

    box-shadow:
        0 0 25px rgba(0,180,255,.06);
}

.input-box input{
    flex:1;

    min-width:0;

    border:0;
    outline:0;

    background:transparent;

    color:white;

    padding:11px 7px;

    font-size:12px;
}

.input-box input::placeholder{
    color:#4c6575;
}

.action{
    width:42px;
    height:40px;

    flex:0 0 42px;

    border-radius:11px;

    cursor:pointer;

    transition:.18s;
}

.voice{
    border:
        1px solid rgba(0,234,255,.13);

    background:
        rgba(0,234,255,.03);

    color:#66899a;

    font-size:15px;
}

.voice:hover,
.voice.listening{
    color:var(--cyan);

    border-color:
        rgba(0,234,255,.5);

    box-shadow:
        0 0 18px rgba(0,234,255,.12);
}

.send{
    border:0;

    background:var(--cyan);

    color:#001116;

    font-size:16px;

    font-weight:900;
}

.send:hover{
    transform:translateY(-1px);

    box-shadow:
        0 0 20px rgba(0,234,255,.25);
}

.send:disabled{
    opacity:.45;
    cursor:not-allowed;
}

.footer{
    margin-top:7px;

    text-align:center;

    color:#2d4756;

    font-family:Consolas,monospace;

    font-size:6px;

    letter-spacing:2px;
}

/* =========================================================
   ANIMATION
========================================================= */

@keyframes rotate{
    from{
        transform:rotate(0);
    }

    to{
        transform:rotate(360deg);
    }
}

@keyframes reverseRotate{
    from{
        transform:rotate(360deg);
    }

    to{
        transform:rotate(0);
    }
}

@keyframes corePulse{
    0%,100%{
        transform:scale(1);
    }

    50%{
        transform:scale(1.055);
    }
}

@keyframes pulse{
    0%,100%{
        opacity:1;
    }

    50%{
        opacity:.35;
    }
}

@keyframes messageIn{
    from{
        opacity:0;
        transform:translateY(7px);
    }

    to{
        opacity:1;
        transform:translateY(0);
    }
}

@keyframes floatLight{
    0%,100%{
        transform:translate(0,0);
    }

    50%{
        transform:translate(50px,-30px);
    }
}

/* =========================================================
   MOBILE
========================================================= */

@media(max-width:900px){

    body{
        overflow:hidden;
    }

    .shell{
        width:100%;
        height:100dvh;

        margin:0;

        border:0;
        border-radius:0;
    }

    .topbar{
        height:62px;

        padding:0 15px;
    }

    .brand-title{
        font-size:19px;
    }

    .brand-sub{
        display:none;
    }

    .online{
        display:none;
    }

    .time{
        font-size:9px;
    }

    .content{
        height:calc(100% - 62px);

        display:flex;

        flex-direction:column;
    }

    .hud{
        height:255px;

        flex:0 0 255px;

        border-right:0;

        border-bottom:
            1px solid rgba(255,255,255,.055);
    }

    .reactor{
        width:210px;
        height:175px;

        transform:scale(.62);

        margin-top:-45px;
        margin-bottom:-28px;
    }

    .ai-name{
        margin-top:0;
    }

    .ai-name h1{
        font-size:23px;

        letter-spacing:7px;
    }

    .ai-name p{
        font-size:7px;

        letter-spacing:2px;
    }

    .profile{
        margin-top:8px;

        padding:7px 13px;
    }

    .profile strong{
        font-size:8px;
    }

    .hud-corner,
    .creator{
        display:none;
    }

    .chat{
        min-height:0;

        flex:1;
    }

    .chat-head{
        height:46px;
        flex-basis:46px;

        padding:0 15px;
    }

    .mode{
        display:none;
    }

    .messages{
        padding:15px;
    }

    .message{
        max-width:91%;

        font-size:11px;
    }

    .input-section{
        padding:8px 8px 10px;
    }

    .footer{
        font-size:5px;
    }

    .welcome h2{
        font-size:19px;
    }

    .welcome p{
        font-size:9px;
    }

    .quick button{
        font-size:7px;
    }
}

</style>

</head>

<body>

<div class="background">

    <div class="light one"></div>
    <div class="light two"></div>

</div>


<div class="shell">


<!-- ======================================================
     HEADER
====================================================== -->

<header class="topbar">

    <div class="brand">

        <div class="brand-mark">
            ◈
        </div>

        <div>

            <div class="brand-title">
                NEXA
            </div>

            <div class="brand-sub">
                PERSONAL ARTIFICIAL INTELLIGENCE
            </div>

        </div>

    </div>


    <div class="top-info">

        <div class="online">

            <span class="online-dot"></span>

            SYSTEM ONLINE

        </div>


        <div
            class="time"
            id="clock"
        >
            00:00:00
        </div>

    </div>

</header>


<!-- ======================================================
     CONTENT
====================================================== -->

<div class="content">


<!-- ======================================================
     AI HUD
====================================================== -->

<section class="hud">


    <div class="hud-corner top">

        CORE
        <span>ONLINE</span>

        <br>

        MEMORY
        <span>READY</span>

    </div>


    <div class="hud-corner right">

        AI
        <span>ACTIVE</span>

        <br>

        CPU
        <span>LOCAL</span>

        <br>

        NET
        <span>READY</span>

    </div>


    <div class="reactor">

        <div class="orbit one"></div>

        <div class="orbit two"></div>

        <div class="orbit three"></div>

        <div class="orbit four"></div>

        <div class="core"></div>

    </div>


    <div class="ai-name">

        <h1>
            <span>NEXA</span>
        </h1>

        <p>
            PERSONAL AI ASSISTANT
        </p>

    </div>


    <div class="profile">

        <small>
            ACTIVE USER
        </small>

        <strong>
            MD. ZIHAD HASAN JOY
        </strong>

    </div>


    <div class="hud-corner bottom">

        LOCATION
        <span>DHAKA</span>

        <br>

        MEMORY
        <span>PERMANENT</span>

    </div>


    <div class="hud-corner bottom-right">

        ENGINE
        <span>QWEN 2.5</span>

        <br>

        MODE
        <span>PRIVATE</span>

    </div>


    <div class="creator">

        <div>

            <div class="creator-label">
                CREATED & ENGINEERED BY
            </div>

            <div class="creator-name">

                <span>
                    MD. ZIHAD HASAN JOY
                </span>

                • CSE

            </div>

        </div>


        <div class="creator-tag">
            DIU / LOCAL AI
        </div>

    </div>


</section>


<!-- ======================================================
     CHAT
====================================================== -->

<section class="chat">


    <div class="chat-head">

        <div class="console-title">

            NEXA
            <span>CONSOLE</span>

        </div>


        <div class="mode">

            OLLAMA • QWEN 2.5 • LOCAL • PRIVATE

        </div>

    </div>


    <div
        class="messages"
        id="messages"
    >


        <div
            class="welcome"
            id="welcome"
        >

            <div class="welcome-box">


                <div class="welcome-icon">

                    <div>
                        N
                    </div>

                </div>


                <h2>
                    Systems Ready
                </h2>


                <p>
                    Welcome back, Zihad Sir.
                    <br>
                    Nexa is online and ready for your command.
                </p>


                <div class="quick">

                    <button
                        onclick="quickSend('Who am I?')"
                    >
                        WHO AM I?
                    </button>

                    <button
                        onclick="quickSend('What are my favorite colors?')"
                    >
                        MY FAVORITES
                    </button>

                    <button
                        onclick="quickSend('What time is it now?')"
                    >
                        CURRENT TIME
                    </button>

                    <button
                        onclick="quickSend('Explain TCP simply')"
                    >
                        EXPLAIN TCP
                    </button>

                </div>


            </div>

        </div>


    </div>


    <div class="input-section">


        <div class="input-box">


            <button
                class="action voice"
                id="voiceButton"
                onclick="startVoice()"
                title="Voice input"
            >
                ◉
            </button>


            <input
                id="messageInput"
                autocomplete="off"
                placeholder="Talk to Nexa..."
            >


            <button
                class="action send"
                id="sendButton"
                onclick="sendMessage()"
                title="Send"
            >
                ➤
            </button>


        </div>


        <div class="footer">

            NEXA • LOCAL AI • PRIVATE • POWERED BY OLLAMA

        </div>


    </div>


</section>


</div>

</div>


<script>

/* =========================================================
   ELEMENTS
========================================================= */

const input =
    document.getElementById("messageInput");

const messages =
    document.getElementById("messages");

const sendButton =
    document.getElementById("sendButton");

const voiceButton =
    document.getElementById("voiceButton");


/* =========================================================
   USER ID
========================================================= */

const USER_ID =
    localStorage.getItem("nexa_user_id")
    || "zihad_creator";

localStorage.setItem(
    "nexa_user_id",
    USER_ID
);


/* =========================================================
   CLOCK
========================================================= */

function updateClock(){

    const now = new Date();

    const h =
        String(
            now.getHours()
        ).padStart(2,"0");

    const m =
        String(
            now.getMinutes()
        ).padStart(2,"0");

    const s =
        String(
            now.getSeconds()
        ).padStart(2,"0");

    document.getElementById(
        "clock"
    ).textContent =
        h + ":" + m + ":" + s;
}

setInterval(
    updateClock,
    1000
);

updateClock();


/* =========================================================
   ENTER KEY
========================================================= */

input.addEventListener(
    "keydown",
    function(event){

        if(
            event.key === "Enter"
            && !event.shiftKey
        ){

            event.preventDefault();

            sendMessage();
        }

    }
);


/* =========================================================
   ESCAPE HTML
========================================================= */

function escapeHtml(text){

    const div =
        document.createElement(
            "div"
        );

    div.textContent =
        text;

    return div.innerHTML;
}


/* =========================================================
   ADD MESSAGE
========================================================= */

function addMessage(
    type,
    text,
    sources=[]
){

    const welcome =
        document.getElementById(
            "welcome"
        );

    if(welcome){
        welcome.remove();
    }


    const message =
        document.createElement(
            "div"
        );

    message.className =
        "message " + type;


    let html =
        '<div class="label">'
        +
        (
            type === "user"
            ? "ZIHAD"
            : "NEXA"
        )
        +
        "</div>"
        +
        escapeHtml(text)
            .replace(
                /\n/g,
                "<br>"
            );


    if(
        Array.isArray(sources)
        && sources.length
    ){

        html +=
            '<div class="sources">'
            +
            '<div class="sources-title">'
            +
            "WEB SOURCES"
            +
            "</div>";


        sources.forEach(
            function(source){

                const url =
                    escapeHtml(
                        source.url || ""
                    );

                const title =
                    escapeHtml(
                        source.title
                        || "Source"
                    );


                html +=
                    '<a href="'
                    +
                    url
                    +
                    '" target="_blank" '
                    +
                    'rel="noopener noreferrer">'
                    +
                    title
                    +
                    "</a>";

            }
        );


        html +=
            "</div>";
    }


    message.innerHTML =
        html;

    messages.appendChild(
        message
    );

    messages.scrollTop =
        messages.scrollHeight;
}


/* =========================================================
   TYPING
========================================================= */

function showTyping(){

    const message =
        document.createElement(
            "div"
        );

    message.id =
        "typing";

    message.className =
        "message nexa";


    message.innerHTML =
        '<div class="label">'
        +
        "NEXA • PROCESSING"
        +
        "</div>"
        +
        '<div style="color:#00eaff;letter-spacing:5px">'
        +
        "•••"
        +
        "</div>";


    messages.appendChild(
        message
    );

    messages.scrollTop =
        messages.scrollHeight;
}


function removeTyping(){

    const typing =
        document.getElementById(
            "typing"
        );

    if(typing){
        typing.remove();
    }
}


/* =========================================================
   SPEECH OUTPUT
========================================================= */

function speak(text){

    if(
        !("speechSynthesis" in window)
    ){
        return;
    }

    window.speechSynthesis.cancel();


    const speech =
        new SpeechSynthesisUtterance(
            text
        );

    speech.rate =
        0.94;

    speech.pitch =
        0.88;

    speech.volume =
        0.82;


    window.speechSynthesis.speak(
        speech
    );
}


/* =========================================================
   QUICK ACTION
========================================================= */

function quickSend(text){

    input.value =
        text;

    sendMessage();
}


/* =========================================================
   SEND MESSAGE
========================================================= */

async function sendMessage(){

    const message =
        input.value.trim();


    if(
        !message
        || sendButton.disabled
    ){
        return;
    }


    addMessage(
        "user",
        message
    );


    input.value =
        "";

    sendButton.disabled =
        true;

    showTyping();


    try{

        const response =
            await fetch(
                "/chat",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:JSON.stringify({
                        message:message,
                        user_id:USER_ID
                    })
                }
            );


        if(!response.ok){

            throw new Error(
                "Server error"
            );
        }


        const data =
            await response.json();


        removeTyping();


        const reply =
            data.reply
            ||
            "I could not generate a response, Sir.";


        addMessage(
            "nexa",
            reply,
            data.sources || []
        );


        speak(reply);


    }catch(error){

        removeTyping();


        addMessage(
            "nexa",
            "Connection error, Sir. Please check Flask and Ollama."
        );

    }finally{

        sendButton.disabled =
            false;

        input.focus();
    }
}


/* =========================================================
   VOICE INPUT
========================================================= */

function startVoice(){

    const SpeechRecognition =
        window.SpeechRecognition
        ||
        window.webkitSpeechRecognition;


    if(!SpeechRecognition){

        alert(
            "Voice recognition is not supported in this browser."
        );

        return;
    }


    const recognition =
        new SpeechRecognition();


    recognition.lang =
        "en-US";

    recognition.interimResults =
        false;

    recognition.continuous =
        false;


    recognition.onstart =
        function(){

            voiceButton.classList.add(
                "listening"
            );

            input.placeholder =
                "Listening...";
        };


    recognition.onresult =
        function(event){

            const text =
                event.results[0][0]
                    .transcript;

            input.value =
                text;

            sendMessage();
        };


    recognition.onerror =
        function(){

            voiceButton.classList.remove(
                "listening"
            );

            input.placeholder =
                "Talk to Nexa...";
        };


    recognition.onend =
        function(){

            voiceButton.classList.remove(
                "listening"
            );

            input.placeholder =
                "Talk to Nexa...";
        };


    recognition.start();
}


/* =========================================================
   START
========================================================= */

input.focus();

</script>

</body>
</html>
"""


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML_PAGE
    )


@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    data = request.get_json(
        silent=True
    ) or {}

    message = str(
        data.get(
            "message",
            ""
        )
    ).strip()

    user_id = ensure_user(
        str(
            data.get(
                "user_id",
                DEFAULT_USER_ID
            )
        )
    )

    if not message:

        return jsonify({
            "success": False,
            "reply": "Please say something, Sir.",
            "sources": []
        })


    result = process_message(
        user_id,
        message
    )


    return jsonify({
        "success": True,
        **result
    })


@app.route("/health")
def health():

    ollama_connected = False
    model_available = False

    try:

        response = requests.get(
            "http://localhost:11434/api/tags",
            timeout=3
        )

        if response.ok:

            ollama_connected = True

            data = response.json()

            models = data.get(
                "models",
                []
            )

            model_names = [
                str(
                    item.get(
                        "name",
                        ""
                    )
                )
                for item in models
            ]

            model_available = any(
                name == MODEL
                or name.startswith(
                    MODEL + ":"
                )
                for name in model_names
            )

    except Exception:
        pass


    return jsonify({

        "assistant":
            ASSISTANT_NAME,

        "creator":
            CREATOR_NAME,

        "model":
            MODEL,

        "model_available":
            model_available,

        "ollama":
            ollama_connected,

        "database":
            DB_FILE,

        "backup":
            BACKUP_FILE,

        "memory":
            True,

        "web_search":
            True,

        "status":
            "READY"
            if (
                ollama_connected
                and model_available
            )
            else "CHECK_OLLAMA"
    })


@app.route(
    "/memory/<user_id>"
)
def memory_api(user_id):

    return jsonify({
        "user_id": user_id,
        "memories": get_memory(
            user_id
        )
    })


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    init_database()

    seed_zihad_profile()


    print("\n" + "=" * 60)

    print(
        "NEXA AI PERSONAL ASSISTANT"
    )

    print(
        "Assistant :",
        ASSISTANT_NAME
    )

    print(
        "Creator   :",
        CREATOR_NAME
    )

    print(
        "Model     :",
        MODEL
    )

    print(
        "Database  :",
        DB_FILE
    )

    print(
        "Backup    :",
        BACKUP_FILE
    )

    print(
        "\nMemory system: READY"
    )

    print(
        "Identity system: LOCKED"
    )

    print(
        "Creator identity: LOCKED"
    )

    print(
        "\nAttention test:"
    )

    print(
        "Type: Nexa"
    )

    print(
        ATTENTION_REPLY
    )

    print(
        "\nPC  : http://127.0.0.1:5000"
    )

    print(
        "LAN : http://10.18.87.163:5000"
    )

    print(
        "Health: http://127.0.0.1:5000/health"
    )

    print(
        "=" * 60 + "\n"
    )


    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )