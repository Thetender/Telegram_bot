"""«Відгуки та пропозиції»: feedback threads from the bot and replies from the Dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, FeedbackMessage, Monitoring, User
from app.services import events, search

TOPICS = {
    "bug": "🐞 Повідомити про помилку",
    "idea": "💡 Запропонувати покращення",
    "search": "🔎 Проблема з пошуком",
    "other": "✍️ Інше повідомлення",
}
STATUSES = {"NEW": "Нове", "IN_PROGRESS": "У роботі", "RESOLVED": "Вирішено"}
MAX_TEXT = 3500
PAGE_SIZE = 30


async def user_context(session: AsyncSession, user: User) -> dict:
    """What the person was doing: their current search filters and monitorings."""
    draft = await search.get_draft(session, user.id)
    monitorings = await session.scalar(
        select(func.count())
        .select_from(Monitoring)
        .where(Monitoring.user_id == user.id, Monitoring.deleted_at.is_(None))
    )
    return {"search": draft or {}, "monitorings": monitorings or 0}


async def create(
    session: AsyncSession,
    user: User,
    topic: str,
    text: str,
    photo_file_id: str | None = None,
) -> Feedback:
    fb = Feedback(
        user_id=user.id,
        topic=topic if topic in TOPICS else "other",
        context=await user_context(session, user),
    )
    session.add(fb)
    await session.flush()
    await add_message(session, fb, "IN", text, photo_file_id, user.id)
    await events.log_event(
        session,
        events.FEEDBACK_CREATED,
        user_id=user.id,
        data={"feedback_id": fb.id, "topic": fb.topic},
    )
    return fb


async def add_message(
    session: AsyncSession,
    fb: Feedback,
    direction: str,
    text: str,
    photo_file_id: str | None = None,
    author_id: int | None = None,
) -> FeedbackMessage:
    msg = FeedbackMessage(
        feedback_id=fb.id,
        direction=direction,
        text=(text or "")[:MAX_TEXT],
        photo_file_id=photo_file_id,
        author_id=author_id,
    )
    session.add(msg)
    fb.updated_at = datetime.now(UTC)
    if direction == "IN" and fb.status == "RESOLVED":
        fb.status = "IN_PROGRESS"  # the person wrote again
    await session.flush()
    return msg


async def messages(session: AsyncSession, feedback_id: int) -> list[FeedbackMessage]:
    return list(
        await session.scalars(
            select(FeedbackMessage)
            .where(FeedbackMessage.feedback_id == feedback_id)
            .order_by(FeedbackMessage.created_at, FeedbackMessage.id)
        )
    )


async def list_feedback(
    session: AsyncSession, status: str = "", topic: str = "", page: int = 1
) -> tuple[list[tuple[Feedback, User, FeedbackMessage | None]], int]:
    stmt = select(Feedback, User).join(User, User.id == Feedback.user_id)
    if status in STATUSES:
        stmt = stmt.where(Feedback.status == status)
    if topic in TOPICS:
        stmt = stmt.where(Feedback.topic == topic)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.execute(
            stmt.order_by(Feedback.created_at.desc())
            .limit(PAGE_SIZE)
            .offset((max(page, 1) - 1) * PAGE_SIZE)
        )
    ).all()
    first: dict[int, FeedbackMessage] = {}
    ids = [f.id for f, _ in rows]
    if ids:
        for m in await session.scalars(
            select(FeedbackMessage)
            .where(FeedbackMessage.feedback_id.in_(ids), FeedbackMessage.direction == "IN")
            .order_by(FeedbackMessage.id)
        ):
            first.setdefault(m.feedback_id, m)
    return [(f, u, first.get(f.id)) for f, u in rows], total


async def count_new(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(Feedback).where(Feedback.status == "NEW")
        )
        or 0
    )


async def set_status(session: AsyncSession, fb: Feedback, status: str, admin: User) -> None:
    if status in STATUSES:
        fb.status = status
        fb.handled_by = admin.id
