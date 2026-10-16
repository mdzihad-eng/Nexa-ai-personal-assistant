import ast
import requests
import json
import re
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from difflib import get_close_matches
try:
    from ddgs import DDGS
except ImportError:
    # Keep local chat, memory and calculator usable if web-search dependencies
    # have not been installed yet. web_search() reports a clear fallback.
    DDGS = None


# =========================================================
# NEXA CONFIG
# =========================================================

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = os.environ.get("NEXA_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b"

MEMORY_FILE = "memory.json"
MEMORY_BACKUP_FILE = "nexa_memory_backup.json"

ASSISTANT_NAME = "Nexa"
DEFAULT_USER_NAME = "Zihad"

USER_FULL_NAME = "Md. Zihad Hasan Joy"

USER_DETAILS = (
    "Md. Zihad Hasan Joy (CSE) in "
    "Daffodil International University (DIU)"
)

USER_PROFILE = {
    "full_name": "Md. Zihad Hasan Joy",
    "short_name": "Zihad",
    "department": "CSE",
    "university": "Daffodil International University, Dhaka",
    "date_of_birth": "2 February 2002",
    "born_in": "Thakurgaon",
    "home_address": "Hazipara, Thakurgaon",
    "height": "5 ft 11 in",
    "weight": "85 kg",
    "phone_model": "Google Pixel 7",
    "favorite_colors": "Black, Blue, Gray",
    "favorite_foods": "Kacchi Biryani, Noodles, Faluda, Pudding",
    "best_friend": "Rifat",
    "best_friend_details": "Rifat is Zihad's roommate and childhood friend.",
    "close_friends": "Mahafuz, Niloy, Sobuj, Sharif, Tutul, Alin, Oronno",
    "friends_origin": "They are all from Thakurgaon.",
    "wife_name": "Jannat (Piku)",
    "likes": "Football, AI work, UI work, and keeping birds",
    "dislikes": "Lazy people",
}

# Voice mode is optional: text-only Nexa still works without voice packages.
CURRENT_RESPONSE_LANGUAGE = "en"
VOICE_MODE_ACTIVE = False
WHISPER_MODEL = None
WHISPER_MODEL_SIZE = os.environ.get("NEXA_WHISPER_MODEL", "base").strip() or "base"  # Configurable multilingual model.


# =========================================================
# LANGUAGE DETECTION / RESPONSE STYLE
# =========================================================

def detect_response_language(text, audio_language=None):
    """Choose the reply language from the transcript first, not unreliable metadata alone."""
    value = normalize_spaces(str(text or "").lower())

    # Bengali-script transcription is the strongest signal.
    if re.search(r"[\u0980-\u09FF]", value):
        return "bn"

    words = set(re.findall(r"[a-z]+", value))
    banglish_words = {
        "amar", "amr", "ami", "tumi", "apni", "tomar", "tomake", "amake",
        "ki", "koto", "kivabe", "kibhabe", "keno", "kothay", "kokhon", "ke",
        "dao", "den", "bolo", "bolen", "hobe", "hobey", "ache", "ase", "nai",
        "naai", "korbo", "koro", "kore", "lagbe", "valo", "bhalo", "thik",
        "naki", "theke", "jonno", "dorkar", "parbo", "parbe", "chai", "chao",
        "hoy", "hoche", "hocche", "bujhi", "bujhte", "bujtese", "frnd",
        "universiti", "kemon", "aso", "acho", "naam", "nam", "bolte", "shuno",
        "kor", "koro", "koro", "hoye", "bondho", "khulbo"
    }
    if words.intersection(banglish_words):
        return "bn"

    # If the transcript itself looks like ordinary English, trust it even when
    # Whisper's automatic language metadata incorrectly says Bengali.
    english_markers = {
        "what", "which", "who", "where", "when", "why", "how", "is", "are",
        "the", "a", "an", "my", "your", "can", "could", "would", "please",
        "tell", "give", "help", "animal", "fastest", "world", "number", "price",
        "weather", "today", "time", "football", "player", "explain", "define",
        "computer", "memory", "name", "university", "created", "made", "search"
    }
    if len(words.intersection(english_markers)) >= 2:
        return "en"

    language = str(audio_language or "").lower()
    if language.startswith(("bn", "beng")):
        return "bn"
    if language.startswith("en"):
        return "en"
    return "en"


def response_language_instruction():
    if CURRENT_RESPONSE_LANGUAGE == "bn":
        return (
            "Reply in natural, clear Bangla (Bengali script). The user asked in Bangla/Banglish. "
            "Keep the key answer short unless they explicitly ask for a guide or details. "
            "Keep technical terms such as TCP, SQL, Python, API, and AI in English where natural."
        )
    return (
        "Reply in clear English because the user asked in English. "
        "Keep the key answer short unless they explicitly ask for a guide or details."
    )


# =========================================================
# MEMORY
# =========================================================

def load_memory():
    default_memory = {
        "name": DEFAULT_USER_NAME,
        "facts": [],
        "preferences": [],
        "important_information": [],
        "reminders": []
    }

    for filename in [MEMORY_FILE, MEMORY_BACKUP_FILE]:
        try:
            with open(filename, "r", encoding="utf-8") as file:
                data = json.load(file)

            if not isinstance(data, dict):
                continue

            for key in ["facts", "preferences", "important_information", "reminders"]:
                if not isinstance(data.get(key), list):
                    data[key] = []

            if not isinstance(data.get("name"), str) or not data["name"].strip():
                data["name"] = DEFAULT_USER_NAME

            return data
        except Exception:
            pass

    return default_memory


def save_memory(memory_data):
    """Persist the existing JSON schema with atomic replacement of each JSON file."""
    try:
        for filename in (MEMORY_FILE, MEMORY_BACKUP_FILE):
            target = os.path.abspath(filename)
            temporary = target + ".tmp"
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(memory_data, file, indent=4, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
    except Exception as error:
        print(f"Memory save error: {error}")
        for filename in (MEMORY_FILE + ".tmp", MEMORY_BACKUP_FILE + ".tmp"):
            try:
                if os.path.exists(filename):
                    os.remove(filename)
            except OSError:
                pass




def load_sqlite_memory_records():
    """Read existing Nexa SQLite memory in read-only spirit; never overwrite the DB."""
    db_path = "nexa_memory.db"
    if not os.path.exists(db_path):
        return []
    try:
        db = sqlite3.connect(db_path, timeout=3)
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "memories" not in tables:
            db.close()
            return []
        cols = {row[1] for row in db.execute("PRAGMA table_info(memories)").fetchall()}
        needed = {"user_id", "category", "memory_key", "memory_value"}
        if not needed.issubset(cols):
            db.close()
            return []
        order = " ORDER BY updated_at DESC" if "updated_at" in cols else ""
        rows = db.execute(
            "SELECT category, memory_key, memory_value FROM memories WHERE user_id=?" + order,
            ("zihad_creator",)
        ).fetchall()
        db.close()
        return [dict(row) for row in rows]
    except Exception as error:
        print(f"SQLite memory read skipped: {error}")
        return []


memory = load_memory()
SQLITE_MEMORY_RECORDS = load_sqlite_memory_records()


def get_saved_memory_records():
    """Read SQLite rows plus older server-style JSON backup rows, de-duplicated."""
    rows = list(SQLITE_MEMORY_RECORDS)
    legacy = memory.get("memories", []) if isinstance(memory, dict) else []
    if isinstance(legacy, list):
        for row in legacy:
            if isinstance(row, dict) and all(k in row for k in ("category", "memory_key", "memory_value")):
                rows.append({k: row.get(k, "") for k in ("category", "memory_key", "memory_value")})
    unique = []
    seen = set()
    for row in rows:
        category = str(row.get("category", "")).strip()
        key = str(row.get("memory_key", "")).strip()
        value = str(row.get("memory_value", "")).strip()
        if not key or not value:
            continue
        ident = (category.lower(), key.lower(), value.lower())
        if ident not in seen:
            seen.add(ident)
            unique.append({"category": category, "memory_key": key, "memory_value": value})
    return unique


def restore_profile_from_saved_memory():
    """Restore profile fields from SQLite/JSON so replacing main.py doesn't forget saved changes."""
    mapping = {
        ("personal details", "full_name"): "full_name",
        ("personal details", "department"): "department",
        ("personal details", "university"): "university",
        ("personal details", "date_of_birth"): "date_of_birth",
        ("personal details", "born_in"): "born_in",
        ("personal details", "home_address"): "home_address",
        ("personal details", "height"): "height",
        ("personal details", "weight"): "weight",
        ("favorites", "colors"): "favorite_colors",
        ("favorites", "favorite_colors"): "favorite_colors",
        ("favorites", "foods"): "favorite_foods",
        ("favorites", "favorite_foods"): "favorite_foods",
        ("likes", "interests"): "likes",
        ("dislikes", "dislikes"): "dislikes",
        ("friends", "close_friends"): "close_friends",
        ("wife", "wife_name"): "wife_name",
        ("personal details", "wife_name"): "wife_name",
    }
    for row in get_saved_memory_records():
        category = str(row.get("category", "")).strip().lower()
        key = str(row.get("memory_key", "")).strip().lower().replace(" ", "_").replace("-", "_")
        value = str(row.get("memory_value", "")).strip()
        field = mapping.get((category, key))
        if field and value:
            if field == "close_friends":
                USER_PROFILE[field] = value
            else:
                USER_PROFILE[field] = value
        elif category == "friends" and key == "best_friend" and value:
            match = re.match(r"([^([]+)", value)
            USER_PROFILE["best_friend"] = match.group(1).strip() if match else value
            if "(" in value:
                USER_PROFILE["best_friend_details"] = value[value.find("(") + 1:].rstrip(") .")

    # JSON preferences are direct user updates and override older seeded DB defaults.
    for item in memory.get("preferences", []):
        entry = str(item).strip()
        lower = entry.lower()
        if ":" in entry and ("favorite color" in lower or "favorite colors" in lower):
            USER_PROFILE["favorite_colors"] = entry.split(":", 1)[1].strip()
        elif ":" in entry and ("favorite food" in lower or "favorite foods" in lower):
            USER_PROFILE["favorite_foods"] = entry.split(":", 1)[1].strip()

    for item in memory.get("important_information", []):
        match = re.match(r"\s*wife\s+name\s*:\s*(.+)$", str(item), re.I)
        if match and match.group(1).strip():
            USER_PROFILE["wife_name"] = match.group(1).strip()


restore_profile_from_saved_memory()


def get_user_name():
    name = memory.get("name")

    if isinstance(name, str) and name.strip():
        return name.strip()

    return DEFAULT_USER_NAME


def get_memory_context():
    name = get_user_name()

    facts = memory.get("facts", [])
    preferences = memory.get("preferences", [])
    important = memory.get(
        "important_information",
        []
    )

    context = f"""
User name: {name}
Full name: {USER_PROFILE["full_name"]}
Department: {USER_PROFILE["department"]}
University: {USER_PROFILE["university"]}
Date of birth: {USER_PROFILE["date_of_birth"]}
Born in: {USER_PROFILE["born_in"]}
Home: {USER_PROFILE["home_address"]}
Height: {USER_PROFILE["height"]}
Weight: {USER_PROFILE["weight"]}
Favorite colors: {USER_PROFILE["favorite_colors"]}
Favorite foods: {USER_PROFILE["favorite_foods"]}
Best friend: {USER_PROFILE["best_friend"]}
Best friend details: {USER_PROFILE["best_friend_details"]}
Close friends: {USER_PROFILE["close_friends"]}
Friends origin: {USER_PROFILE["friends_origin"]}
Wife: {USER_PROFILE["wife_name"]}
Likes: {USER_PROFILE["likes"]}
Dislikes: {USER_PROFILE["dislikes"]}
"""

    if facts:
        context += "\nKnown facts:\n"

        for item in facts:
            context += f"- {item}\n"

    if preferences:
        context += "\nUser preferences:\n"

        for item in preferences:
            context += f"- {item}\n"

    if important:
        context += "\nImportant remembered information:\n"

        for item in important:
            context += f"- {item}\n"

    return context.strip()


# =========================================================
# OLLAMA
# =========================================================

def ask_ollama(prompt):
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "keep_alive": os.environ.get("NEXA_KEEP_ALIVE", "10m"),
                "options": {
                    "temperature": 0.2,
                    "top_p": 0.85,
                    "repeat_penalty": 1.12,
                    "num_ctx": int(os.environ.get("NEXA_NUM_CTX", "3072")),
                    "num_predict": 480 if ("GUIDANCE MODE:" in prompt or "ready-to-send email" in prompt.lower()) else 180
                }
            },
            timeout=int(os.environ.get("NEXA_LLM_TIMEOUT", "60"))
        )

        response.raise_for_status()

        data = response.json()

        return data.get(
            "response",
            ""
        ).strip()

    except Exception as error:
        print(f"Local AI unavailable: {error}")
        return "AI error: local model unavailable"


# =========================================================
# BASIC TEXT NORMALIZATION
# =========================================================

def normalize_spaces(text):
    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


# =========================================================
# COMMON SPELLING CORRECTION
# =========================================================

COMMON_CORRECTIONS = {
    "helo": "hello",
    "hllo": "hello",
    "helllo": "hello",

    "hii": "hi",
    "hiii": "hi",
    "heyy": "hey",

    "wht": "what",
    "wat": "what",
    "whaat": "what",
    "whatt": "what",

    "whos": "who is",
    "whre": "where",
    "wher": "where",
    "wer": "where",

    "hw": "how",
    "hwo": "how",
    "hows": "how",

    "whn": "when",
    "wen": "when",

    "wich": "which",
    "whcih": "which",

    "abt": "about",
    "abou": "about",

    "ur": "your",
    "u": "you",
    "r": "are",

    "yr": "your",

    "thnks": "thanks",
    "tnx": "thanks",
    "thx": "thanks",
    "thanx": "thanks",

    "plz": "please",
    "pls": "please",

    "creatd": "created",
    "cretaed": "created",
    "creted": "created",
    "creat": "create",

    "techer": "teacher",
    "teahcer": "teacher",

    "tomorow": "tomorrow",
    "tomoroww": "tomorrow",

    "toddy": "today",
    "tody": "today",
    "todai": "today",

    "currnt": "current",
    "curent": "current",

    "tim": "time",
    "tme": "time",

    "nam": "name",
    "nme": "name",

    "favrite": "favorite",
    "favouritee": "favorite",

    "mch": "much",
    "mny": "many",

    "ipone": "iphone",
    "iphne": "iphone",
    "iphon": "iphone",

    "samsng": "samsung",
    "samsumg": "samsung",

    "onepluse": "oneplus",
    "onepls": "oneplus",

    "xiaome": "xiaomi",
    "xiaomi": "xiaomi",

    "misn": "mission",
    "mison": "mission",

    "criket": "cricket",
    "crickte": "cricket",

    "banglads": "bangladesh",
    "bangladsh": "bangladesh",

    "latestt": "latest",
    "lstest": "latest",

    "msssi": "messi",
    "messi": "messi",

    "nasa": "nasa"
}


