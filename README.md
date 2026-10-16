# Nexa AI Personal Assistant

Nexa is a personal AI assistant project built with Python. It aims to provide natural conversations, voice interaction, personal memory, web search, and helpful everyday assistance.

## Features

- **AI Conversation:** Answers questions and helps explain concepts.
- **Voice Interaction:** Supports voice input and text-to-speech where configured.
- **Personal Memory:** Designed to use locally stored memory.
- **Web Search:** Attempts to retrieve online information for current or factual questions.
- **Writing Assistance:** Helps draft emails, letters, applications, guides, and other text.
- **Everyday Assistance:** Designed to help with general questions and tasks.

## Technology Stack

- Python
- Ollama for local language model integration
- Voice recognition and text-to-speech components
- Web search integrations

## Project Files

- `main.py` — Main assistant application
- `requirements.txt` — Python dependencies
- `requirements_voice.txt` — Voice-related dependencies
- `server.py` — Server component
- `web_app.py` — Web application component

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/mdzihad-eng/Nexa-ai-personal-assistant.git
cd Nexa-ai-personal-assistant
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

Activate it on Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements_voice.txt
```

### 4. Set up the language model

Install Ollama from its official website and download a compatible model. The current application configuration may use `qwen2.5:3b`.

### 5. Run the assistant

```bash
python main.py
```

Follow the application's prompts and configure any required services before using optional features.

## Security and Privacy

- Never commit API keys, passwords, or private credentials.
- Keep personal memory files and local databases out of public repositories.
- Review configuration files before publishing them.

## Limitations

Nexa is an ongoing personal project. Response quality, live web search, weather information, and voice features depend on the configured model, internet connection, and available services. Results may be incomplete or incorrect, so important information should be verified.

## Future Improvements

- Faster responses and improved streaming
- Better Bangla and multilingual voice recognition
- More reliable live information retrieval
- Improved memory management
- Better error handling and testing

## Author

**Zihad Hasan Joy**

---

*Built as a personal AI assistant project and continuously improved through development and testing.*
