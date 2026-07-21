"""DashScope embedding service — text-embedding-v2 (1536维)."""

import asyncio
import logging
from typing import List, Optional

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-v2"
EMBEDDING_DIMENSIONS = 1536

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


async def embed_text(text: str, model: str = EMBEDDING_MODEL) -> List[float]:
    """Embed a single text string, returning a 1536-dim vector."""
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIMENSIONS

    client = _get_client()
    resp = await client.embeddings.create(
        model=model,
        input=text,
    )
    vector = resp.data[0].embedding
    logger.debug("Embedded %d chars -> %d dims", len(text), len(vector))
    return vector


async def embed_batch(texts: List[str], model: str = EMBEDDING_MODEL) -> List[List[float]]:
    """Embed a batch of texts in one API call."""
    valid = [t for t in texts if t and t.strip()]
    if not valid:
        return []

    client = _get_client()
    resp = await client.embeddings.create(
        model=model,
        input=valid,
    )
    # resp.data is ordered to match input order
    vectors: List[List[float]] = []
    idx_map = {t: i for i, t in enumerate(valid)}
    for t in texts:
        if t and t.strip():
            vectors.append(resp.data[idx_map[t]].embedding)
        else:
            vectors.append([0.0] * EMBEDDING_DIMENSIONS)
    return vectors


def embed_text_sync(text: str) -> List[float]:
    """Sync wrapper around :func:`embed_text`.

    Use in sync contexts (FastAPI sync routes, agent node functions).
    """
    return asyncio.run(embed_text(text))


def embed_batch_sync(texts: List[str]) -> List[List[float]]:
    """Sync wrapper around :func:`embed_batch`."""
    return asyncio.run(embed_batch(texts))
