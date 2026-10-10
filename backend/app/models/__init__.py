"""SQLAlchemy models. Tables are added iteration by iteration via Alembic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow_column

ROLE_MANAGER = "MANAGER"
ROLE_ADMIN = "ADMIN"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(256))
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    registered_at: Mapped[datetime] = utcnow_column(nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bot_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marketing_opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when personal data was anonymized after a completed privacy request.
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utcnow_column(nullable=False)
    updated_at: Mapped[datetime] = utcnow_column(nullable=False, onupdate=func.now())


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),
        CheckConstraint("role IN ('MANAGER','ADMIN')", name="role_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = utcnow_column(nullable=False)


class ManagerSettings(Base):
    __tablename__ = "manager_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    consultation_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class AdminSettings(Base):
    __tablename__ = "admin_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    system_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class Event(Base):
    """Analytics + audit log. One row per business event."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_type_created", "event_type", "created_at"),
        Index("ix_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = utcnow_column(nullable=False)


class ProcessedUpdate(Base):
    """Telegram update_id dedupe: an update is handled at most once."""

    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    received_at: Mapped[datetime] = utcnow_column(nullable=False)


class BotSession(Base):
    """Persistent conversation state (aiogram FSM). Keyed by Telegram user id
    so that it also works before registration."""

    __tablename__ = "bot_sessions"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    state: Mapped[str | None] = mapped_column(String(128))
    state_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = utcnow_column(nullable=False, onupdate=func.now())


class LegalDocument(Base):
    __tablename__ = "legal_documents"
    __table_args__ = (CheckConstraint("doc_type IN ('TERMS','PRIVACY')", name="doc_type_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)


class LegalDocumentVersion(Base):
    __tablename__ = "legal_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_legal_versions_doc_version"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="status_valid"),
        # At most one ACTIVE version per document.
        Index(
            "uq_legal_versions_one_active",
            "document_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("legal_documents.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Immutable reference to the stored content (file path / hash). Never edited.
    stored_reference: Mapped[str] = mapped_column(Text, nullable=False)
    # Editable presentation URL. Changing it does NOT create a new version.
    public_url: Mapped[str] = mapped_column(Text, nullable=False)
    require_reacceptance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    # Optional immutable stored copy of the document (uploaded in the Dashboard).
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = utcnow_column(nullable=False)


class UserLegalAcceptance(Base):
    __tablename__ = "user_legal_acceptances"
    __table_args__ = (
        UniqueConstraint("user_id", "legal_document_version_id", name="uq_acceptance_user_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    legal_document_version_id: Mapped[int] = mapped_column(
        ForeignKey("legal_document_versions.id", ondelete="RESTRICT"), nullable=False
    )
    accepted_at: Mapped[datetime] = utcnow_column(nullable=False)


class PrivacyRequest(Base):
    __tablename__ = "privacy_requests"
    __table_args__ = (
        CheckConstraint("type IN ('DATA_DELETION')", name="type_valid"),
        CheckConstraint("status IN ('NEW','IN_PROGRESS','COMPLETED')", name="status_valid"),
        # One open request per user.
        Index(
            "uq_privacy_requests_one_open",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('NEW','IN_PROGRESS')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DATA_DELETION")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="NEW")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = utcnow_column(nullable=False)
    updated_at: Mapped[datetime] = utcnow_column(nullable=False, onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchDraft(Base):
    """The user's current, unfinished search parameters (one per user)."""

    __tablename__ = "search_drafts"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = utcnow_column(nullable=False, onupdate=func.now())


class SearchSnapshot(Base):
    """Immutable filter set of an executed search. Result buttons reference it."""

    __tablename__ = "search_snapshots"
    __table_args__ = (Index("ix_search_snapshots_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # SEARCH = from the search screen; MONITORING = "show results" of a saved monitoring.
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SEARCH")
    # Highest result page already sent (protects "Показати ще" from double taps).
    pages_shown: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = utcnow_column(nullable=False)


CONSULTATION_OPEN = ("NEW", "IN_PROGRESS")


class ConsultationRequest(Base):
    __tablename__ = "consultation_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('NEW','IN_PROGRESS','COMPLETED','CLOSED_BY_ADMIN')", name="status_valid"
        ),
        CheckConstraint(
            "result IS NULL OR result IN ('INTERESTED','DECLINED')", name="result_valid"
        ),
        # A client may have only one open (NEW / IN_PROGRESS) request at a time.
        Index(
            "uq_consultation_one_open_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('NEW','IN_PROGRESS')"),
        ),
        Index("ix_consultation_status_created", "status", "created_at"),
        Index("ix_consultation_claimed_by", "claimed_by", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Contact snapshot at request time (the user may change it later).
    contact_name: Mapped[str | None] = mapped_column(String(256))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    contact_username: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="NEW")
    result: Mapped[str | None] = mapped_column(String(16))
    claimed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = utcnow_column(nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = utcnow_column(nullable=False, onupdate=func.now())


class ConsultationNotification(Base):
    """A Telegram message about a request sent to a manager (or an admin as
    fallback). Kept so other managers' messages can be updated after a claim."""

    __tablename__ = "consultation_notifications"
    __table_args__ = (
        CheckConstraint("kind IN ('MANAGER','ADMIN_FALLBACK')", name="kind_valid"),
        CheckConstraint("status IN ('SENT','FAILED')", name="status_valid"),
        Index("ix_consultation_notifications_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="CASCADE"), nullable=False
    )
    recipient_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="MANAGER")
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utcnow_column(nullable=False)


class AdminSession(Base):
    """Dashboard login session. The cookie holds a random token; only its
    SHA-256 hash is stored."""

    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utcnow_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminInvitation(Base):
    __tablename__ = "admin_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','ACCEPTED','DECLINED','CANCELLED')", name="status_valid"
        ),
        Index(
            "uq_admin_invitations_one_pending",
            "invited_user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invited_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    created_at: Mapped[datetime] = utcnow_column(nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "Base",
    "User",
    "UserRole",
    "ManagerSettings",
    "AdminSettings",
    "Event",
    "ProcessedUpdate",
    "BotSession",
    "LegalDocument",
    "LegalDocumentVersion",
    "UserLegalAcceptance",
    "PrivacyRequest",
    "SearchDraft",
    "SearchSnapshot",
    "ConsultationRequest",
    "ConsultationNotification",
    "CONSULTATION_OPEN",
    "AdminSession",
    "AdminInvitation",
    "ROLE_MANAGER",
    "ROLE_ADMIN",
]