COMMON_CORRECTIONS.update({
    "grildfrind": "girlfriend",
    "girlfirend": "girlfriend",
    "xioami": "xiaomi",
    "universiti": "university",
    "frnd": "friend",
    "colur": "color",
    "colour": "color",
    "collor": "color",
    "weite": "write",
    "writ": "write",
    "leving": "leaving",
    "leavingg": "leaving",
    "collage": "college",
    "colage": "college",
    "teachar": "teacher",
    "riminder": "reminder",
    "remnder": "reminder",
    "frnd": "friend",
    "frnds": "friends",
    "universiti": "university",
    "univercity": "university",
    "deptt": "department",
    "fav": "favorite",
    "koto": "how much",
})



def correct_text(text):
    """
    Fast local correction.
    It fixes common mistakes without aggressively changing
    product/model names.
    """

    words = text.split()
    corrected = []

    for word in words:
        leading = ""
        trailing = ""

        while word and word[0] in "\"'([{":
            leading += word[0]
            word = word[1:]

        while word and word[-1] in ".,!?;:)]}\"'":
            trailing = word[-1] + trailing
            word = word[:-1]

        lower_word = word.lower()

        replacement = COMMON_CORRECTIONS.get(
            lower_word,
            word
        )

        corrected.append(
            leading + replacement + trailing
        )

    result = normalize_spaces(
        " ".join(corrected)
    )

    # Common Banglish spellings that are safe to normalize locally.
    result = re.sub(r"\buniversiti\b", "university", result, flags=re.IGNORECASE)
    result = re.sub(r"\bfrnd\b", "friend", result, flags=re.IGNORECASE)
    result = re.sub(r"\bfrnds\b", "friends", result, flags=re.IGNORECASE)
    result = re.sub(r"\bfavrite\b", "favorite", result, flags=re.IGNORECASE)
    return normalize_spaces(result)


# =========================================================
# AI SMART TEXT UNDERSTANDING
# =========================================================

def needs_ai_correction(original, corrected):
    """
    AI correction is used only when the local correction
    did not completely solve the text.

    Avoid unnecessary AI calls for clean sentences.
    """

    if not original.strip():
        return False

    if original.lower().strip() != corrected.lower().strip():
        return False

    words = re.findall(
        r"[A-Za-z]+",
        original.lower()
    )

    if len(words) == 0:
        return False

    suspicious_patterns = [
        r"(.)\1\1\1",
        r"[a-z]{1,2}$"
    ]

    for pattern in suspicious_patterns:
        if any(
            re.search(pattern, word)
            for word in words
        ):
            return True

    return False


def ai_correct_text(text):
    """
    Uses local Qwen model as a fallback to understand
    heavily misspelled English sentences.

    It must return ONLY the corrected sentence.
    """

    prompt = f"""
You are a strict English sentence correction system.

Correct the spelling and grammar of this user message.

IMPORTANT:
- Preserve the exact meaning.
- Do NOT answer the question.
- Do NOT explain anything.
- Do NOT add information.
- Do NOT remove important words.
- Keep product names, model names, people names and brands.
- If the sentence is already understandable, make only minimal changes.
- Return ONLY the corrected sentence.

USER MESSAGE:
{text}

CORRECTED SENTENCE:
"""

    result = ask_ollama(prompt)

    if result.startswith("AI error:"):
        return text

    result = result.strip()

    result = re.sub(
        r"^(corrected sentence\s*:?)",
        "",
        result,
        flags=re.IGNORECASE
    ).strip()

    if not result:
        return text

    if len(result) > max(
        len(text) * 3,
        300
    ):
        return text

    return result


def smart_correct_text(text):
    """
    First local correction.
    Then optional AI correction for difficult typos.
    """

    local = correct_text(text)

    if local.lower() != text.lower():
        return local

    # Speech transcripts have already gone through Whisper. Avoid extra LLM correction
    # latency and accidental rewrites; the assistant should answer the recognized words.
    if VOICE_MODE_ACTIVE:
        return local

    # Avoid unnecessary Ollama calls for very short normal inputs.
    words = text.split()

    if len(words) <= 1:
        return local

    # AI fallback only for suspiciously short/misspelled input.
    suspicious_words = 0

    for word in words:
        clean = re.sub(
            r"[^A-Za-z]",
            "",
            word
        ).lower()

        if not clean:
            continue

        # Very short words ("my", "is", "who", etc.) often match typo
        # dictionary entries by accident. Do not send them to Ollama.
        if len(clean) < 4:
            continue

        close = get_close_matches(
            clean,
            list(COMMON_CORRECTIONS.keys()),
            n=1,
            cutoff=0.78
        )

        if close:
            # Do not treat a correctly spelled word as suspicious merely
            # because it is close to one of our typo keys. Example: "what"
            # is close to "wht", but it already is the correct word.
            candidate = COMMON_CORRECTIONS.get(close[0], "")
            if candidate and candidate.lower() != clean:
                suspicious_words += 1

    if suspicious_words > 0:
        return ai_correct_text(text)

    return local


# =========================================================
# DHAKA TIME
# =========================================================

def get_dhaka_datetime():
    dhaka_timezone = timezone(
        timedelta(hours=6)
    )

    return datetime.now(
        dhaka_timezone
    )


def current_time_answer(user_question=""):
    """Return current local time for a requested city, using geocoding when needed."""
    text = str(user_question or "").lower().strip()
    timezone_name = "Asia/Dhaka"
    location_name = "Dhaka"
    # Common city/time-zone aliases work without a network request.
    known_timezones = {
        "dhaka": ("Asia/Dhaka", "Dhaka"), "bangladesh": ("Asia/Dhaka", "Bangladesh"),
        "rajshahi": ("Asia/Dhaka", "Rajshahi"), "thakurgaon": ("Asia/Dhaka", "Thakurgaon"),
        "kolkata": ("Asia/Kolkata", "Kolkata"), "delhi": ("Asia/Kolkata", "Delhi"),
        "mumbai": ("Asia/Kolkata", "Mumbai"), "karachi": ("Asia/Karachi", "Karachi"),
        "tokyo": ("Asia/Tokyo", "Tokyo"), "seoul": ("Asia/Seoul", "Seoul"),
        "singapore": ("Asia/Singapore", "Singapore"), "beijing": ("Asia/Shanghai", "Beijing"),
        "shanghai": ("Asia/Shanghai", "Shanghai"), "dubai": ("Asia/Dubai", "Dubai"),
        "london": ("Europe/London", "London"), "paris": ("Europe/Paris", "Paris"),
        "berlin": ("Europe/Berlin", "Berlin"), "istanbul": ("Europe/Istanbul", "Istanbul"),
        "new york": ("America/New_York", "New York"), "los angeles": ("America/Los_Angeles", "Los Angeles"),
        "chicago": ("America/Chicago", "Chicago"), "toronto": ("America/Toronto", "Toronto"),
        "sydney": ("Australia/Sydney", "Sydney"), "melbourne": ("Australia/Melbourne", "Melbourne"),
        "ঢাকা": ("Asia/Dhaka", "ঢাকা"), "রাজশাহী": ("Asia/Dhaka", "রাজশাহী"),
        "টোকিও": ("Asia/Tokyo", "টোকিও"), "লন্ডন": ("Europe/London", "লন্ডন"),
        "নিউইয়র্ক": ("America/New_York", "নিউইয়র্ক"), "নিউ ইয়র্ক": ("America/New_York", "নিউ ইয়র্ক"),
    }
    requested_place = None
    match = re.search(r"\b(?:in|at|for|of)\s+([a-z][a-z .'-]{1,35})", text, re.I)
    if match:
        requested_place = re.split(r"\b(?:right now|now|currently|today|please|what time|time is)\b", match.group(1), maxsplit=1, flags=re.I)[0].strip(" .,!?")
    if not requested_place:
        # Bengali/Banglish city forms, e.g. "Tokyo te koyta" or "ঢাকায় কয়টা".
        for city in sorted(known_timezones, key=len, reverse=True):
            if city in text and city not in {"bangladesh"}:
                requested_place = city
                break
    if requested_place:
        found = None
        for city, data in known_timezones.items():
            if city in requested_place or requested_place in city:
                found = data
                break
        if found:
            timezone_name, location_name = found
        else:
            try:
                geo = requests.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": requested_place, "count": 1, "language": "en", "format": "json"},
                    timeout=3
                )
                geo.raise_for_status()
                places = geo.json().get("results", [])
                if places and places[0].get("timezone"):
                    timezone_name = places[0]["timezone"]
                    location_name = places[0].get("name", requested_place.title())
                else:
                    location_name = requested_place.title()
            except Exception:
                location_name = requested_place.title()
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(timezone_name))
    except Exception:
        now = get_dhaka_datetime()
        timezone_name, location_name = "Asia/Dhaka", "Dhaka"
    time_text = now.strftime("%I:%M")
    if CURRENT_RESPONSE_LANGUAGE == "bn":
        hour = now.hour
        period = "রাত" if hour >= 20 or hour < 4 else ("সকাল" if hour < 12 else ("দুপুর" if hour < 16 else "বিকেল"))
        bangla_digits = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
        return f"{location_name}-এ এখন {period} {time_text.translate(bangla_digits)}।"
    return f"The current time in {location_name} is {time_text} {now.strftime('%p')} ({now.strftime('%Z')})."


def current_date_answer():
    now = get_dhaka_datetime()
    date_text = now.strftime("%A, %d %B %Y")
    if CURRENT_RESPONSE_LANGUAGE == "bn":
        weekdays = {"Monday": "সোমবার", "Tuesday": "মঙ্গলবার", "Wednesday": "বুধবার", "Thursday": "বৃহস্পতিবার", "Friday": "শুক্রবার", "Saturday": "শনিবার", "Sunday": "রবিবার"}
        months = {"January": "জানুয়ারি", "February": "ফেব্রুয়ারি", "March": "মার্চ", "April": "এপ্রিল", "May": "মে", "June": "জুন", "July": "জুলাই", "August": "আগস্ট", "September": "সেপ্টেম্বর", "October": "অক্টোবর", "November": "নভেম্বর", "December": "ডিসেম্বর"}
        day = now.strftime("%d").translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
        year = now.strftime("%Y").translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
        return f"আজ {weekdays[now.strftime('%A')]}, {day} {months[now.strftime('%B')]} {year}।"
    return f"Today is {date_text} in Dhaka, Bangladesh."


# =========================================================
# TIME DETECTION
# =========================================================

def is_time_question(text):
    text = text.lower().strip()

    phrases = [
        "what time",
        "what is the time",
        "what's the time",
        "current time",
        "current time now",
        "current time in dhaka",
        "current time in bangladesh",
        "time now",
        "time right now",
        "what time is it",
        "what time is it now",
        "time in dhaka",
        "time on dhaka",
        "dhaka time",
        "dhaka current time",
        "time in bangladesh",
        "bangladesh time",
        "local time",
        "now time",
        "now time is dhaka",
        "now time in dhaka",
        "now time bangladesh",
        "what is dhaka time",
        "what's dhaka time",
        "what is the time in dhaka",
        "what's the time in dhaka",
        "what is the current time in dhaka",
        "what is the current time in bangladesh"
    ]

    if text in phrases or any(phrase in text for phrase in ("সময় কত", "সময় কত", "এখন কয়টা", "এখন কয়টা", "ঢাকায় এখন সময়", "ঢাকায় এখন সময়", "ekhon koyta", "ekhon koita", "dhaka te koyta", "bangladesh e koyta", "ekhon shomoy", "ekhon somoy", "time koto")):
        return True

    if (
        "time" in text
        and (
            "dhaka" in text
            or "bangladesh" in text
        )
    ):
        return True

    if "time" in text and any(prep in text for prep in (" in ", " at ", " of ", " for ")):
        return True

    if any(term in text for term in ("কয়টা", "কয়টা", "koita", "koyta")) and any(city in text for city in ("tokyo", "london", "new york", "singapore", "dubai", "ঢাকা", "টোকিও", "লন্ডন")):
        return True

    return False


# =========================================================
# DATE DETECTION
# =========================================================

def is_date_question(text):
    text = text.lower().strip()

    exact_phrases = [
        "today",
        "today's date",
        "todays date",
        "date today",
        "today date",
        "what is today",
        "what's today"
    ]

    if text in exact_phrases:
        return True

    other_phrases = [
        "what date is it",
        "what is the date",
        "what's the date",
        "what is today's date",
        "what's today's date",
        "what is todays date",
        "what's todays date",
        "what is the date today",
        "what's the date today",
        "current date",
        "today's date"
    ]

    if any(phrase in text for phrase in ("আজ কত তারিখ", "আজকে কত তারিখ", "আজ কী বার", "আজ কি বার", "আজকে কী বার", "আজকে কি বার")):
        return True

    return any(
        phrase in text
        for phrase in other_phrases
    )


# =========================================================
# CREATOR
# =========================================================

def is_creator_question(text):
    text = text.lower().strip()

    phrases = [
        "who made you",
        "who created you",
        "who built you",
        "who developed you",
        "who programmed you",
        "who designed you",
        "who is your creator",
        "who is your developer",
        "who is your maker",
        "how made you",
        "who made nexa",
        "who created nexa",
        "who built nexa",
        "who developed nexa",
        "who programmed nexa"
    ]

    if any(phrase in text for phrase in ("কে বানিয়েছে", "কে বানিয়েছে", "তোমাকে কে তৈরি করেছে", "কে তৈরি করেছে", "কে ডেভেলপ করেছে")):
        return True

    return any(
        phrase in text
        for phrase in phrases
    )


def creator_answer():
    if CURRENT_RESPONSE_LANGUAGE == "bn":
        return "Nexa তৈরি ও কনফিগার করেছে Md. Zihad Hasan Joy (CSE), Daffodil International University।"
    return "Nexa was built and configured by Md. Zihad Hasan Joy (CSE), Daffodil International University."


# =========================================================
# PERSONAL RESPONSE
# =========================================================

def personal_response(text):
    lower = text.lower().strip()

    name = get_user_name()

    # WAKE
    if lower in [
        "nexa", "hey nexa", "hello nexa", "hi nexa"
    ]:
        return "Yes Zihad Sir, I am just here."

    if lower in ["নেক্সা", "এই নেক্সা", "হে নেক্সা"]:
        return "জি, আমি এখানেই আছি।"

    # HOW ARE YOU
    if lower in [
        "how are you",
        "how r you",
        "how are u"
    ]:
        return (
            "I am fine. And you? 😊"
        )

    # USER IS FINE
    if lower in [
        "fine",
        "i am fine",
        "im fine",
        "i'm fine",
        "good",
        "i am good",
        "im good",
        "i'm good",
        "great",
        "awesome",
        "excellent",
        "okay",
        "ok",
        "alright"
    ]:
        return (
            "Glad to hear that. 😊"
        )

    # THANKS
    if lower in [
        "thanks",
        "thank you",
        "thx",
        "tnx",
        "thank u"
    ]:
        return (
            "You're welcome. 🤖"
        )

    # SIMPLE GREETINGS
    if lower in [
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening"
    ]:
        return (
            f"Hello {name}. 😊"
        )

    return None


# =========================================================
# NAME EXTRACTION
# =========================================================

def clean_name(name):
    name = name.strip()

    name = re.sub(
        r"\b(?:set|save|remember)\s+(?:this|it)\b.*$",
        "",
        name,
        flags=re.IGNORECASE
    )

    name = re.sub(
        r"\b(?:set|save|remember)\b.*$",
        "",
        name,
        flags=re.IGNORECASE
    )

    name = name.strip(
        " .,!?:;-"
    )

    return name


