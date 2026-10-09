from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LegalDocument, LegalDocumentVersion, UserLegalAcceptance
from app.services import events

TERMS = "TERMS"
PRIVACY = "PRIVACY"
DOC_TITLES = {TERMS: "Умови використання", PRIVACY: "Політика конфіденційності"}


@dataclass(frozen=True)
class ActiveLegalVersion:
    id: int
    doc_type: str
    version: str
    public_url: str
    require_reacceptance: bool


async def get_active_versions(session: AsyncSession) -> dict[str, ActiveLegalVersion]:
    rows = await session.execute(
        select(LegalDocument.doc_type, LegalDocumentVersion)
        .join(LegalDocumentVersion, LegalDocumentVersion.document_id == LegalDocument.id)
        .where(LegalDocumentVersion.status == "ACTIVE")
    )
    result: dict[str, ActiveLegalVersion] = {}
    for doc_type, v in rows:
        result[doc_type] = ActiveLegalVersion(
            id=v.id,
            doc_type=doc_type,
            version=v.version,
            public_url=v.public_url,
            require_reacceptance=v.require_reacceptance,
        )
    return result


async def record_acceptance(session: AsyncSession, user_id: int, version_ids: list[int]) -> None:
    for vid in version_ids:
        await session.execute(
            insert(UserLegalAcceptance)
            .values(user_id=user_id, legal_document_version_id=vid)
            .on_conflict_do_nothing(constraint="uq_acceptance_user_version")
        )


async def publish_version(
    session: AsyncSession,
    doc_type: str,
    version: str,
    public_url: str,
    stored_reference: str,
    effective_at: datetime,
    require_reacceptance: bool = False,
    file_name: str | None = None,
    file_content: bytes | None = None,
    created_by: int | None = None,
) -> LegalDocumentVersion:
    """Create a new immutable ACTIVE version and archive the previous one."""
    doc = await session.scalar(select(LegalDocument).where(LegalDocument.doc_type == doc_type))
    if doc is None:
        doc = LegalDocument(doc_type=doc_type, title=DOC_TITLES[doc_type])
        session.add(doc)
        await session.flush()
    existing = await session.scalar(
        select(LegalDocumentVersion).where(
            LegalDocumentVersion.document_id == doc.id, LegalDocumentVersion.version == version
        )
    )
    if existing is not None:
        raise ValueError(f"{doc_type} version {version} already exists (versions are immutable)")
    await session.execute(
        update(LegalDocumentVersion)
        .where(
            LegalDocumentVersion.document_id == doc.id, LegalDocumentVersion.status == "ACTIVE"
        )
        .values(status="ARCHIVED")
    )
    v = LegalDocumentVersion(
        document_id=doc.id,
        version=version,
        public_url=public_url,
        stored_reference=stored_reference,
        effective_at=effective_at,
        require_reacceptance=require_reacceptance,
        status="ACTIVE",
        file_name=file_name,
        file_content=file_content,
        file_sha256=hashlib.sha256(file_content).hexdigest() if file_content else None,
        created_by=created_by,
    )
    session.add(v)
    await session.flush()
    await events.log_event(
        session,
        events.LEGAL_VERSION_PUBLISHED,
        actor_user_id=created_by,
        data={"doc_type": doc_type, "version": version, "reaccept": require_reacceptance},
    )
    return v
