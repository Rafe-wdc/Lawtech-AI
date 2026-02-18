import chromadb
from dotenv import load_dotenv
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
PPLX_API_KEY = os.getenv('PPLX_API_KEY')

# Validate required API keys
_missing_keys = [k for k, v in {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
}.items() if not v]
if _missing_keys:
    raise ValueError(f"Missing required environment variables: {', '.join(_missing_keys)}. Check your .env file.")

# Clear Chroma system cache
chromadb.api.client.SharedSystemClient.clear_system_cache()
from routes import test
from routes import llm_answer
from routes import mainqa
from routes import mainqa_test
from routes import delete_vectordb
# === Initialize FastAPI ===
app = FastAPI(title="Legal AI API", version="1.0.0")

# === Enable CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust to specific domains in production
    allow_credentials=False,  # cannot be True with wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(test.router)
app.include_router(llm_answer.router)
app.include_router(mainqa.router)
app.include_router(mainqa_test.router)
app.include_router(delete_vectordb.router)

# === Run the app ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)