def extract_name_from_command(user_text):
    text = normalize_spaces(
        user_text
    )

    patterns = [
        r"^\s*my\s+name\s+is\s+(.+?)\s*$",
        r"^\s*my\s+name\s+(.+?)\s*$",
        r"^\s*set\s+my\s+name\s+(?:as\s+)?(.+?)\s*$",
        r"^\s*remember\s+my\s+name\s+is\s+(.+?)\s*$"
    ]

    for pattern in patterns:
        match = re.match(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            name = clean_name(
                match.group(1)
            )

            if name:
                return name

    return None


# =========================================================
# MEMORY QUESTION DETECTION
# =========================================================

def is_memory_question(text):
    lower = text.lower().strip()
    patterns = [
        "what is my", "what's my", "what are my", "what my",
        "who am i", "who is my", "who's my",
        "where was i", "where do i live",
        "what do you remember about me",
        "what do you know about me",
        "tell me what you remember about me", "what do you know about me",
        "what you know about me", "you know what about me", "you know about me",
        "what about me", "tell me about myself", "summarize what you know about me", "what information do you have about me"
    ]

    if any(p in lower for p in patterns):
        return True

    # Bengali-script personal-memory questions transcribed by Whisper.
    if re.search(r"আমার.*(নাম|বিশ্ববিদ্যালয়|ভার্সিটি|ইউনিভার্সিটি|বিভাগ|স্ত্রী|বউ|বন্ধু|ফ্রেন্ড|রং|রঙ|খাবার|জন্ম|উচ্চতা|ওজন|ঠিকানা|প্রিয়|পছন্দ)", lower):
        return True

    # Mixed Bangla/English personal-memory questions.
    mixed_tokens = [
        "amar name", "amar fav", "amar favorite", "amar favourite",
        "amar university", "amar department", "amar wife",
        "amar best friend", "amar food", "amar color", "amar colour",
        "amar dob", "amar birthday", "amar height", "amar weight",
        "amar home", "amar address", "amar ki", "amr "
    ]

    return any(token in lower for token in mixed_tokens)


def answer_from_memory_question(user_text):
    text = normalize_spaces(user_text.lower().strip())
    bn = CURRENT_RESPONSE_LANGUAGE == "bn"

    # A broad memory-summary request should summarize available profile/memory,
    # not fall through to the language model and claim it knows nothing.
    if any(phrase in text for phrase in (
        "what do you know about me", "what do you remember about me",
        "tell me what you remember", "tell me about myself",
        "what you know about me", "you know what about me", "you know about me", "what about me",
        "summarize what you know", "what information do you have about me",
        "আমার সম্পর্কে কী জানো", "আমার সম্পর্কে কি জানো", "আমার সম্পর্কে বল"
    )):
        summary = [
            f"Name: {USER_PROFILE['full_name']}",
            f"University/department: {USER_PROFILE['university']} — {USER_PROFILE['department']}",
            f"Interests: {USER_PROFILE['likes']}",
            f"Favorite player: Lionel Messi",
            f"Phone: {USER_PROFILE.get('phone_model', 'not saved')}"
        ]
        saved_facts = memory.get("facts", [])
        saved_prefs = memory.get("preferences", [])
        if saved_facts:
            summary.append("Remembered facts: " + "; ".join(str(x) for x in saved_facts[:8]))
        if saved_prefs:
            summary.append("Preferences: " + "; ".join(str(x) for x in saved_prefs[:8]))
        if bn:
            return "তোমার সম্পর্কে আমার কাছে যে তথ্য আছে:\n" + "\n".join("- " + line for line in summary)
        return "Here is what I currently remember about you:\n" + "\n".join("- " + line for line in summary)

    if any(term in text for term in ("phone", "mobile", "ফোন", "মোবাইল")):
        value = USER_PROFILE.get("phone_model", "এ তথ্যটি এখনো সংরক্ষিত নেই")
        return f"তোমার ফোন {value}।" if bn else f"Your phone is {value}."

    # Bengali-script utterances recognized by the voice engine.
    if re.search(r"(স্ত্রী|বউ|wife|spouse)", text):
        value = USER_PROFILE["wife_name"]
        return f"তোমার স্ত্রীর নাম {value}।" if bn else f"Your wife's name is {value}."
    if "পুরো নাম" in text or "full name" in text:
        value = USER_PROFILE["full_name"]
        return f"তোমার পুরো নাম {value}।" if bn else value
    if ("নাম" in text or "name" in text) and not any(word in text for word in (
        "বিশ্ববিদ্যালয়", "বিশ্ববিদ্যালয়", "ইউনিভার্সিটি", "ভার্সিটি", "university", "college",
        "বন্ধু", "ফ্রেন্ড", "friend", "স্ত্রী", "বউ", "wife", "spouse"
    )):
        value = USER_PROFILE["short_name"]
        return f"তোমার নাম {value}।" if bn else value
    if any(word in text for word in ("বিশ্ববিদ্যালয়", "বিশ্ববিদ্যালয়", "ইউনিভার্সিটি", "ভার্সিটি", "university", "college")):
        value = USER_PROFILE["university"]
        return f"তোমার বিশ্ববিদ্যালয় {value}।" if bn else value
    if "বিভাগ" in text or "department" in text or "dept" in text:
        value = USER_PROFILE["department"]
        return f"তোমার বিভাগ {value}।" if bn else value
    if any(word in text for word in ("জন্মস্থান", "কোথায় জন্ম", "কোথায় জন্ম", "জন্ম কোথায়", "জন্ম কোথায়", "born", "birthplace")):
        value = USER_PROFILE["born_in"]
        return f"তোমার জন্মস্থান {value}।" if bn else value
    if "ঠিকানা" in text or "বাসা" in text or "address" in text:
        value = USER_PROFILE["home_address"]
        return f"তোমার ঠিকানা {value}।" if bn else value
    if "বন্ধু" in text or "ফ্রেন্ড" in text or "best friend" in text or "bestfriend" in text or "frnd" in text:
        value = USER_PROFILE["best_friend"]
        return f"তোমার সবচেয়ে কাছের বন্ধু {value}।" if bn else value
    if any(word in text for word in ("রং", "রঙ", "color", "colour")):
        value = USER_PROFILE["favorite_colors"]
        return f"তোমার পছন্দের রং {value}।" if bn else value
    if "খাবার" in text or "food" in text:
        value = USER_PROFILE["favorite_foods"]
        return f"তোমার পছন্দের খাবার {value}।" if bn else value
    if "জন্মতারিখ" in text or "জন্ম তারিখ" in text or "জন্মদিন" in text or "birthday" in text or "dob" in text:
        value = USER_PROFILE["date_of_birth"]
        return f"তোমার জন্মতারিখ {value}।" if bn else value
    if ("উচ্চতা" in text and "ওজন" in text) or ("height" in text and "weight" in text):
        return f"উচ্চতা {USER_PROFILE['height']}। ওজন {USER_PROFILE['weight']}।" if bn else f"Height: {USER_PROFILE['height']} | Weight: {USER_PROFILE['weight']}"
    if "উচ্চতা" in text or "height" in text:
        value = USER_PROFILE["height"]
        return f"তোমার উচ্চতা {value}।" if bn else value
    if "ওজন" in text or "weight" in text:
        value = USER_PROFILE["weight"]
        return f"তোমার ওজন {value}।" if bn else value

    if "wife" in text or "spouse" in text or "amar bou" in text or "amar wife" in text:
        value = USER_PROFILE["wife_name"]
        return f"তোমার স্ত্রীর নাম {value}।" if bn else f"Your wife's name is {value}."

    if "full name" in text or "full name" in text:
        value = USER_PROFILE["full_name"]
        return f"তোমার পুরো নাম {value}।" if bn else value
    if "who am i" in text or "my name" in text or "amar name" in text:
        value = USER_PROFILE["short_name"]
        return f"তোমার নাম {value}।" if bn else value

    if "university" in text or "college" in text or "varsity" in text or "universiti" in text:
        value = USER_PROFILE["university"]
        return f"তোমার বিশ্ববিদ্যালয় {value}।" if bn else value
    if "department" in text or "dept" in text or "department" in text:
        value = USER_PROFILE["department"]
        return f"তোমার বিভাগ {value}।" if bn else value

    if "born" in text or "birth place" in text or "birthplace" in text or "jonmo" in text:
        value = USER_PROFILE["born_in"]
        return f"তোমার জন্মস্থান {value}।" if bn else value
    if "home" in text or "address" in text or "where do i live" in text:
        value = USER_PROFILE["home_address"]
        return f"তোমার ঠিকানা {value}।" if bn else value

    if "best friend" in text or "bestfriend" in text or "best frnd" in text or "frnd" in text:
        value = USER_PROFILE["best_friend"]
        return f"তোমার সবচেয়ে কাছের বন্ধু {value}।" if bn else value

    if any(k in text for k in ("favorite color", "favourite color", "favorite colour", "favourite colour", "favrite colur", "amar color", "amar colour", "fav color")):
        value = USER_PROFILE["favorite_colors"]
        return f"তোমার পছন্দের রং {value}।" if bn else value
    if "favorite food" in text or "favourite food" in text or "amar food" in text:
        value = USER_PROFILE["favorite_foods"]
        return f"তোমার পছন্দের খাবার {value}।" if bn else value

    if "date of birth" in text or "dob" in text or "birthday" in text:
        value = USER_PROFILE["date_of_birth"]
        return f"তোমার জন্মতারিখ {value}।" if bn else value

    if "height" in text and "weight" in text:
        if bn:
            return f"উচ্চতা: {USER_PROFILE['height']}। ওজন: {USER_PROFILE['weight']}।"
        return f"Height: {USER_PROFILE['height']} | Weight: {USER_PROFILE['weight']}"
    if "height" in text:
        value = USER_PROFILE["height"]
        return f"তোমার উচ্চতা {value}।" if bn else value
    if "weight" in text:
        value = USER_PROFILE["weight"]
        return f"তোমার ওজন {value}।" if bn else value

    if "dislike" in text or "don't like" in text or "do not like" in text:
        value = USER_PROFILE["dislikes"]
        return f"তুমি অলস মানুষ পছন্দ করো না।" if bn else f"You dislike {value.lower()}."

    if any(k in text for k in ("favorite player", "favourite player", "প্রিয় খেলোয়াড়", "প্রিয় খেলোয়াড়")):
        has_saved_player = any("favorite player" in str(item).lower() or "favourite player" in str(item).lower()
                               for item in memory.get("preferences", []))
        has_saved_player = has_saved_player or any(
            "favorite player" in str(row.get("memory_key", "")).lower().replace("_", " ")
            or "favourite player" in str(row.get("memory_key", "")).lower().replace("_", " ")
            for row in get_saved_memory_records()
        )
        if not has_saved_player:
            return "তোমার প্রিয় খেলোয়াড় Lionel Messi।" if bn else "Your favorite player is Lionel Messi."

    for item in memory.get("preferences", []):
        item_lower = item.lower()
        if "programming language" in text and "favorite programming language" in item_lower:
            value = item.split(":", 1)[1].strip()
            return f"তোমার পছন্দের programming language {value}।" if bn else f"Your favorite programming language is {value}."
        if ("favorite player" in text or "প্রিয় খেলোয়াড়" in text or "প্রিয় খেলোয়াড়" in text) and "favorite player" in item_lower:
            value = item.split(":", 1)[1].strip()
            return f"তোমার প্রিয় খেলোয়াড় {value}।" if bn else f"Your favorite player is {value}."
        if ("favorite team" in text or "প্রিয় দল" in text or "প্রিয় দল" in text) and "favorite team" in item_lower:
            value = item.split(":", 1)[1].strip()
            return f"তোমার প্রিয় দল {value}।" if bn else f"Your favorite team is {value}."

    for row in get_saved_memory_records():
        key = str(row.get("memory_key", "")).lower().replace("_", " ").replace("-", " ")
        value = str(row.get("memory_value", "")).strip()
        if not value:
            continue
        if "programming language" in text and "programming language" in key:
            return f"তোমার পছন্দের programming language {value}।" if bn else f"Your favorite programming language is {value}."
        if ("favorite player" in text or "favourite player" in text or "প্রিয় খেলোয়াড়" in text or "প্রিয় খেলোয়াড়" in text) and ("favorite player" in key or "favourite player" in key):
            return f"তোমার প্রিয় খেলোয়াড় {value}।" if bn else f"Your favorite player is {value}."
        if ("favorite team" in text or "favourite team" in text or "প্রিয় দল" in text or "প্রিয় দল" in text) and ("favorite team" in key or "favourite team" in key):
            return f"তোমার প্রিয় দল {value}।" if bn else f"Your favorite team is {value}."

    if any(k in text for k in ("what do you remember about me", "what do you know about me", "key things you remember", "only the key things")):
        if bn:
            return (f"নাম: {USER_PROFILE['full_name']}\nবিভাগ: {USER_PROFILE['department']}\nবিশ্ববিদ্যালয়: {USER_PROFILE['university']}\nবন্ধু: {USER_PROFILE['best_friend']}\nস্ত্রী: {USER_PROFILE['wife_name']}")
        return (f"Name: {USER_PROFILE['full_name']}\nCSE, {USER_PROFILE['university']}\nFrom: {USER_PROFILE['born_in']}\nBest friend: {USER_PROFILE['best_friend']}\nWife: {USER_PROFILE['wife_name']}")

    return None


# =========================================================
# REMINDERS
# =========================================================

def is_reminder_request(text):
    lower = text.lower().strip()

    reminder_phrases = [
        "remind me",
        "set a reminder",
        "set reminder",
        "create a reminder",
        "save a reminder",
        "save reminder",
        "my reminders",
        "show my reminders",
        "what are my reminders",
        "list reminders",
        "reminder it",
        "remind it",
        "remnder it",
    ]

    return any(phrase in lower for phrase in reminder_phrases)


def handle_reminder(user_text):
    text = normalize_spaces(user_text)
    lower = text.lower()

    # Ambiguous reminder command: never guess the reminder content.
    if lower in {
        "reminder", "reminder it", "remind it", "set reminder",
        "set a reminder", "remnder it", "রিমাইন্ডার", "মনে করিয়ে দাও"
    }:
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return "কী মনে করিয়ে দেব, আর কখন?"
        return "What should I remind you about, and when?"

    # List saved reminders.
    if (
        lower in {"my reminders", "show my reminders", "what are my reminders", "list reminders"}
    ):
        reminders = memory.get("reminders", [])
        active = [r for r in reminders if not r.get("done", False)]

        if not active:
            return "কোনো reminder সংরক্ষণ করা নেই।" if CURRENT_RESPONSE_LANGUAGE == "bn" else "No saved reminders."

        lines = ["সংরক্ষিত reminder:" if CURRENT_RESPONSE_LANGUAGE == "bn" else "Saved reminders:"]
        for index, item in enumerate(active, 1):
            note = item.get("text", "").strip()
            when = item.get("when", "") or "time not specified"
            lines.append(f"{index}. {note} — {when}")
        return "\n".join(lines)

    # Save a reminder from natural language.
    match = re.match(
        r"^remind\s+me\s+(?:to\s+)?(.+)$",
        text,
        flags=re.IGNORECASE
    )

    if not match:
        match = re.match(
            r"^(?:set\s+a?\s*reminder|create\s+a?\s*reminder|save\s+a?\s*reminder)\s*(?:to\s+)?(.+)$",
            text,
            flags=re.IGNORECASE
        )

    if match:
        body = normalize_spaces(match.group(1))
        when = ""

        # Pull a simple schedule phrase off the end without trying to infer a date.
        schedule_patterns = [
            r"\b(today|tomorrow|tonight)(?:\s+at\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?))?$",
            r"\bat\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm))$",
            r"\bon\s+(.+)$",
        ]

        for pattern in schedule_patterns:
            found = re.search(pattern, body, flags=re.IGNORECASE)
            if found:
                when = found.group(0).strip()
                body = body[:found.start()].strip(" ,-")
                break

        if not body:
            return (
                "I need the reminder text first. "
                "For example: remind me to study AI tomorrow at 8 PM."
            )

        memory.setdefault("reminders", [])
        memory["reminders"].append({
            "text": body,
            "when": when,
            "created_at": get_dhaka_datetime().isoformat(),
            "done": False,
        })
        save_memory(memory)

        if when:
            return f"Saved reminder: {body} — {when}. 🧠⏰"

        return (
            f"Saved reminder: {body}. "
            "You did not specify a time."
        )

    return None


# =========================================================
# MEMORY HANDLER
# =========================================================

def handle_memory(user_text):
    text = user_text.strip()
    lower = text.lower()

    new_name = extract_name_from_command(text)
    if new_name:
        memory["name"] = new_name
        save_memory(memory)
        return f"তোমার নাম {new_name} হিসেবে সংরক্ষণ করেছি।" if CURRENT_RESPONSE_LANGUAGE == "bn" else f"Saved your name as {new_name}."

    wife_match = re.search(
        r"\b(?:my\s+)?wife(?:'s)?\s+name\s+is\s+(.+)$",
        text,
        flags=re.IGNORECASE
    )
    if wife_match:
        value = wife_match.group(1).strip(" .,!?:;")
        memory["important_information"] = [
            x for x in memory.get("important_information", [])
            if "wife name" not in x.lower()
        ]
        memory["important_information"].append(f"Wife name: {value}")
        USER_PROFILE["wife_name"] = value
        save_memory(memory)
        return f"Saved your wife's name as {value}."

    if re.match(r"^\s*remember\b", lower):
        info = re.sub(r"^\s*remember\s+(?:that\s+)?", "", text, flags=re.IGNORECASE).strip()
        if info:
            if info not in memory["important_information"]:
                memory["important_information"].append(info)
            save_memory(memory)
            return "Saved to memory."

    if is_memory_question(text):
        answer = answer_from_memory_question(text)
        if answer:
            return answer

    return None


# =========================================================
# SAVE SIMPLE PREFERENCES
# =========================================================

def handle_preference_statement(user_text):
    text = normalize_spaces(
        user_text
    )

    lower = text.lower()

    # Favorite color
    match = re.search(
        r"\bmy\s+(?:favorite|favourite)\s+colou?r\s+is\s+(.+)$",
        text,
        flags=re.IGNORECASE
    )

    if match:
        value = match.group(1).strip(
            " .,!?"
        )

        if value:
            entry = f"Favorite colors: {value}"

            preferences = memory.setdefault(
                "preferences",
                []
            )

            # Remove previous favorite color entry
            preferences[:] = [
                item
                for item in preferences
                if "favorite color" not in item.lower()
            ]

            preferences.append(entry)

            save_memory(memory)

            return (
                f"Saved. Your favorite "
                f"color is {value}. I saved it. 🎨"
            )

    # Favorite food
    match = re.search(
        r"\bmy\s+(?:favorite|favourite)\s+food\s+is\s+(.+)$",
        text,
        flags=re.IGNORECASE
    )

    if match:
        value = match.group(1).strip(
            " .,!?"
        )

        if value:
            entry = f"Favorite food: {value}"

            preferences = memory.setdefault(
                "preferences",
                []
            )

            preferences[:] = [
                item
                for item in preferences
                if "favorite food" not in item.lower()
            ]

            preferences.append(entry)

            save_memory(memory)

            return (
                f"Saved. Your favorite "
                f"food is {value}. I saved it. 🍽️"
            )

    # Favorite player
    match = re.search(
        r"\bmy\s+(?:favorite|favourite)\s+player\s+is\s+(.+)$",
        text,
        flags=re.IGNORECASE
    )

    if match:
        value = match.group(1).strip(
            " .,!?"
        )

        if value:
            entry = f"Favorite player: {value}"

            preferences = memory.setdefault(
                "preferences",
                []
            )

            preferences[:] = [
                item
                for item in preferences
                if "favorite player" not in item.lower()
            ]

            preferences.append(entry)

            save_memory(memory)

            return (
                f"Saved. Your favorite "
                f"player is {value}. I saved it. ⚽"
            )

    # Favorite programming language
    match = re.search(
        r"\bmy\s+(?:favorite|favourite)\s+programming\s+language\s+is\s+(.+)$",
        text,
        flags=re.IGNORECASE
    )

    if match:
        value = match.group(1).strip(" .,!?")

        if value:
            entry = f"Favorite programming language: {value}"
            preferences = memory.setdefault("preferences", [])
            preferences[:] = [
                item for item in preferences
                if "favorite programming language" not in item.lower()
            ]
            preferences.append(entry)
            save_memory(memory)
            return (
                f"Saved. Your favorite programming language "
                f"is {value}. I saved it. 💻"
            )

    # Favorite team
    match = re.search(
        r"\bmy\s+(?:favorite|favourite)\s+team\s+is\s+(.+)$",
        text,
        flags=re.IGNORECASE
    )

    if match:
        value = match.group(1).strip(
            " .,!?"
        )

        if value:
            entry = f"Favorite team: {value}"

            preferences = memory.setdefault(
                "preferences",
                []
            )

            preferences[:] = [
                item
                for item in preferences
                if "favorite team" not in item.lower()
            ]

            preferences.append(entry)

            save_memory(memory)

            return (
                f"Saved. Your favorite "
                f"team is {value}. I saved it."
            )

    return None


# =========================================================
# EMAIL / LETTER
# =========================================================

def is_writing_request(text):
    lower = text.lower()

    phrases = [
        "write an email",
        "write email",
        "write a mail",
        "write mail",
        "write a leave",
        "write leave",
        "leave application",
        "leave email",
        "leave mail",
        "write an e-mail",
        "write e-mail",
        "write a letter",
        "write letter",
        "make an email",
        "make a mail",
        "make a letter",
        "compose an email",
        "compose a mail",
        "compose a letter",
        "draft an email",
        "draft a mail",
        "draft a letter",
        "email লিখ",
        "mail লিখ",
        "letter লিখ",
        "mail likhe dao",
        "email likhe dao",
        "letter likhe dao",
        "mail likho",
        "email likho",
        "letter likho"
    ]

    return any(
        phrase in lower
        for phrase in phrases
    )


def extract_email_details(user_question):
    text = normalize_spaces(
        user_question
    )

    details = {
        "teacher_name": None,
        "date": None
    }

    # -----------------------------------------------------
    # TEACHER NAME
    # -----------------------------------------------------

    teacher_patterns = [
        # Explicit recipient phrasing used in natural-language writing requests.
        r"\b(?:email|e-mail|mail|letter|application)\s+to\s+(?:my\s+)?teacher\s+([A-Za-z][A-Za-z .'-]*)",
        r"\b(?:email|e-mail|mail|letter|application)\s+to\s+([A-Za-z][A-Za-z .'-]*)",
        r"teacher(?:'s)?\s+name\s*(?:is|=|:)?\s*([A-Za-z][A-Za-z .'-]*)",
        r"teacher\s+is\s+([A-Za-z][A-Za-z .'-]*)",
        r"teacher\s*:\s*([A-Za-z][A-Za-z .'-]*)"
    ]

    stop_words = (
        r"\b(?:date|today|tody|tomorrow|"
        r"email|e-mail|mail|letter|application|leave|request|"
        r"asking|for|please|subject|because|since|as|about|regarding|"
        r"when|while|that)\b"
    )

    for pattern in teacher_patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            name = match.group(1).strip()

            name = re.split(
                stop_words,
                name,
                maxsplit=1,
                flags=re.IGNORECASE
            )[0].strip()

            name = name.strip(
                " .,!?:;"
            )

            if name:
                details["teacher_name"] = name
                break

    # -----------------------------------------------------
    # DATE
    # -----------------------------------------------------

    lower = text.lower()

    if (
        "today" in lower
        or "tody" in lower
        or re.search(r"\bdate\s+now\b", lower)
    ):
        now = get_dhaka_datetime()

        details["date"] = now.strftime(
            "%d %B %Y"
        )

    elif "tomorrow" in lower:
        now = (
            get_dhaka_datetime()
            + timedelta(days=1)
        )

        details["date"] = now.strftime(
            "%d %B %Y"
        )

    else:
        date_patterns = [
            r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",

            r"\b\d{1,2}\s+"
            r"(?:january|february|march|april|may|june|"
            r"july|august|september|october|november|"
            r"december)"
            r"(?:\s+\d{4})?\b"
        ]

        for pattern in date_patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:
                details["date"] = match.group(0)
                break

    return details


def write_email_or_letter(user_question):
    user_name = get_user_name()

    details = extract_email_details(
        user_question
    )

    teacher_name = (
        details["teacher_name"]
        or "Teacher"
    )

    email_date = (
        details["date"]
        or get_dhaka_datetime().strftime(
            "%d %B %Y"
        )
    )

    prompt = f"""
You are Nexa, a professional personal AI assistant.

Write a ready-to-send email.

RESPONSE LANGUAGE:
{response_language_instruction()}

USER REQUEST:
{user_question}

USER NAME:
{user_name}

TEACHER NAME:
{teacher_name}

DATE:
{email_date}

RULES:

1. Use the actual teacher name:
{teacher_name}

2. Use the actual date:
{email_date}

3. Use the user's actual name:
{user_name}

4. Never use [Teacher's Name].

5. Never use [User's Name].

6. Never use [Date].

7. Never use [Start Date].

8. Never use [End Date].

9. Do not invent personal information.

10. Do not add unnecessary university information.

11. Use only the information provided by the user.

12. Make it sound naturally written by a real person, not robotic or over-formal.

13. Keep it professional, specific to the user's situation, and easy to copy and send.
14. Avoid generic filler, repeated points, and exaggerated claims.

14. Include a suitable subject.

15. If this is a leave email, clearly request leave.

16. End exactly with:

Best regards,

{user_name}

Return ONLY the finished email.
"""

    return ask_ollama(prompt)

# =========================================================
# SIMPLE LOCAL FACTS
# =========================================================

def simple_fact_answer(text):
    lower = normalize_spaces(text.lower()).rstrip(" ?!.")

    # Robust answers for common questions, including imperfect speech transcripts.
    if "animal" in lower and any(word in lower for word in ("fastest", "fast animal", "fastest animal", "fastest in the world")):
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return "ডুব দিয়ে উড়লে peregrine falcon সবচেয়ে দ্রুত; স্থলভাগে cheetah সবচেয়ে দ্রুত প্রাণী।"
        return "The peregrine falcon is fastest in a dive; the cheetah is the fastest land animal."

    if "mess" in lower and any(word in lower for word in ("number", "jersey", "shirt")):
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return "Lionel Messi সাধারণত ১০ নম্বর জার্সি পরেন। তুমি কি জার্সির নম্বর, নাকি career goals জানতে চাও?"
        return "Lionel Messi is famous for wearing No. 10. Did you mean his jersey number or career goals?"

    if "girlfriend" in lower and any(word in lower for word in ("manage", "relationship", "deal with", "handle")):
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return "সম্পর্ক ভালো রাখতে খোলামেলা কথা বলো, তার কথা মন দিয়ে শোনো, সীমারেখাকে সম্মান করো, আর সমস্যা হলে শান্তভাবে সমাধান করো।"
        return "Build trust through honest communication, listen carefully, respect her boundaries, and discuss problems calmly."

    if "qa" in lower and any(word in lower for word in ("guide", "roadmap", "from zero", "from scratch", "start")):
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return "QA শেখার ক্রম: ১) SDLC/STLC ও testing basics, ২) test case ও bug report লেখা, ৩) কোনো demo website-এ practice, ৪) Postman দিয়ে API testing, ৫) SQL basics, ৬) পরে Selenium/Playwright automation। প্রতিদিন হাতে-কলমে practice করো।"
        return "QA roadmap: 1) Learn SDLC/STLC and testing basics. 2) Write test cases and bug reports. 3) Practice on a demo website. 4) Learn API testing with Postman. 5) Learn basic SQL. 6) Add Selenium or Playwright automation later."

    english_facts = {
        "who is the best football player in the world": "There is no objective single best; Lionel Messi is widely regarded as one of the greatest ever.",
        "what is ram": "RAM is temporary memory that a computer uses for active tasks.",
        "what is an ip address": "An IP address identifies a device on a network.",
        "what is sql": "SQL is used to query and manage relational databases.",
        "what is tcp": "TCP reliably delivers data between devices over a network.",
        "what is python": "Python is a general-purpose programming language widely used in automation, data, and AI.",
        "how is fast animal in the world": "The peregrine falcon is fastest in a dive; the cheetah is the fastest land animal.",
        "how fast animal in the world": "The peregrine falcon is fastest in a dive; the cheetah is the fastest land animal.",
        "what is the fastest animal in the world": "The peregrine falcon is fastest in a dive; the cheetah is the fastest land animal.",
        "what animal is fastest in the world": "The peregrine falcon is fastest in a dive; the cheetah is the fastest land animal.",
        "which animal is fastest in the world": "The peregrine falcon is fastest in a dive; the cheetah is the fastest land animal.",
        "give me love bird birds data": "Lovebirds are small African parrots in the genus Agapornis.",
        "what are lovebirds": "Lovebirds are small, social African parrots in the genus Agapornis.",
        "what is a lovebird": "A lovebird is a small African parrot in the genus Agapornis.",
    }
    bangla_facts = {
        "who is the best football player in the world": "সর্বকালের সেরা নিয়ে মতভেদ আছে; Lionel Messi-কে অনেকেই সর্বকালের অন্যতম সেরা মনে করেন।",
        "what is ram": "RAM হলো কম্পিউটারের অস্থায়ী মেমোরি, যেখানে চলমান কাজের ডেটা রাখা হয়।",
        "what is an ip address": "IP address হলো নেটওয়ার্কে কোনো ডিভাইসকে শনাক্ত করার ঠিকানা।",
        "what is sql": "SQL দিয়ে relational database-এর ডেটা খোঁজা ও পরিচালনা করা হয়।",
        "what is tcp": "TCP নেটওয়ার্কে ডেটা নির্ভরযোগ্যভাবে পাঠাতে সাহায্য করে।",
        "what is python": "Python একটি সহজে শেখা যায় এমন general-purpose programming language, যা automation ও AI-তে ব্যবহৃত হয়।",
        "how is fast animal in the world": "ডুব দিয়ে উড়লে peregrine falcon সবচেয়ে দ্রুত; স্থলভাগে cheetah সবচেয়ে দ্রুত প্রাণী।",
        "how fast animal in the world": "ডুব দিয়ে উড়লে peregrine falcon সবচেয়ে দ্রুত; স্থলভাগে cheetah সবচেয়ে দ্রুত প্রাণী।",
        "what is the fastest animal in the world": "ডুব দিয়ে উড়লে peregrine falcon সবচেয়ে দ্রুত; স্থলভাগে cheetah সবচেয়ে দ্রুত প্রাণী।",
        "what animal is fastest in the world": "ডুব দিয়ে উড়লে peregrine falcon সবচেয়ে দ্রুত; স্থলভাগে cheetah সবচেয়ে দ্রুত প্রাণী।",
        "which animal is fastest in the world": "ডুব দিয়ে উড়লে peregrine falcon সবচেয়ে দ্রুত; স্থলভাগে cheetah সবচেয়ে দ্রুত প্রাণী।",
        "give me love bird birds data": "Lovebird হলো আফ্রিকার ছোট, সামাজিক এক ধরনের parrot; এর বৈজ্ঞানিক genus Agapornis।",
        "what are lovebirds": "Lovebird হলো আফ্রিকার ছোট, সামাজিক এক ধরনের parrot।",
        "what is a lovebird": "Lovebird হলো আফ্রিকার ছোট এক ধরনের parrot।",
    }
    table = bangla_facts if CURRENT_RESPONSE_LANGUAGE == "bn" else english_facts
    exact = table.get(lower)
    if exact:
        return exact

    # Short voice questions may be transcribed without punctuation or with Bangla wording.
    question_markers = ("what", "explain", "define", "ki", "কী", "কি", "মানে")
    if any(marker in lower for marker in question_markers):
        topic_map = [
            (("ram", "র‍্যাম", "র‍্যাম"), "what is ram"),
            (("sql", "এসকিউএল"), "what is sql"),
            (("tcp", "টিসিপি"), "what is tcp"),
            (("ip address", "আইপি address", "আইপি অ্যাড্রেস"), "what is an ip address"),
            (("python", "পাইথন"), "what is python"),
            (("lovebird", "love bird", "love birds", "লাভবার্ড", "লাভ বার্ড"), "what are lovebirds"),
        ]
        for terms, key in topic_map:
            if any(term in lower for term in terms):
                return table.get(key)
    return None



# =========================================================
# LOCAL CALCULATOR
# =========================================================

def calculate_simple_expression(text):
    lower = text.lower().strip()

    # Banglish: "1000 er 20 percent koto?" -> 200
    percent_match = re.search(
        r"^\s*(\d+(?:\.\d+)?)\s+er\s+(\d+(?:\.\d+)?)\s*%?\s*percent\s+(?:koto|how much)\s*[?!.]*\s*$",
        lower
    )
    if percent_match:
        base = float(percent_match.group(1))
        percent = float(percent_match.group(2))
        value = base * percent / 100
        return str(int(value)) if value.is_integer() else str(value)

    match = re.search(
        r"save\s+(\d+(?:\.\d+)?)\s+(?:taka|tk|bdt)\s+(?:every|per)\s+day.*?(\d+)\s*(?:year|years)",
        lower
    )
    if match:
        daily = float(match.group(1))
        years = int(match.group(2))
        total = daily * 365 * years
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return f"{years} বছরে মোট {int(total):,} টাকা জমবে।"
        return f"You will save {int(total):,} taka in {years} year{'s' if years != 1 else ''}."

    if "save" in lower and "taka" in lower and ("year" in lower or "365" in lower):
        match = re.search(r"(\d+(?:\.\d+)?)\s+(?:taka|tk|bdt)", lower)
        if match:
            daily = float(match.group(1))
            total = daily * 365
            if CURRENT_RESPONSE_LANGUAGE == "bn":
                return f"১ বছরে মোট {int(total):,} টাকা জমবে।"
            return f"You will save {int(total):,} taka in 1 year."

    if re.fullmatch(r"[\d\s+\-*/().%]+", lower):
        try:
            expr = lower.replace("%", "/100")
            tree = ast.parse(expr, mode="eval")
            allowed = (ast.Expression, ast.Constant, ast.BinOp, ast.UnaryOp,
                       ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                       ast.Pow, ast.USub, ast.UAdd, ast.Load)
            if all(isinstance(node, allowed) for node in ast.walk(tree)):
                value = eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, {})
                return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        except Exception:
            pass

    return None


