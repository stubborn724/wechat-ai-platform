"""PostgreSQL/pgvector ORM models for knowledge base and document storage."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import mapped_column

from app.database import PgBase


class KnowledgeBase(PgBase):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False, index=True)
    name = Column(Text, nullable=False)
    slug = Column(Text, unique=True, nullable=False, index=True)
    kb_type = Column(Text, nullable=False, comment="knowledge base type, e.g. article, faq")
    description = Column(Text, nullable=True)
    is_active = Column(Integer, nullable=False, server_default="1", comment="1=active, 0=inactive")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KbDocument(PgBase):
    __tablename__ = "kb_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id = Column(
        Integer,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(Text, nullable=False)
    file_type = Column(Text, nullable=False, comment="e.g. pdf, docx, md")
    file_size = Column(Integer, nullable=True, comment="file size in bytes")
    status = Column(
        Text, nullable=False, server_default="pending", comment="pending, processing, ready, error"
    )
    content_hash = Column(Text, nullable=True, comment="SHA256 of raw content for dedup")
    error_message = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    chunk_count = Column(Integer, nullable=True)
    storage_key = Column(Text, nullable=True, comment="object storage key (MinIO/S3)")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KbDocumentChunk(PgBase):
    __tablename__ = "kb_document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False, index=True)
    knowledge_base_id = Column(Integer, nullable=False, index=True)
    document_id = Column(
        Integer,
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    kb_type = Column(Text, nullable=False, comment="denormalized from KnowledgeBase for filtering")
    metadata_ = mapped_column("metadata", Text, nullable=True, comment="JSON-encoded metadata")
    embedding = mapped_column(Vector(1536), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


Index(
    "ix_kb_document_chunk_embedding",
    KbDocumentChunk.embedding,
    postgresql_using="ivfflat",
)
