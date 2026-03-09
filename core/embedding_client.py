"""HTTP-backed LangChain Embeddings — calls the embedding microservice.

Drop-in replacement for HuggingFaceEmbeddings. Works with both:
- Direct vector computation: embeddings.embed_query(text) → List[float]
- ChromaDB integration: Chroma(embedding_function=embeddings)
"""

from __future__ import annotations

from typing import List

import requests
from langchain_core.embeddings import Embeddings


class RemoteEmbeddings(Embeddings):
    """LangChain-compatible embeddings that delegate to an HTTP service."""

    def __init__(
        self,
        base_url: str,
        model_name: str = "retriever",
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts. Called by ChromaDB for document storage."""
        # Batch in chunks of 50 to avoid oversized requests
        all_vectors = []
        for i in range(0, len(texts), 50):
            batch = texts[i : i + 50]
            resp = requests.post(
                f"{self.base_url}/embed",
                json={"texts": batch, "model": self.model_name},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            all_vectors.extend(resp.json()["vectors"])
        return all_vectors

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query text. Called by ES hybrid search and ChromaDB retrieval."""
        return self.embed_documents([text])[0]