# =========================================================
# PRODUCT KEYWORDS
# =========================================================

PRODUCT_KEYWORDS = [
    # Phones
    "iphone",
    "ipad",
    "samsung",
    "xiaomi",
    "redmi",
    "realme",
    "oneplus",
    "vivo",
    "oppo",
    "pixel",
    "google pixel",
    "motorola",
    "honor",
    "huawei",
    "tecno",
    "infinix",
    "nothing phone",

    # Computers
    "laptop",
    "macbook",
    "asus",
    "lenovo",
    "hp",
    "dell",
    "acer",
    "msi",
    "razer",
    "desktop",
    "pc",
    "computer",

    # Audio
    "airpods",
    "headphone",
    "headphones",
    "earbuds",
    "earphone",
    "speaker",
    "soundbar",

    # Smart devices
    "smartwatch",
    "smart watch",
    "watch",
    "tablet",
    "tv",
    "smart tv",
    "camera",

    # Hardware
    "keyboard",
    "mouse",
    "monitor",
    "router",
    "ssd",
    "hard drive",
    "hdd",
    "ram",
    "gpu",
    "graphics card",
    "processor",
    "cpu",
    "motherboard",
    "power supply",
    "psu",

    # Vehicles
    "bike",
    "motorcycle",
    "motorbike",
    "scooter",
    "car",
    "vehicle",
    "electric car",
    "electric bike",
    "ev",

    # Grocery
    "grocery",
    "rice",
    "oil",
    "milk",
    "bread",
    "salt",
    "sugar",
    "food",

    # General
    "product",
    "products",
    "ecommerce",
    "e-commerce",
    "online shop",
    "electronics",
    "shopping"
]


