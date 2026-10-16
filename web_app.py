from fastapi import FastAPI

app = FastAPI(title="Nexa AI")

@app.get("/")
def home():
    return {
        "assistant": "Nexa",
        "status": "online",
        "message": "Hello! I am Nexa 🤖"
    }