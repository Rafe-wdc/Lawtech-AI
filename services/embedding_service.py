"""Embedding microservice — loads models once, serves vectors via HTTP.

Solves the multi-worker OOM problem: instead of each uvicorn worker loading
its own copy of BGE-large-en-v1.5 (~1.3GB), this service loads it once and
all API workers call it via HTTP.

Run:
    python -m services.embedding_service

Endpoints:
    POST /embed          — embed one or more texts, returns vectors
    GET  /health         — health check with loaded model info
"""

from __future__ import annotations

import threading
import time
import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from core.settings import EMBEDDING_MODELS

log = logging.getLogger("EmbeddingService")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# --- App ---

app = FastAPI(title="Embedding Service", version="1.0.0")

# --- Lazy Model Loading (thread-safe, one model at a time) ---

_models: dict = {}
_lock = threading.Lock()


def _get_model(name: str):
    """Lazy-load a model on first request (thread-safe)."""
    if name in _models:
        return _models[name]

    with _lock:
        # Double-check after acquiring lock
        if name in _models:
            return _models[name]

        from langchain_community.embeddings import HuggingFaceEmbeddings

        if name == "retriever":
            log.info("Loading retriever model (BGE-large-en-v1.5)...")
            start = time.time()
            _models["retriever"] = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODELS["retriever"],
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
            log.info("Retriever model loaded in %.1fs", time.time() - start)

        elif name == "qa":
            log.info("Loading QA model (all-MiniLM-L6-v2)...")
            start = time.time()
            _models["qa"] = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODELS["pdf_qa"],
                model_kwargs={"device": "cpu"},
            )
            log.info("QA model loaded in %.1fs", time.time() - start)

        else:
            raise ValueError(f"Unknown model: {name}")

        return _models[name]


# --- Schemas ---

class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=100)
    model: str = Field("retriever", pattern=r"^(retriever|qa)$")


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    count: int


# --- Endpoints ---

@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    """Embed one or more texts using the specified model."""
    model = _get_model(req.model)
    vectors = model.embed_documents(req.texts)
    return EmbedResponse(vectors=vectors, model=req.model, count=len(vectors))


@app.get("/health")
def health():
    """Health check with loaded model info."""
    return {
        "status": "ok",
        "models_loaded": list(_models.keys()),
        "available_models": ["retriever", "qa"],
    }


# --- Entrypoint ---

if __name__ == "__main__":
    import os
    port = int(os.getenv("EMBEDDING_SERVICE_PORT", "5100"))
    uvicorn.run(
        "services.embedding_service:app",
        host="0.0.0.0",
        port=port,
        workers=1,  # Single process — models loaded once
    )