# =========================================================
# SPORTS KEYWORDS
# =========================================================

SPORTS_KEYWORDS = [
    "football",
    "soccer",
    "cricket",
    "tennis",
    "basketball",
    "baseball",
    "badminton",
    "volleyball",
    "hockey",
    "golf",
    "boxing",
    "ufc",
    "formula 1",
    "f1",
    "motogp",
    "wwe",
    "olympics",
    "world cup",
    "champions league",
    "premier league",
    "la liga",
    "serie a",
    "bundesliga",
    "ipl",
    "bpl"
]


# Common athlete names so current stats/profile requests are routed to sports search.
PLAYER_KEYWORDS = [
    "messi", "ronaldo", "cristiano ronaldo", "neymar", "mbappe", "haaland",
    "salah", "vinicius", "lewandowski", "modric", "de bruyne", "alcaraz",
    "djokovic", "serena williams", "carlos alcaraz", "shakib al hasan"
]


# =========================================================
# SPACE KEYWORDS
# =========================================================

SPACE_KEYWORDS = [
    "nasa",
    "space",
    "spacex",
    "artemis",
    "mars",
    "moon",
    "lunar",
    "astronaut",
    "rocket",
    "satellite",
    "iss",
    "international space station",
    "james webb",
    "jwst",
    "hubble",
    "exoplanet",
    "asteroid",
    "comet",
    "black hole",
    "galaxy",
    "universe",
    "solar system"
]


# =========================================================
# SCIENCE KEYWORDS
# =========================================================

SCIENCE_KEYWORDS = [
    "science",
    "scientific",
    "physics",
    "chemistry",
    "biology",
    "astronomy",
    "geology",
    "genetics",
    "quantum",
    "neuroscience",
    "medicine",
    "medical",
    "evolution",
    "molecule",
    "atom",
    "dna",
    "rna",
    "cell",
    "protein",
    "climate",
    "earth science"
]


# =========================================================
# ANIMAL KEYWORDS
# =========================================================

ANIMAL_KEYWORDS = [
    "animal",
    "animals",
    "wildlife",
    "mammal",
    "bird",
    "birds",
    "fish",
    "reptile",
    "amphibian",
    "insect",
    "elephant",
    "tiger",
    "lion",
    "leopard",
    "bear",
    "dog",
    "cat",
    "whale",
    "dolphin",
    "shark",
    "snake",
    "eagle",
    "penguin"
]


# =========================================================
# PRODUCT INTENT
# =========================================================

def is_product_query(text):
    lower = text.lower()

    product_intent_words = [
        "price",
        "cost",
        "how much",
        "buy",
        "where to buy",
        "spec",
        "specs",
        "specification",
        "specifications",
        "details",
        "review",
        "available",
        "availability",
        "seller",
        "shop",
        "store"
    ]

    if any(
        word in lower
        for word in product_intent_words
    ):
        return True

    # A product/model keyword alone is not enough; otherwise "What is RAM?"
    # or "What is a CPU?" incorrectly becomes a shopping search.
    product_mentions = [
        word for word in PRODUCT_KEYWORDS
        if re.search(r"\b" + re.escape(word) + r"\b", lower)
    ]
    if product_mentions and any(intent in lower for intent in [
        "price", "cost", "buy", "where to buy", "spec", "specs",
        "specification", "review", "seller", "shop", "store",
        "available", "availability", "which one", "recommend"
    ]):
        return True

    return False


# =========================================================
# SEARCH QUERY NORMALIZATION
# =========================================================

SEARCH_CORRECTIONS = {
    "xioami": "xiaomi",
    "xiaome": "xiaomi",
    "mush": "much",
    "banladesh": "bangladesh",
    "banglades": "bangladesh",
    "grildfrind": "girlfriend",
    "girldfrind": "girlfriend",
    "girlfrind": "girlfriend",
    "ipone": "iphone",
    "iphne": "iphone",
    "iphon": "iphone",
    "samsng": "samsung",
    "samsumg": "samsung",
    "onepluse": "oneplus",
    "onepls": "oneplus",
    "xiaome": "xiaomi",
    "criket": "cricket",
    "banglads": "bangladesh",
    "mison": "mission",
    "misn": "mission"
}


def normalize_search_query(query):
    words = query.split()
    result = []

    for word in words:
        clean = word.lower().strip(
            ".,!?;:"
        )

        replacement = SEARCH_CORRECTIONS.get(
            clean
        )

        if replacement:
            result.append(replacement)
        else:
            result.append(word)

    return normalize_spaces(
        " ".join(result)
    )


# =========================================================
# DIRECT LIVE WEATHER (Open-Meteo; no API key needed)
# =========================================================

WEATHER_CITIES = {
    "dhaka": (23.8103, 90.4125, "Dhaka"),
    "ঢাকা": (23.8103, 90.4125, "ঢাকা"),
    "savar": (23.8583, 90.2667, "Savar"),
    "সাভার": (23.8583, 90.2667, "সাভার"),
    "thakurgaon": (26.0337, 88.4617, "Thakurgaon"),
    "ঠাকুরগাঁও": (26.0337, 88.4617, "ঠাকুরগাঁও"),
    "chattogram": (22.3569, 91.7832, "Chattogram"),
    "chittagong": (22.3569, 91.7832, "Chattogram"),
    "চট্টগ্রাম": (22.3569, 91.7832, "চট্টগ্রাম"),
    "sylhet": (24.8949, 91.8687, "Sylhet"),
    "সিলেট": (24.8949, 91.8687, "সিলেট"),
    "khulna": (22.8456, 89.5403, "Khulna"),
    "রাজশাহী": (24.3745, 88.6042, "রাজশাহী"),
    "rajshahi": (24.3745, 88.6042, "Rajshahi"),
    "cox's bazar": (21.4272, 92.0058, "Cox's Bazar"),
    "cox bazar": (21.4272, 92.0058, "Cox's Bazar"),
    "singapore": (1.3521, 103.8198, "Singapore"),
}

WEATHER_CODE_EN = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog", 51: "light drizzle", 53: "drizzle",
    55: "dense drizzle", 56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with small hail", 99: "thunderstorm with hail"
}
WEATHER_CODE_BN = {
    0: "পরিষ্কার আকাশ", 1: "প্রায় পরিষ্কার", 2: "আংশিক মেঘলা", 3: "মেঘাচ্ছন্ন",
    45: "কুয়াশা", 48: "ঘন কুয়াশা", 51: "হালকা গুঁড়ি গুঁড়ি বৃষ্টি", 53: "গুঁড়ি গুঁড়ি বৃষ্টি",
    55: "ঘন গুঁড়ি গুঁড়ি বৃষ্টি", 56: "শীতল গুঁড়ি বৃষ্টি", 57: "ঘন শীতল গুঁড়ি বৃষ্টি",
    61: "হালকা বৃষ্টি", 63: "বৃষ্টি", 65: "ভারী বৃষ্টি", 66: "শীতল বৃষ্টি",
    67: "ভারী শীতল বৃষ্টি", 71: "হালকা তুষারপাত", 73: "তুষারপাত", 75: "ভারী তুষারপাত",
    77: "তুষারকণা", 80: "হালকা ঝরনা বৃষ্টি", 81: "বৃষ্টির ঝরনা", 82: "ভারী বৃষ্টির ঝরনা",
    85: "তুষারঝরনা", 86: "ভারী তুষারঝরনা", 95: "বজ্রঝড়",
    96: "শিলাসহ বজ্রঝড়", 99: "শিলাসহ বজ্রঝড়"
}


def is_current_weather_question(text):
    lower = str(text or "").lower()
    weather_terms = ("weather", "forecast", "temperature", "abohawa", "abohowa", "আবহাওয়া", "আবহাওয়া", "তাপমাত্রা", "বৃষ্টি হবে")
    return any(term in lower for term in weather_terms)


def get_current_weather_answer(user_question):
    """Fetch real current weather and today's high/low; return None so normal web search can fall back."""
    try:
        lower = str(user_question or "").lower()
        location = None
        lat = lon = None
        for key, coords in WEATHER_CITIES.items():
            if key in lower:
                lat, lon, location = coords
                break

        if location is None:
            # Try to resolve a requested city not in the small fast lookup table.
            match = re.search(r"(?:weather|forecast|temperature|আবহাওয়া|আবহাওয়া|তাপমাত্রা)\s+(?:in|at|for|of|in the city of)?\s*([a-z][a-z .'-]{1,35})", lower, re.I)
            if match:
                place = normalize_spaces(match.group(1))
                place = re.sub(r"\b(today|now|currently|right now|forecast|please|like|kemon)\b.*$", "", place, flags=re.I).strip(" .,?!")
                if place and place not in {"today", "now", "right now"}:
                    try:
                        geo = requests.get(
                            "https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": place, "count": 1, "language": "en", "format": "json"},
                            timeout=5
                        )
                        geo.raise_for_status()
                        places = geo.json().get("results", [])
                        if places:
                            first = places[0]
                            lat = float(first["latitude"])
                            lon = float(first["longitude"])
                            location = first.get("name", place)
                    except Exception:
                        pass

        if location is None:
            # Weather question without a place defaults to Dhaka, matching Nexa's local context.
            location = "Dhaka"
            lat, lon = WEATHER_CITIES["dhaka"][:2]

        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 1,
            "timezone": "auto"
        }
        response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=8)
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        if "temperature_2m" not in current:
            return None

        temp = round(float(current["temperature_2m"]))
        feels = round(float(current.get("apparent_temperature", temp)))
        humidity = int(current.get("relative_humidity_2m", 0) or 0)
        wind = round(float(current.get("wind_speed_10m", 0) or 0))
        code = int(current.get("weather_code", -1))
        high = round(float((daily.get("temperature_2m_max") or [temp])[0]))
        low = round(float((daily.get("temperature_2m_min") or [temp])[0]))
        rain = daily.get("precipitation_probability_max") or [None]
        rain_chance = rain[0] if rain else None

        if CURRENT_RESPONSE_LANGUAGE == "bn":
            description = WEATHER_CODE_BN.get(code, "বর্তমান আবহাওয়া")
            answer = f"{location} এখন {temp}°C, অনুভূত হচ্ছে {feels}°C। {description}; আর্দ্রতা {humidity}%, বাতাস {wind} কিমি/ঘণ্টা। আজ {low}–{high}°C"
            if rain_chance is not None:
                answer += f", বৃষ্টির সম্ভাবনা সর্বোচ্চ {int(rain_chance)}%"
            return answer + "।"

        description = WEATHER_CODE_EN.get(code, "current conditions")
        answer = f"{location} now: {temp}°C, feels like {feels}°C; {description}. Humidity {humidity}%, wind {wind} km/h. Today's range: {low}–{high}°C"
        if rain_chance is not None:
            answer += f", with up to {int(rain_chance)}% chance of rain"
        return answer + "."
    except Exception as error:
        print(f"Live weather source unavailable: {error}")
        return None


