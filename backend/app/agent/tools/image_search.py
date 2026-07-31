"""LangChain tool definitions for image search.

Provides ``search_local_asset`` tool decorated with ``@tool`` so it can be
used within LangChain agents or as OpenAI-compatible function calls.
"""

import logging
from typing import Optional

import requests
from langchain.tools import tool

from app.config import settings

logger = logging.getLogger(__name__)


@tool
def search_local_asset(keywords: str) -> str:
    """Search the local asset library for an image matching the keywords.

    This queries the MinIO asset bucket (or a local directory) for existing
    images whose filename or tags match the given keywords.

    Args:
        keywords: Space-separated keywords to search for in local assets.
    """
    try:
        from minio import Minio
    except ImportError:
        return '{"error": "MinIO client not installed"}'

    if not settings.minio_endpoint or not settings.minio_bucket:
        return '{"error": "MinIO not configured"}'

    try:
        client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )

        # List objects and try to match keywords against object names
        keyword_list = [k.strip().lower() for k in keywords.split() if k.strip()]
        matches = []

        for obj in client.list_objects(settings.minio_bucket, recursive=True):
            obj_name = obj.object_name.lower()
            # Simple keyword match against the filename
            if any(kw in obj_name for kw in keyword_list):
                url = (
                    f"{settings.minio_endpoint}/{settings.minio_bucket}"
                    f"/{obj.object_name}"
                )
                matches.append({"object_name": obj.object_name, "url": url})

            # Stop when we have enough
            if len(matches) >= 5:
                break

        if not matches:
            return (
                f'{{"error": "No local assets found for \\"{keywords}\\""}}'
            )

        import json

        return json.dumps(
            {"matches": matches, "total": len(matches)},
            ensure_ascii=False,
        )

    except Exception as exc:
        logger.warning("Local asset search failed for %r: %s", keywords, exc)
        return f'{{"error": "Local asset search failed: {exc}"}}'
