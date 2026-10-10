"""Marketing campaigns (FR §13, UI/UX §11, Architecture §14).

Audience → message/button → preview → counts → explicit confirmation. At
confirmation the recipient list is materialized into campaign_recipients
(an immutable snapshot); the delivery worker sends it after auction
notifications (they have priority). Users who opted out of marketing,
blocked the bot, were blocked by an admin or deleted their data are never
recipients — also not when selected by hand.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Campaign, CampaignRecipient, Event, User
from app.services import events
from app.services.links import allowed_hosts, is_allowed_destination, tracked_url

MAX_TEXT = 3500  # Telegram limit is 4096; keep room for formatting
MAX_SELECTED = 5000
UNSUBSCRIBE_PREFIX = "mu"  # callback data "mu:<campaign id>"

# Telegram Bot API HTML subset.
_ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "a", "code", "pre", "blockquote", "tg-spoiler", "span",
}  # fmt: skip


class _Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.visible = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            self.errors.append(f"Тег <{tag}> не підтримується Telegram")
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.startswith(("https://", "http://", "tg://")):
                self.errors.append("Посилання <a> має починатися з https://")
        if tag == "span" and dict(attrs).get("class") != "tg-spoiler":
            self.errors.append('Тег <span> дозволено лише як <span class="tg-spoiler">')
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"Тег </{tag}> закрито не в тому місці")
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.visible += len(data)


def validate(campaign: Campaign) -> list[str]:
    """Errors that must be fixed before the campaign can be sent."""
    errors: list[str] = []
    text = (campaign.message or "").strip()
    if not text:
        errors.append("Додайте текст повідомлення.")
    else:
        checker = _Checker()
        checker.feed(text)
        checker.close()
        errors += checker.errors
        if checker.stack:
            errors.append("Не всі теги закрито: " + ", ".join(f"<{t}>" for t in checker.stack))
        if checker.visible > MAX_TEXT:
            errors.append(f"Текст задовгий: {checker.visible} символів (максимум {MAX_TEXT}).")
    if bool(campaign.button_text) != bool(campaign.button_url):
        errors.append(
            "Для кнопки вкажіть і текст, і посилання (або залиште обидва поля порожніми)."
        )
    if campaign.button_url and not campaign.button_url.startswith("https://"):
        errors.append("Посилання кнопки має починатися з https://")
    if campaign.audience_type == "SELECTED" and not campaign.selected_user_ids:
        errors.append("Оберіть хоча б одного користувача.")
    return errors


# ---------- audience ----------


@dataclass
class Audience:
    total: int  # selected/registered population (active users)
    opted_out: int  # excluded: marketing messages switched off
    unreachable: int  # excluded: blocked the bot / chat not found
    recipient_ids: list[int]

    @property
    def final(self) -> int:
        return len(self.recipient_ids)


async def audience(session: AsyncSession, campaign: Campaign) -> Audience:
    stmt = select(User.id, User.marketing_opt_out_at, User.bot_blocked_at).where(
        User.anonymized_at.is_(None), User.access_blocked_at.is_(None)
    )
    if campaign.audience_type == "SELECTED":
        stmt = stmt.where(User.id.in_(campaign.selected_user_ids or [-1]))
    total = opted_out = unreachable = 0
    recipients: list[int] = []
    for uid, opt_out_at, blocked_at in await session.execute(stmt.order_by(User.id)):
        total += 1
        if opt_out_at is not None:
            opted_out += 1
        elif blocked_at is not None:
            unreachable += 1
        else:
            recipients.append(uid)
    return Audience(total, opted_out, unreachable, recipients)


async def confirm(session: AsyncSession, campaign: Campaign, admin: User) -> Audience | None:
    """DRAFT → SENDING with a materialized recipient snapshot. None if not allowed."""
    if campaign.status != "DRAFT" or validate(campaign):
        return None
    aud = await audience(session, campaign)
    if not aud.recipient_ids:
        return None
    await session.execute(
        insert(CampaignRecipient)
        .values([{"campaign_id": campaign.id, "user_id": uid} for uid in aud.recipient_ids])
        .on_conflict_do_nothing(constraint="uq_campaign_recipient")
    )
    campaign.status = "SENDING"
    campaign.confirmed_by = admin.id
    campaign.queued_at = datetime.now(UTC)
    campaign.audience_total = aud.total
    campaign.opted_out_count = aud.opted_out
    campaign.unreachable_count = aud.unreachable
    campaign.recipients_count = aud.final
    return aud


# ---------- message ----------


def click_tag(campaign_id: int) -> str:
    """Stored in the tracked link (and the AUCTION_OPENED event) to count clicks."""
    return f"campaign:{campaign_id}"


def render(
    campaign: Campaign, user_id: int | None, settings: Settings, preview: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    if campaign.button_text and campaign.button_url:
        url = campaign.button_url
        hosts = allowed_hosts(settings.thetender_base_url)
        if user_id is not None and is_allowed_destination(url, hosts):
            url = tracked_url(
                settings.public_base_url,
                settings.link_signing_key,
                user_id,
                url,
                "CAMPAIGN",
                click_tag(campaign.id),
            )
        rows.append([InlineKeyboardButton(text=campaign.button_text, url=url)])
    rows.append(
        [
            InlineKeyboardButton(
                text="🔕 Відписатися від розсилок",
                callback_data=f"{UNSUBSCRIBE_PREFIX}:{campaign.id}",
            )
        ]
    )
    text = campaign.message.strip()
    if preview:
        text = f"🧪 <i>Тестове повідомлення розсилки</i>\n\n{text}"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def link_is_tracked(campaign: Campaign, settings: Settings) -> bool:
    return bool(
        campaign.button_url
        and settings.public_base_url
        and settings.link_signing_key
        and is_allowed_destination(campaign.button_url, allowed_hosts(settings.thetender_base_url))
    )


# ---------- progress ----------


@dataclass
class Progress:
    pending: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    clicked: int = 0

    @property
    def processed(self) -> int:
        return self.sent + self.failed + self.skipped


async def progress(session: AsyncSession, campaign_ids: list[int]) -> dict[int, Progress]:
    out = {cid: Progress() for cid in campaign_ids}
    if not campaign_ids:
        return out
    rows = await session.execute(
        select(CampaignRecipient.campaign_id, CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id.in_(campaign_ids))
        .group_by(CampaignRecipient.campaign_id, CampaignRecipient.status)
    )
    for cid, status, count in rows:
        p = out[cid]
        if status in ("PENDING", "SENDING"):
            p.pending += count
        elif status == "SENT":
            p.sent += count
        elif status == "FAILED":
            p.failed += count
        else:
            p.skipped += count
    tags = {click_tag(cid): cid for cid in campaign_ids}
    tag = Event.event_data["auction_number"].astext
    clicks = await session.execute(
        select(tag, func.count(func.distinct(Event.user_id)))
        .where(Event.event_type == events.AUCTION_OPENED, tag.in_(list(tags)))
        .group_by(tag)
    )
    for value, count in clicks:
        out[tags[value]].clicked = count
    return out


async def finish_if_done(session: AsyncSession, campaign_id: int) -> bool:
    campaign = await session.get(Campaign, campaign_id, with_for_update=True)
    if campaign is None or campaign.status != "SENDING":
        return False
    left = await session.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.status.in_(("PENDING", "SENDING")),
        )
    )
    if left:
        return False
    campaign.status = "DONE"
    campaign.finished_at = datetime.now(UTC)
    p = (await progress(session, [campaign_id]))[campaign_id]
    await events.log_event(
        session,
        events.CAMPAIGN_SENT,
        actor_user_id=campaign.confirmed_by,
        data={
            "campaign_id": campaign_id,
            "sent": p.sent,
            "failed": p.failed,
            "skipped": p.skipped,
        },
    )
    return True


async def search_users(session: AsyncSession, q: str, limit: int = 30) -> list[User]:
    q = q.strip()
    if not q:
        return []
    like = f"%{q}%"
    digits = "".join(ch for ch in q if ch.isdigit())
    conditions = [User.name.ilike(like), User.username.ilike(like.lstrip("@"))]
    if digits:
        conditions.append(User.phone.like(f"%{digits}%"))
        conditions.append(func.cast(User.telegram_id, type_=User.phone.type).like(f"%{digits}%"))
    return list(
        await session.scalars(
            select(User)
            .where(User.anonymized_at.is_(None), or_(*conditions))
            .order_by(User.name, User.id)
            .limit(limit)
        )
    )


class _Sanitizer(HTMLParser):
    """Re-emits only Telegram tags with only safe attributes (Dashboard preview)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith(("https://", "http://")):
                self.out.append(
                    f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">'
                )
            else:
                self.out.append("<a>")
        elif tag in ("span", "tg-spoiler"):
            self.out.append('<span class="spoiler">')
        else:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _ALLOWED_TAGS:
            self.out.append("</span>" if tag in ("span", "tg-spoiler") else f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data).replace("\n", "<br>"))


def preview_html(text: str) -> str:
    """Safe HTML approximation of how Telegram will show the message."""
    sanitizer = _Sanitizer()
    sanitizer.feed(text or "")
    sanitizer.close()
    return "".join(sanitizer.out)