# =========================================================
# UNIVERSAL WEB SEARCH DETECTION
# =========================================================

def needs_web_search(text):
    text = text.lower().strip()

    current_words = [
        "latest", "today", "current", "now", "news", "weather",
        "live", "recent", "this week", "this month", "this year",
        "2026", "who won", "what happened", "exchange rate",
        "stock market", "availability", "where to buy", "buy"
    ]

    # "today" inside a learning/advice question does not automatically mean live web data.
    advice_patterns = [
        "what should i learn today",
        "what should i study today",
        "what can i learn today",
        "what should i do today to improve",
        "what should i learn to improve my it career",
    ]

    if any(pattern in text for pattern in advice_patterns):
        return False

    # General learning/career advice is local unless the user explicitly asks
    # for current news, live data, or product/price research.
    if is_guidance_request(text) and not is_product_query(text):
        explicit_live_research = any(term in text for term in (
            "latest news", "breaking news", "live score", "current score",
            "weather", "forecast", "exchange rate", "stock market",
            "price today", "today's price", "today price", "current price",
            "search the web", "search online", "web search"
        ))
        if not explicit_live_research:
            return False

    if any(re.search(r"\b" + re.escape(word) + r"\b", text) for word in current_words):
        return True

    if is_product_query(text):
        return True

    if any(phrase in text for phrase in ["search the web", "search online", "web search", "look it up online"]):
        return True

    research_terms = ["stats", "statistics", "career goals", "career assists", "all data", "biography", "latest record", "current record"]
    if any(term in text for term in research_terms) and any(term in text for term in SPORTS_KEYWORDS + ["messi", "ronaldo", "alcaraz", "neymar", "mbappe", "argentina"]):
        return True

    if any(word in text for word in SPORTS_KEYWORDS):
        return any(word in text for word in ["latest", "current", "today", "now", "live", "score", "result", "schedule", "match", "fixture"])

    if any(word in text for word in SPACE_KEYWORDS):
        return any(word in text for word in ["latest", "current", "today", "now", "news", "update", "launch", "mission", "status", "schedule"])

    return False


# =========================================================
# SEARCH QUERY BUILDER
# =========================================================

def build_search_queries(query):
    original = query.strip()
    corrected_original = normalize_search_query(correct_text(original))
    q = corrected_original.lower()

    queries = []

    # -----------------------------------------------------
    # GOLD
    # -----------------------------------------------------

    if "gold" in q:
        queries.extend([
            f"{corrected_original} Bangladesh gold price today BAJUS 22K 21K 18K",
            f"{corrected_original} Bangladesh gold price latest official"
        ])

        return queries

    # -----------------------------------------------------
    # CURRENCY
    # -----------------------------------------------------

    if (
        "dollar" in q
        or "usd" in q
        or "exchange rate" in q
        or "euro" in q
        or "pound" in q
    ):
        queries.extend([
            f"{corrected_original} Bangladesh latest exchange rate today",
            f"{corrected_original} BDT exchange rate today"
        ])

        return queries

    # -----------------------------------------------------
    # PRODUCT
    # -----------------------------------------------------

    if is_product_query(q):
        corrected = corrected_original

        queries.extend([
            f"{corrected} Bangladesh price BDT",
            f"{corrected} Bangladesh price seller shop",
            f"{corrected} official specifications price"
        ])

        return queries

    # -----------------------------------------------------
    # NASA / SPACE
    # -----------------------------------------------------

    if any(
        word in q
        for word in SPACE_KEYWORDS
    ):
        queries.extend([
            f"{corrected_original} latest NASA official information",
            f"{corrected_original} NASA latest"
        ])

        return queries

    # -----------------------------------------------------
    # SPORTS
    # -----------------------------------------------------

    if any(
        word in q
        for word in SPORTS_KEYWORDS + PLAYER_KEYWORDS
    ):
        queries.extend([
            f"{corrected_original} latest official profile career stats results",
            f"{corrected_original} latest score result date competition"
        ])

        return queries

    # -----------------------------------------------------
    # NEWS
    # -----------------------------------------------------

    if "news" in q:
        queries.append(
            f"{corrected_original} latest news today"
        )

        return queries

    # -----------------------------------------------------
    # WEATHER
    # -----------------------------------------------------

    if "weather" in q:
        queries.append(
            f"{corrected_original} weather today forecast"
        )

        return queries

    # -----------------------------------------------------
    # SCIENCE
    # -----------------------------------------------------

    if any(
        word in q
        for word in SCIENCE_KEYWORDS
    ):
        queries.append(
            f"{corrected_original} scientific reliable information"
        )

        return queries

    # -----------------------------------------------------
    # ANIMALS
    # -----------------------------------------------------

    if any(
        word in q
        for word in ANIMAL_KEYWORDS
    ):
        queries.append(
            f"{corrected_original} animal species habitat diet behavior facts"
        )

        return queries

    # -----------------------------------------------------
    # GENERAL
    # -----------------------------------------------------

    queries.append(
        f"{corrected_original} reliable information"
    )

    return queries


# =========================================================
# UNIVERSAL WEB SEARCH
# =========================================================

def web_search(query):
    """Fast best-effort web search: one focused query, then parallel HTML fallbacks."""
    from concurrent.futures import ThreadPoolExecutor, wait
    from bs4 import BeautifulSoup

    search_queries = build_search_queries(query)
    search_query = search_queries[0] if search_queries else normalize_spaces(query)
    print(f"🌐 Searching online: {search_query}")
    results = []
    seen = set()

    def add_result(title, body, url):
        title = normalize_spaces(str(title or ""))
        body = normalize_spaces(str(body or ""))
        url = str(url or "").strip()
        if not title and not body:
            return
        if not url.startswith("http"):
            return
        key = url.lower().split("#", 1)[0]
        if key in seen:
            return
        seen.add(key)
        results.append({"title": title[:200], "body": body[:1000], "url": url})

    # Preferred structured search provider, with a strict timeout.
    if DDGS is not None:
        try:
            try:
                client = DDGS(timeout=3)
            except TypeError:
                client = DDGS()
            for item in client.text(search_query, safesearch="moderate", max_results=5):
                add_result(item.get("title"), item.get("body") or item.get("snippet"), item.get("href") or item.get("url"))
                if len(results) >= 5:
                    break
        except Exception as error:
            print(f"Primary search source unavailable; trying alternatives ({type(error).__name__}).")

    if results:
        return results

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"}

    def scrape_provider(provider):
        url = provider + requests.utils.quote(search_query)
        page = requests.get(url, headers=headers, timeout=4)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        found = []
        if "google.com" in provider:
            found = soup.select("div.MjjYud, div.g")
        elif "bing.com" in provider:
            found = soup.select("li.b_algo")
        else:
            found = soup.select("div.result, .web-result, .result__body")
        local = []
        for container in found:
            anchor = container.select_one("a[href^='http']")
            heading = container.select_one("h3")
            if not anchor:
                continue
            title = normalize_spaces(heading.get_text(" ", strip=True)) if heading else normalize_spaces(anchor.get_text(" ", strip=True))
            href = anchor.get("href", "").strip()
            body_node = container.select_one(".VwiC3b, .IsZvec, .b_caption p, .result__snippet, p")
            body = normalize_spaces(body_node.get_text(" ", strip=True)) if body_node else normalize_spaces(container.get_text(" ", strip=True))
            if title and href.startswith("http"):
                local.append({"title": title, "body": body, "url": href})
            if len(local) >= 5:
                break
        return local

    providers = [
        "https://www.google.com/search?q=",
        "https://www.bing.com/search?q=",
        "https://html.duckduckgo.com/html/?q=",
    ]
    # Run fallbacks together so a slow provider does not block the others.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(scrape_provider, provider) for provider in providers]
        done, _pending = wait(futures, timeout=5)
        for future in done:
            try:
                for item in future.result():
                    add_result(item["title"], item["body"], item["url"])
                    if len(results) >= 8:
                        return results
            except Exception:
                continue
    return results


# =========================================================
# WEB ANSWER
# =========================================================

def answer_from_web(
    user_question,
    results
):
    if not results:
        if CURRENT_RESPONSE_LANGUAGE == "bn":
            return "এখন নির্ভরযোগ্য web result পাওয়া যায়নি, তাই নিশ্চিত উত্তর দিতে পারছি না।"
        return "I couldn't verify that from the web right now."

    # For shopping questions, discard search snippets that do not mention the
    # requested brand/model. This prevents unrelated snippets becoming made-up prices.
    if is_product_query(user_question):
        query_lower = normalize_search_query(user_question).lower()
        model_anchors = set(re.findall(r"\b[a-z]{1,10}\d{1,4}[a-z0-9-]*\b", query_lower))
        spaced_models = [
            normalize_spaces(match.group(0))
            for match in re.finditer(r"\b[a-z]{2,12}\s+\d{1,4}(?:\s+(?:pro|max|ultra|plus|fe|lite|v\d+))?\b", query_lower)
        ]
        brand_anchors = {
            brand for brand in PRODUCT_KEYWORDS
            if len(brand) >= 3 and re.search(r"\b" + re.escape(brand) + r"\b", query_lower)
        }
        anchors = model_anchors | brand_anchors
        # Generic shopping terms alone aren't useful anchors (e.g. "bike price").
        anchors -= {"price", "cost", "bike", "motorcycle", "motorbike", "phone", "laptop", "computer", "product", "products", "shop", "store", "buy"}
        if anchors or spaced_models:
            relevant = []
            for item in results:
                evidence = normalize_spaces(f"{item.get('title', '')} {item.get('body', '')}".lower())
                has_model_phrase = any(model in evidence for model in spaced_models)
                has_anchor = any(anchor in evidence for anchor in anchors)
                if (has_model_phrase and (has_anchor or not anchors)) or (has_anchor and not spaced_models):
                    relevant.append(item)
            if not relevant:
                if CURRENT_RESPONSE_LANGUAGE == "bn":
                    return "চাওয়া model-এর সঙ্গে মেলে এমন নির্ভরযোগ্য result পাইনি, তাই দাম অনুমান করে বলছি না।"
                return "I couldn't find a reliable result for that exact model, so I won't guess its price."
            results = relevant

        # A price question needs evidence containing a price/currency, not just a model mention.
        if any(term in query_lower for term in ("price", "cost", "how much", "koto", "dam", "দাম", "কত টাকা")):
            price_pattern = re.compile(r"(?:৳|bdt|tk\.?|taka|\$|€|£|usd)\s*\d[\d,.]*|\d[\d,.]*\s*(?:৳|bdt|tk\.?|taka|usd|dollars?)", re.I)
            price_rows = []
            seen_prices = set()
            for item in results:
                evidence = f"{item.get('title', '')} {item.get('body', '')}"
                matches = price_pattern.findall(evidence)
                for mention in matches:
                    price = normalize_spaces(mention)
                    if price and price.lower() not in seen_prices:
                        seen_prices.add(price.lower())
                        title = item.get("title", "Search result").strip() or "Search result"
                        url = item.get("url", "").strip()
                        price_rows.append((price, title, url))
                    if len(price_rows) >= 4:
                        break
                if len(price_rows) >= 4:
                    break

            # Unless another country is explicitly requested, this assistant is
            # configured for Bangladesh: never present a US dollar price as the local price.
            explicit_foreign_market = bool(re.search(r"\b(usa|united states|us price|india|indian price|uk price|united kingdom|europe|china price)\b", query_lower))
            if not explicit_foreign_market:
                bd_price_rows = [row for row in price_rows if re.search(r"(৳|bdt|taka|\btk\b|bangladesh|bangladeshi)", row[0] + " " + row[1], re.I)]
                if bd_price_rows:
                    price_rows = bd_price_rows
                else:
                    if CURRENT_RESPONSE_LANGUAGE == "bn":
                        return "চাওয়া ফোনটির বাংলাদেশের নির্ভরযোগ্য দাম খুঁজে পাইনি। অন্য দেশের দামকে বাংলাদেশের দাম হিসেবে বলছি না।"
                    return "I couldn't verify a reliable Bangladesh price for that phone. I won't present a foreign price as the local price."

            if not price_rows:
                if CURRENT_RESPONSE_LANGUAGE == "bn":
                    return "Model-এর result পেয়েছি, কিন্তু নির্ভরযোগ্য price evidence পাইনি। তাই দাম বানিয়ে বলছি না।"
                return "I found the model, but no reliable price evidence. I won't invent a price."

            if CURRENT_RESPONSE_LANGUAGE == "bn":
                lines = ["Search result-এ যে দামগুলো পাওয়া গেছে:"]
                lines.extend(f"- {price} — {title}" + (f" ({url})" if url else "") for price, title, url in price_rows)
                lines.append("দাম variant ও seller অনুযায়ী বদলাতে পারে; কেনার আগে source যাচাই করো।")
            else:
                lines = ["Prices reported in the search results:"]
                lines.extend(f"- {price} — {title}" + (f" ({url})" if url else "") for price, title, url in price_rows)
                lines.append("Prices may vary by variant and seller; check the source before buying.")
            return "\n".join(lines)

    source_text = ""

    for i, result in enumerate(
        results,
        1
    ):
        source_text += f"""
SOURCE {i}

TITLE:
{result["title"]}

CONTENT:
{result["body"]}

URL:
{result["url"]}
"""

    # Keep prompt size reasonable for Qwen 3B.
    source_text = source_text[:10000]

    now = get_dhaka_datetime()

    product_question = is_product_query(
        user_question
    )

    product_rules = ""

    if product_question:
        product_rules = """
PRODUCT-SPECIFIC RULES:

- Identify the exact requested product/model.
- Never substitute another model.
- Look for actual prices in the search evidence.
- Prefer Bangladesh prices when Bangladesh is requested or implied.
- Prefer BDT / ৳ when BDT evidence exists.
- Clearly distinguish official, seller, imported, used and refurbished prices.
- If multiple sellers have different prices, give the range and seller names.
- Include RAM, storage, display, processor, camera, battery or other important specs when found.
- If an exact price is not found, say:
  "I couldn't verify the exact price."
- NEVER invent a price.
- NEVER convert a price unless the conversion is clearly requested.
"""

    prompt = f"""
You are Nexa, a smart personal AI assistant.

CURRENT BANGLADESH DATE:
{now.strftime('%A, %d %B %Y')}

CURRENT BANGLADESH TIME:
{now.strftime('%I:%M:%S %p')}

USER QUESTION:
{user_question}

RELEVANT USER CONTEXT:
{get_relevant_memory_context(user_question)}

The assistant searched the web.

SEARCH RESULTS:
{source_text}

RESPONSE LANGUAGE:
{response_language_instruction()}

IMPORTANT RULES:

1. Answer the user's actual question directly.

2. Use the search results as evidence.

3. Never invent facts.

4. Never invent prices.

5. Never invent specifications.

6. Never invent scores.

7. Never invent dates.

8. Never invent availability.

9. If sources disagree, clearly mention it.

10. Prefer newer information for current questions.

11. Do not dump search results.

12. Do not mention SOURCE 1, SOURCE 2, etc.

13. Do not claim something is live unless the evidence supports it.

14. If exact information is unavailable, clearly say so.

15. Give the important answer first.

16. Keep the answer to 1-3 short sentences unless the user asks for details.

17. Answer naturally as Nexa.
18. If the search results do not directly support a fact, do not include that fact.
19. Do not add extra specifications, mileage, features, dates, or prices that are not shown in the sources.

18. If the user asked for a recommendation or buying/learning decision, synthesize the evidence and give a clear best choice when the evidence supports one.

19. If the user asked for a guide or plan, turn the evidence into practical ordered steps.

20. Do not overwhelm the user with a list of links or sources unless requested.

{product_rules}

SPORTS RULES:
- Include teams/players.
- Include event/competition.
- Include date when available.
- Include score/result when available.
- Do not invent statistics.

NASA / SPACE RULES:
- Include mission.
- Include status.
- Include date when available.
- Include important facts.

SCIENCE RULES:
- Explain accurately.
- Explain simply.

ANIMAL RULES:
- Give species, habitat, diet, behavior and conservation facts when relevant.

GROCERY / E-COMMERCE:
- Include product.
- Include size/quantity.
- Include price.
- Include seller.
- Include availability.
- Include location when available.

FINAL ANSWER:
"""

    fallback = (
        "নির্ভরযোগ্য source থেকে নিশ্চিত উত্তর তৈরি করতে পারিনি।"
        if CURRENT_RESPONSE_LANGUAGE == "bn"
        else "I couldn't prepare a reliable answer from the search results."
    )
    return generate_language_checked_answer(prompt, fallback)


