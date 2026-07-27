"""Knowledge base CRUD — document upload, vectorization, and semantic search."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_pg_db
from app.deps import CurrentPrincipal, require_auth

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class KnowledgeBaseCreate(BaseModel):
    name: str
    kb_type: str = "article"
    description: Optional[str] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    name: str
    slug: Optional[str] = None
    kb_type: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class KnowledgeBaseListResponse(BaseModel):
    total: int
    items: List[KnowledgeBaseResponse]


class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    status: str
    chunk_count: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    total: int
    items: List[DocumentResponse]


class SearchResult(BaseModel):
    id: int
    content: str
    chunk_index: int = 0
    kb_type: Optional[str] = None
    score: float = 0.0
    knowledge_base_id: Optional[int] = None


class SearchResponse(BaseModel):
    results: List[SearchResult]


# --- Routes ---

@router.get("/knowledge-bases", response_model=KnowledgeBaseListResponse)
def list_knowledge_bases(
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List all knowledge bases for the current tenant."""
    from app.services.knowledge_base_service import list_knowledge_bases as svc_list
    items = svc_list(db, principal.tenant_id)
    return KnowledgeBaseListResponse(total=len(items), items=items)


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse,
             status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    req: KnowledgeBaseCreate,
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new knowledge base."""
    from app.services.knowledge_base_service import create_knowledge_base as svc_create
    kb = svc_create(db, tenant_id=principal.tenant_id, name=req.name,
                    kb_type=req.kb_type, description=req.description)
    return kb


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get a single knowledge base by id."""
    from app.services.knowledge_base_service import get_knowledge_base as svc_get
    kb = svc_get(db, kb_id, tenant_id=principal.tenant_id)
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Knowledge base not found")
    return kb


@router.delete("/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete (soft) a knowledge base."""
    from app.services.knowledge_base_service import delete_knowledge_base as svc_delete
    if not svc_delete(db, kb_id, tenant_id=principal.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Knowledge base not found")


# --- Document endpoints ---

@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentResponse,
             status_code=status.HTTP_201_CREATED)
async def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Upload a document to a knowledge base.

    Supports PDF, DOCX, MD, TXT files. The document is automatically parsed,
    chunked, embedded, and indexed for vector search.
    """
    from app.services.knowledge_base_service import process_document

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Empty file")

    doc = process_document(db, kb_id, principal.tenant_id, file_bytes,
                           file.filename or "untitled")
    return doc


@router.get("/knowledge-bases/{kb_id}/documents", response_model=DocumentListResponse)
def list_documents(
    kb_id: int,
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List documents in a knowledge base."""
    from app.services.knowledge_base_service import list_documents as svc_list_docs
    items = svc_list_docs(db, kb_id, tenant_id=principal.tenant_id)
    return DocumentListResponse(total=len(items), items=items)


@router.get("/knowledge-bases/{kb_id}/documents/{doc_id}", response_model=DocumentResponse)
def get_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get a single document by id."""
    from app.services.knowledge_base_service import get_document as svc_get_doc
    doc = svc_get_doc(db, doc_id, tenant_id=principal.tenant_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Document not found")
    return doc


@router.delete("/knowledge-bases/{kb_id}/documents/{doc_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a document and its chunks from a knowledge base."""
    from app.services.knowledge_base_service import delete_document as svc_delete_doc
    if not svc_delete_doc(db, doc_id, tenant_id=principal.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Document not found")


# --- Search endpoints ---

@router.get("/knowledge-bases/{kb_id}/search", response_model=SearchResponse)
def search_knowledge_base(
    kb_id: int,
    q: str = Query(..., description="Search query"),
    top_k: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Vector similarity search within a knowledge base.

    Uses pgvector cosine distance on DashScope text-embedding-v2 vectors.
    """
    from app.services.knowledge_base_service import search_knowledge_base as svc_search
    results = svc_search(db, kb_id, q, top_k, tenant_id=principal.tenant_id)
    return SearchResponse(results=[SearchResult(**r) for r in results])


@router.get("/knowledge-bases/search/all", response_model=SearchResponse)
def search_all_knowledge_bases(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_pg_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Search across all active knowledge bases for the current tenant."""
    from app.services.knowledge_base_service import search_all_knowledge_bases as svc_search_all
    results = svc_search_all(db, principal.tenant_id, q, top_k)
    return SearchResponse(results=[SearchResult(**r) for r in results])
