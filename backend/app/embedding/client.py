from __future__ import annotations

from typing import Any

import httpx


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_ms: int = 60000,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_ms = timeout_ms
        self.http_client = http_client or httpx.Client()

    def embed_query(
        self,
        *,
        text: str,
        model: str,
        dimension: int | None = None,
        provider: str | None = None,
    ) -> list[float]:
        _ = provider
        return self.embed_documents(texts=[text], model=model, dimension=dimension)[0]

    def embed_documents(
        self,
        *,
        texts: list[str],
        model: str,
        dimension: int | None = None,
        provider: str | None = None,
    ) -> list[list[float]]:
        _ = provider
        payload: dict[str, Any] = {"input": texts, "model": model}
        if dimension:
            payload["dimensions"] = int(dimension)
        response = self.http_client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout_ms / 1000,
        )
        response.raise_for_status()
        data = response.json()
        items = data.get("data")
        if not isinstance(items, list):
            raise ValueError("Embedding response data must be a list.")
        ordered = sorted(items, key=lambda item: int(item.get("index") or 0))
        vectors: list[list[float]] = []
        for item in ordered:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list):
                raise ValueError("Embedding item is missing embedding vector.")
            vectors.append([float(value) for value in embedding])
        return vectors