def get_relevant_memory_context(user_question):
    """Return only memory relevant to the question; include a safe profile summary when asked."""
    lower = user_question.lower()

    context = []
    if any(phrase in lower for phrase in (
        "what do you know about me", "what do you remember about me",
        "tell me what you remember", "tell me about myself", "আমার সম্পর্কে"
    )):
        context.extend([
            f"Full name: {USER_PROFILE['full_name']}",
            f"Preferred name: {USER_PROFILE['short_name']}",
            f"University: {USER_PROFILE['university']}",
            f"Department: {USER_PROFILE['department']}",
            f"Interests: {USER_PROFILE['likes']}",
            f"Phone: {USER_PROFILE.get('phone_model', 'not saved')}"
        ])
        context.extend(f"Saved fact: {item}" for item in memory.get("facts", [])[:10])
        context.extend(f"Saved preference: {item}" for item in memory.get("preferences", [])[:10])
        return "\n".join(dict.fromkeys(context))

    career_terms = [
        "career", "job", "learn", "study", "skill", "it career",
        "cse", "qa", "testing", "software tester", "ui", "ux", "automation"
    ]

    if any(term in lower for term in career_terms):
        context.extend([
            f"Name: {USER_PROFILE['short_name']}",
            f"Department: {USER_PROFILE['department']}",
            f"University: {USER_PROFILE['university']}",
            f"Interests: {USER_PROFILE['likes']}",
        ])

    # Include only DB memories that overlap the user's current topic, not the whole private profile.
    rows = get_saved_memory_records()
    query_terms = set(re.findall(r"[a-z0-9]+", lower))
    for row in rows:
        key = str(row.get("memory_key", ""))
        category = str(row.get("category", ""))
        value = str(row.get("memory_value", ""))
        key_normalized = key.lower().replace("_", " ").replace("-", " ")
        category_normalized = category.lower()
        searchable = f"{category_normalized} {key_normalized} {value}".lower()
        tokens = set(re.findall(r"[a-z0-9]+", searchable))
        if query_terms.intersection(tokens) and any(term in lower for term in (key_normalized, category_normalized)):
            context.append(f"Saved memory: {category} / {key} = {value}")

    if not context:
        return "No personal memory is needed for this question."

    return "\n".join(dict.fromkeys(context))


# =========================================================
# NORMAL AI
# =========================================================

def is_guidance_request(text):
    """Return True only when the user is clearly asking for guidance/advice."""
    lower = text.lower().strip()

    guidance_phrases = [
        "guide me", "give me a guide", "i need a guide", "need a guide",
        "guideline", "give me guidelines", "show me the steps",
        "step by step", "give me a roadmap", "make me a roadmap", "roadmap for",
        "give me a plan", "make a plan", "help me plan", "planning guide",
        "what do you suggest", "what's your suggestion", "what is your suggestion",
        "give me a suggestion", "give me suggestions", "suggest a",
        "what do you recommend", "what's your recommendation", "give me a recommendation",
        "which one should i choose", "which should i choose", "which is better",
        "what is the best choice", "what's the best choice", "best way to learn",
        "what should i learn", "what should i study", "what should i focus on",
        "what should i practice", "how can i improve", "how should i prepare",
        "how can i become", "where should i start", "how to start"
    ]

    return any(phrase in lower for phrase in guidance_phrases)


def guidance_instruction(user_question):
    if not is_guidance_request(user_question):
        return ""

    return """
GUIDANCE MODE:
The user explicitly asked for guidance, a plan, a recommendation, a suggestion, a roadmap, or practical steps.

- First identify the user's goal from the question.
- Give the KEY recommendation first in 1-2 clear sentences.
- Then give a short, ordered step-by-step plan only when useful.
- Prefer practical actions, exercises, projects, tools, and examples over theory.
- If there are multiple reasonable options, compare them briefly and choose one recommendation.
- Tailor the advice to the user's relevant context when available, but do not mention private memory unless it helps the answer.
- Assume the user benefits from beginner-friendly explanations unless they clearly ask for advanced detail.
- Mention what to do today/next when useful.
- Point out common mistakes or what to avoid when useful.
- Do not invent current courses, prices, job openings, product specs, or other time-sensitive facts.
- For current recommendations, the system may use web search separately.
- End with one concrete next action the user can take immediately.
"""


def response_language_mismatch(answer, language):
    """Detect gross language mismatch without rejecting normal mixed technical terms."""
    text = str(answer or "")
    bengali_chars = len(re.findall(r"[\u0980-\u09FF]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    total_letters = bengali_chars + latin_chars
    if total_letters < 12:
        return False
    bengali_ratio = bengali_chars / total_letters
    if language == "en":
        return bengali_ratio > 0.18
    if language == "bn":
        return bengali_ratio < 0.08
    return False


def generate_language_checked_answer(prompt, fallback):
    answer = ask_ollama(prompt)
    if answer.startswith("AI error:"):
        return fallback
    if not response_language_mismatch(answer, CURRENT_RESPONSE_LANGUAGE):
        return answer

    # Retry once with a very explicit language constraint.
    required = "natural Bangla in Bengali script" if CURRENT_RESPONSE_LANGUAGE == "bn" else "English only"
    retry_prompt = f"""{prompt}

OVERRIDING FINAL OUTPUT RULE:
Return the answer ONLY in {required}. This rule overrides any conflicting instruction above.
Keep it to 1-3 short sentences. Do not invent facts. If the question is unclear, ask one brief clarification. Do not output unintelligible or unrelated text.
"""
    retry = ask_ollama(retry_prompt)
    if retry and not retry.startswith("AI error:") and not response_language_mismatch(retry, CURRENT_RESPONSE_LANGUAGE):
        return retry
    return fallback


def normal_ai_answer(user_question):
    prompt = f"""
You are Nexa, a smart personal AI assistant for Zihad.

RESPONSE LANGUAGE:
{response_language_instruction()}

RELEVANT USER CONTEXT:
{get_relevant_memory_context(user_question)}

USER:
{user_question}

{guidance_instruction(user_question)}

GENERAL RULES:

- Understand the user's intent before answering.
- Give the KEY ANSWER first.
- Be accurate and honest. Never guess when a fact is uncertain.
- For a normal question, answer directly in a concise way; do NOT turn it into a guide, roadmap, or long advice unless the user explicitly asks for guidance, a suggestion, recommendation, plan, or steps.
- For a normal factual question, prioritize the exact answer and the 1-3 most important supporting points.
- For technical questions, explain simply and use a small example only when it helps.
- Use relevant user context only when it materially improves the answer.
- Never reveal or summarize private memory unless the user asks or it is directly relevant.
- Do not claim real-time knowledge.
- Do not invent current information, prices, schedules, news, or availability.
- If you are uncertain, say so clearly instead of guessing.
- Do not say you are Qwen or Ollama; you are Nexa.
- Do not call the user "Sir" unless the user is explicitly addressing Nexa/waking Nexa.
- Sound like a thoughtful, natural human assistant: warm, clear, helpful, and not robotic.
- Match the user's requested level of detail: concise for simple questions; thorough and step-by-step for guides, help, comparisons, and learning.
- When the user speaks through voice mode, answer the recognized question; do not comment on the transcript unless it is unclear.
- For factual or location-specific questions, prefer current web evidence when available; for timeless concepts, explain clearly and do not add needless search delays.
- When the user asks for an email, letter, application, CV, caption, or other writing, produce a complete polished draft that can be copied directly.
- Use the user's preferred form of address naturally, not in every sentence.
"""

    fallback = (
        "প্রশ্নটা নির্ভরযোগ্যভাবে বুঝতে পারিনি। আরেকবার একটু সহজ করে বলো।"
        if CURRENT_RESPONSE_LANGUAGE == "bn"
        else "I couldn't generate a reliable answer just now. Please rephrase the question."
    )
    return generate_language_checked_answer(prompt, fallback)


# =========================================================
# VOICE INPUT / OUTPUT
# =========================================================

def initialize_whisper_model():
    """Load the multilingual local speech model only when voice mode is requested."""
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        print(f"Loading Bangla/English voice model '{WHISPER_MODEL_SIZE}' (first run may download model files)...")
        WHISPER_MODEL = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=os.environ.get("NEXA_WHISPER_DEVICE", "cpu"),
            compute_type=os.environ.get("NEXA_WHISPER_COMPUTE_TYPE", "int8")
        )
    return WHISPER_MODEL


def record_voice_utterance(max_speech_seconds=12, start_timeout=5, silence_seconds=0.75):
    """Record one utterance from the default microphone until a short silence."""
    import time
    import wave
    import tempfile
    import numpy as np
    import sounddevice as sd

    # Use the microphone's native rate for maximum device compatibility.
    device_info = sd.query_devices(kind="input")
    sample_rate = int(device_info.get("default_samplerate", 44100))
    block_size = max(1024, int(sample_rate * 0.25))  # about 250 ms
    frames = []
    started = False
    started_at = None
    last_voice_at = None

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_size
    ) as stream:
        # Briefly estimate background noise before listening for speech.
        noise_levels = []
        for _ in range(2):
            block, _ = stream.read(block_size)
            audio = block.astype("float32").reshape(-1)
            noise_levels.append(float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0)
        threshold = max(140.0, (sum(noise_levels) / max(len(noise_levels), 1)) * 1.8)

        print("Listening... speak now.")
        wait_started = time.time()
        while True:
            block, overflowed = stream.read(block_size)
            now = time.time()
            audio = block.reshape(-1).astype("float32")
            rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0

            if not started:
                if rms >= threshold:
                    started = True
                    started_at = now
                    last_voice_at = now
                    frames.append(block.copy())
                elif now - wait_started >= start_timeout:
                    return None
                continue

            frames.append(block.copy())
            if rms >= threshold:
                last_voice_at = now

            if now - last_voice_at >= silence_seconds:
                break
            if started_at and now - started_at >= max_speech_seconds:
                break

    if not frames:
        return None

    audio_bytes = np.concatenate(frames).astype("int16").tobytes()
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = handle.name
    handle.close()
    try:
        with wave.open(wav_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_bytes)
        return wav_path
    except Exception:
        try:
            import os
            os.unlink(wav_path)
        except OSError:
            pass
        raise


def transcript_looks_unreliable(text):
    """Reject obvious repeated-character/noise hallucinations before they reach the assistant."""
    value = normalize_spaces(str(text or ""))
    if not value:
        return True
    # Long runs such as কাাাা... or aaaaaaaaa are usually noise, not a question.
    if re.search(r"(.)\1{5,}", value, re.IGNORECASE):
        return True
    letters = re.findall(r"[A-Za-z\u0980-\u09FF]", value)
    if len(letters) < 2:
        return True
    words = re.findall(r"[A-Za-z\u0980-\u09FF]+", value)
    if len(words) >= 4:
        long_gibberish = sum(1 for word in words if len(word) > 22)
        if long_gibberish >= 2:
            return True
    # Reject a transcript dominated by repeated copies of one short token.
    if len(words) >= 6 and len(set(w.lower() for w in words)) <= 2:
        return True
    return False


def listen_for_speech():
    """Transcribe one utterance; retry Bengali/English when Whisper misidentifies the language."""
    import os
    audio_path = record_voice_utterance()
    if not audio_path:
        return "", None

    try:
        model = initialize_whisper_model()
        def run_transcription(language=None):
            # Do not bias automatic language detection toward Bangla. Use a
            # language-specific prompt only for a forced retry.
            if language == "bn":
                prompt_hint = "বাংলা কথোপকথন: আমার নাম কী, আমার বিশ্ববিদ্যালয়, কীভাবে, ঢাকা, বাংলাদেশ।"
            elif language == "en":
                prompt_hint = "Natural conversational English about everyday questions, computers, AI, football, and Bangladesh."
            else:
                prompt_hint = None
            segments, info = model.transcribe(
                audio_path,
                language=language,
                task="transcribe",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 350, "speech_pad_ms": 120},
                condition_on_previous_text=False,
                initial_prompt=prompt_hint
            )
            segments = list(segments)
            transcript = " ".join(segment.text.strip() for segment in segments).strip()
            logprobs = [getattr(segment, "avg_logprob", -2.0) for segment in segments]
            score = sum(logprobs) / len(logprobs) if logprobs else -99.0
            detected = getattr(info, "language", language)
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
            return transcript, detected, probability, score

        text, language, probability, score = run_transcription()

        # If language auto-detection is clearly wrong/uncertain, make ONE fast
        # retry in the likely language instead of decoding every utterance 3 times.
        needs_retry = language not in {"bn", "en"} or probability < 0.42 or score < -1.45
        if needs_retry:
            low = text.lower()
            english_markers = {
                "what", "which", "who", "where", "when", "why", "how", "is", "are",
                "the", "my", "your", "can", "please", "tell", "give", "help", "name",
                "weather", "time", "today", "animal", "fastest", "world", "price"
            }
            marker_count = len(set(re.findall(r"[a-z]+", low)) & english_markers)
            has_bengali_script = bool(re.search(r"[\u0980-\u09FF]", text))
            banglish_markers = {
                "amar", "amr", "ami", "tumi", "tomar", "tomake", "amake",
                "ki", "koto", "kivabe", "kibhabe", "keno", "kothay", "dao",
                "den", "bolo", "hobe", "ache", "ase", "nai", "korbo", "koro",
                "lagbe", "valo", "bhalo", "jonno", "parbo", "chai", "hoy"
            }
            words = set(re.findall(r"[a-z]+", low))
            if has_bengali_script or words.intersection(banglish_markers):
                retry_language = "bn"
            elif marker_count >= 2 or re.search(r"[A-Za-z]", text):
                retry_language = "en"
            else:
                retry_language = "bn"
            candidate = run_transcription(retry_language)
            candidate_text, candidate_lang, candidate_prob, candidate_score = candidate
            candidate_has_expected_script = (
                bool(re.search(r"[\u0980-\u09FF]", candidate_text))
                if retry_language == "bn"
                else bool(re.search(r"[A-Za-z]", candidate_text))
            )
            if candidate_text and candidate_score > -1.7 and candidate_has_expected_script:
                text, language, probability, score = candidate

        # Reject silence/noise/clearly low-confidence output instead of sending
        # random words to the AI and causing unrelated answers.
        if not text or score < -1.65 or transcript_looks_unreliable(text):
            print("I couldn't hear that clearly. Please speak a little slower, closer to the microphone, and try again.")
            return "", None

        # A transcript in a clearly unrelated script is not a valid answer.
        if language not in {"bn", "en"} and not re.search(r"[\u0980-\u09FF]", text):
            print("I couldn't identify that as Bangla or English. Please try once more.")
            return "", None

        return text, language
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass


_TTS_VOICE_CACHE = None

def select_tts_voice(language, available_voices=None):
    """Select a verified available voice, preferring Bangladesh Bangla."""
    if available_voices is None:
        return "bn-BD-PradeepNeural" if language == "bn" else "en-US-GuyNeural"
    names = {str(item.get("ShortName", "")) for item in available_voices if isinstance(item, dict)}
    if language == "bn":
        preferred = ("bn-BD-PradeepNeural", "bn-BD-NabanitaNeural", "bn-IN-TanishaaNeural")
        return next((name for name in preferred if name in names), next((name for name in sorted(names) if name.lower().startswith(("bn-bd-", "bn-in-"))), ""))
    preferred = ("en-US-GuyNeural", "en-US-AriaNeural", "en-GB-RyanNeural")
    return next((name for name in preferred if name in names), next((name for name in sorted(names) if name.lower().startswith("en-")), ""))


def speak_text(text, language=None):
    """Speak via Edge neural TTS; if the network fails, try an installed Windows SAPI voice."""
    import asyncio
    import os
    import tempfile
    import time
    if not text or not str(text).strip():
        return
    language = language or CURRENT_RESPONSE_LANGUAGE
    try:
        import edge_tts
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        global _TTS_VOICE_CACHE
        if _TTS_VOICE_CACHE is None:
            _TTS_VOICE_CACHE = asyncio.run(edge_tts.list_voices())
        voice = select_tts_voice(language, _TTS_VOICE_CACHE)
        if voice:
            temp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            audio_path = temp.name
            temp.close()
            try:
                async def save_audio():
                    communication = edge_tts.Communicate(text=str(text), voice=voice, rate="+5%")
                    await communication.save(audio_path)
                asyncio.run(save_audio())
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                pygame.mixer.music.load(audio_path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    time.sleep(0.05)
                pygame.mixer.music.unload()
                return
            finally:
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass
    except Exception as error:
        print(f"Online speech unavailable: {error}")

    # Offline fallback: Windows built-in SAPI voices. Bangla is only attempted
    # if an installed voice identifies itself as Bengali/Bangla.
    try:
        import pyttsx3
        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
        if language == "bn":
            matching = [v for v in voices if any(tag in (str(getattr(v, "id", "")) + " " + str(getattr(v, "name", ""))).lower() for tag in ("bangla", "bengali", "bn-bd", "bn-in"))]
            if not matching:
                print("No offline Bangla voice is installed. Install a Bengali Windows speech voice or reconnect internet for neural TTS.")
                engine.stop()
                return
            engine.setProperty("voice", matching[0].id)
        else:
            matching = [v for v in voices if "english" in (str(getattr(v, "name", "")) + " " + str(getattr(v, "id", ""))).lower()]
            if matching:
                engine.setProperty("voice", matching[0].id)
        engine.setProperty("rate", 185)
        engine.say(str(text))
        engine.runAndWait()
        engine.stop()
    except Exception as error:
        print(f"Voice playback unavailable: {error}")
        print("Text answers will continue to work. Check internet or install an offline system voice.")


def print_answer(answer):
    """Print every answer and speak it only while voice mode is active."""
    answer_text = str(answer or "").strip()
    # Keep ready-to-send writing clean; add the requested sign-off to normal assistant replies.
    is_copy_ready_draft = bool(re.search(r"(?im)^subject\s*:", answer_text) and re.search(r"(?i)(best regards|sincerely|ধন্যবাদান্তে|বিনীত)", answer_text))
    if answer_text and not is_copy_ready_draft and not re.search(r"(?i)(?:—|-|,)\s*zihad sir\s*$", answer_text):
        answer_text = answer_text.rstrip() + "\n— Zihad Sir"
    print("Nexa:", answer_text)
    if VOICE_MODE_ACTIVE:
        if re.search(r"[\u0980-\u09FF]", answer_text):
            spoken_language = "bn"
        else:
            spoken_language = CURRENT_RESPONSE_LANGUAGE
        speak_text(answer_text, spoken_language)


# =========================================================
# SEARCH FOR GENERAL FACTUAL QUESTIONS
# =========================================================

def should_search_general_question(text):
    """Search online for factual questions, while keeping chat, writing and local tasks fast."""
    value = normalize_spaces(str(text or "")).lower()
    if not value:
        return False
    if is_writing_request(value) or is_memory_question(value):
        return False
    if is_time_question(value) or is_date_question(value) or is_current_weather_question(value):
        return False
    if calculate_simple_expression(value) is not None:
        return False
    # Casual greetings and direct commands do not need a web request.
    casual = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "good morning", "good night", "nexa", "hey nexa", "hello nexa"}
    if value.strip(".!? ") in casual:
        return False
    # Explicit question forms in English, Bangla, or Banglish.
    question_starts = (
        "what ", "who ", "where ", "when ", "why ", "how ", "which ", "is ", "are ", "can ", "could ", "does ", "do ", "did ", "will ", "would ", "tell me ", "explain ", "define ", "compare ", "latest ", "search ",
        "ki ", "ke ", "kothay ", "kokhon ", "keno ", "kivabe ", "kibhabe ", "koto ", "kon ", "bolo ", "bujhai ", "bujhiye ", "amake bolo ", "amake bujhao ",
    )
    if "?" in value or value.startswith(question_starts):
        return True
    if re.search(r"[ঀ-৿].*(?:কি|কে|কোথায়|কোথায়|কেন|কিভাবে|কত|কোন|বল|ব্যাখ্যা)", value):
        return True
    # Most non-trivial requests for explanation/guidance benefit from sourced research.
    return is_guidance_request(value) or any(term in value for term in ("guide me", "roadmap", "recommend", "recommendation", "difference between", "pros and cons", "full details", "details about"))


# =========================================================
# SHARED REQUEST PIPELINE (typed and voice use the same path)
# =========================================================

EXIT_REQUESTED = False

def is_stop_voice_command(text):
    value = normalize_spaces(str(text or "").lower()).strip(" .,!?:;")
    compact = re.sub(r"[^a-z\u0980-\u09ff]", "", value)
    variants = {
        "stop voice", "stop listening", "exit voice", "back to text mode",
        "voice off", "stop the voice", "stop voice mode", "turn off voice",
        "ভয়েস বন্ধ", "ভয়েস বন্ধ", "ভয়েস বন্ধ করো", "ভয়েস বন্ধ করো",
        "ভয়েস বন্ধ কর", "ভয়েস বন্ধ কর", "voice bondho", "listening off"
    }
    if value in variants:
        return True
    # Whisper sometimes transcribes "stop voice" as "stop boys", "stop moot", etc.
    tokens = value.split()
    if len(tokens) <= 4 and tokens and tokens[0] in {"stop", "exit", "turn", "back"}:
        tail = " ".join(tokens[1:])
        if any(word in tail for word in ("voice", "listening", "off", "bondho", "mode")):
            return True
        if tokens[0] == "stop" and len(tokens) >= 2:
            if get_close_matches(tokens[1], ["voice", "boys", "voise", "voices", "moot", "mode"], n=1, cutoff=0.42):
                return True
    return ("voice" in value and any(word in value for word in ("stop", "off", "exit", "bondho"))) or (
        "listening" in value and any(word in value for word in ("stop", "off", "exit"))
    ) or compact in {"ভয়েসবন্দ", "ভয়েসবন্দ", "ভয়েসবন্দকরো", "ভয়েসবন্দকরো"}

def process_user_request(user_input):
    """Process one typed or transcribed request using one consistent feature pipeline."""
    global CURRENT_RESPONSE_LANGUAGE, VOICE_MODE_ACTIVE, EXIT_REQUESTED
    raw = normalize_spaces(str(user_input or ""))
    if not raw:
        return None

    lower = raw.lower().strip()
    if lower in {"exit", "quit", "bye", "close nexa"} or lower in {"বন্ধ", "বিদায়", "বিদায়"}:
        EXIT_REQUESTED = True
        return "Goodbye. 🤖"

    if is_stop_voice_command(raw):
        if VOICE_MODE_ACTIVE:
            VOICE_MODE_ACTIVE = False
            return "Voice mode off. Type normally, or type 'voice' to start again."
        return "Voice mode is already off."

    # The wake response remains exact as requested.
    if lower in {"nexa", "hey nexa", "hello nexa", "hi nexa"} or lower in {"নেক্সা", "এই নেক্সা", "হে নেক্সা"}:
        return personal_response(raw) or "Yes Zihad Sir, I am just here."

    # Identity and stored facts are handled before model or web calls.
    if is_creator_question(raw):
        return creator_answer()

    corrected = smart_correct_text(raw)
    if corrected and corrected.lower() != raw.lower():
        print(f"✏️ Understood as: {corrected}")
    question = corrected or raw

    new_name = extract_name_from_command(question)
    if new_name:
        return handle_memory(question)

    if is_time_question(question):
        return current_time_answer(question)
    if is_date_question(question):
        return current_date_answer()

    # Personal memory questions must never depend on LLM recall.
    if is_memory_question(question):
        answer = answer_from_memory_question(question)
        if answer:
            return answer

    calculation = calculate_simple_expression(question)
    if calculation is not None:
        return calculation

    if is_reminder_request(question):
        answer = handle_reminder(question)
        if answer:
            return answer

    preference = handle_preference_statement(question)
    if preference:
        return preference

    memory_response = handle_memory(question)
    if memory_response:
        return memory_response

    if is_writing_request(question):
        print("✍️ Writing...")
        return write_email_or_letter(question)

    if is_current_weather_question(question):
        weather_answer = get_current_weather_answer(question)
        if weather_answer:
            return weather_answer

    personal = personal_response(question)
    if personal:
        return personal

    # Prefer online evidence for factual questions and guides. If search fails, answer from
    # the local model or stable built-in facts instead of leaving the user without help.
    if needs_web_search(question) or should_search_general_question(question):
        results = web_search(question)
        if results:
            answer = answer_from_web(question, results)
            if answer and not answer.startswith(("AI error:", "I couldn't verify that from the web right now.", "এখন নির্ভরযোগ্য web result পাওয়া যায়নি")):
                return answer

    fact_answer = simple_fact_answer(question)
    if fact_answer:
        return fact_answer

    return normal_ai_answer(question)


# =========================================================
# MAIN
# =========================================================

def main():
    global VOICE_MODE_ACTIVE, CURRENT_RESPONSE_LANGUAGE, EXIT_REQUESTED

    print("=" * 65)
    print("🤖 NEXA - Universal Personal AI Assistant")
    print("🌐 Web research enabled (uses available search providers; internet required)")
    print("🧠 Memory Enabled")
    print("✍️ Email / Letter Enabled")
    print("🚀 NASA / Space / Science Search Enabled")
    print("🏆 Sports Search Enabled")
    print("🛒 Product / Price Search Enabled")
    print("🧠 Smart Memory Questions Enabled")
    print("💡 Type 'exit' to quit")
    print("🎙️ Type 'voice' to start Bangla/English voice mode")
    print("=" * 65)
    consecutive_empty_voice_inputs = 0

    while not EXIT_REQUESTED:
        try:
            if VOICE_MODE_ACTIVE:
                try:
                    spoken_text, detected_audio_language = listen_for_speech()
                except Exception as voice_error:
                    print(f"Voice input unavailable: {voice_error}")
                    print("Run: python -m pip install -r requirements_voice.txt")
                    print("Then check your microphone and type 'voice' again.")
                    VOICE_MODE_ACTIVE = False
                    continue
                if not spoken_text:
                    consecutive_empty_voice_inputs += 1
                    if consecutive_empty_voice_inputs >= 3:
                        VOICE_MODE_ACTIVE = False
                        consecutive_empty_voice_inputs = 0
                        print("Voice mode paused after repeated silence or unclear audio. Type 'voice' to try again.")
                    continue
                consecutive_empty_voice_inputs = 0
                user_input = spoken_text.strip()
                print(f"\nYou (voice): {user_input}")
                CURRENT_RESPONSE_LANGUAGE = detect_response_language(user_input, detected_audio_language)
            else:
                try:
                    user_input = input("\nYou: ").strip()
                except EOFError:
                    print("\nNexa: Goodbye. 🤖")
                    break

                command = user_input.lower().strip()
                if command in {"voice", "voice mode", "start voice", "jarvis mode"}:
                    try:
                        initialize_whisper_model()
                        VOICE_MODE_ACTIVE = True
                        print("Voice mode ON. Speak in Bangla or English. Say 'stop voice' to return to typing.")
                    except Exception as voice_error:
                        VOICE_MODE_ACTIVE = False
                        print(f"Could not start voice mode: {voice_error}")
                        print("Run: python -m pip install -r requirements_voice.txt")
                    continue
                CURRENT_RESPONSE_LANGUAGE = detect_response_language(user_input)

            if not user_input:
                continue
            answer = process_user_request(user_input)
            if answer:
                print_answer(answer)

        except KeyboardInterrupt:
            print("\n\nNexa: Goodbye. 🤖")
            break
        except Exception as error:
            print(f"\n⚠️ Error: {error}")
            print("Nexa is still running; try rephrasing the request.")

if __name__ == "__main__":
    main()
