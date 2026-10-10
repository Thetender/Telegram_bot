from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot import texts as t
from app.models import ROLE_MANAGER
from app.services.legal import PRIVACY, TERMS, ActiveLegalVersion


class HelpCb(CallbackData, prefix="h"):
    section: str  # menu|faq|about|tariffs|consult|settings|delete


class MarketingCb(CallbackData, prefix="mk"):
    enable: bool


class PrivacyCb(CallbackData, prefix="pd"):
    action: str  # send


class LegalCb(CallbackData, prefix="lg"):
    action: str  # accept


def main_menu(roles: set[str]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t.BTN_SEARCH)],
        [KeyboardButton(text=t.BTN_MONITORINGS), KeyboardButton(text=t.BTN_CONSULTATION)],
        [KeyboardButton(text=t.BTN_HELP)],
    ]
    if ROLE_MANAGER in roles:
        rows[-1].append(KeyboardButton(text=t.BTN_MY_REQUESTS))
    # one_time_keyboard: the menu folds away after a tap and frees the screen;
    # the user reopens it with the keyboard icon next to the input field.
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=False, one_time_keyboard=True
    )


def share_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t.BTN_SHARE_PHONE, request_contact=True)]],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
    )


def legal_links(versions: dict[str, ActiveLegalVersion]) -> InlineKeyboardMarkup | None:
    buttons = []
    if TERMS in versions:
        buttons.append([InlineKeyboardButton(text=t.BTN_TERMS, url=versions[TERMS].public_url)])
    if PRIVACY in versions:
        buttons.append([InlineKeyboardButton(text=t.BTN_PRIVACY, url=versions[PRIVACY].public_url)])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


def help_menu(versions: dict[str, ActiveLegalVersion]) -> InlineKeyboardMarkup:
    def cb(text: str, section: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=HelpCb(section=section).pack())

    # Compact: short labels two per row, long ones on their own row.
    rows = [
        [cb(t.BTN_FAQ, "faq"), cb(t.BTN_TARIFFS, "tariffs")],
        [cb(t.BTN_ABOUT, "about")],
    ]
    legal_row = []
    if TERMS in versions:
        legal_row.append(
            InlineKeyboardButton(text=t.BTN_TERMS_SHORT, url=versions[TERMS].public_url)
        )
    if PRIVACY in versions:
        legal_row.append(
            InlineKeyboardButton(text=t.BTN_PRIVACY_SHORT, url=versions[PRIVACY].public_url)
        )
    if legal_row:
        rows.append(legal_row)
    rows.append([cb(t.BTN_NOTIFICATION_SETTINGS, "settings")])
    rows.append([cb(t.BTN_DELETE_DATA, "delete")])
    rows.append([cb(t.BTN_MAIN_MENU, "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_help() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t.BTN_BACK, callback_data=HelpCb(section="menu").pack())]
        ]
    )


def marketing(enabled: bool) -> InlineKeyboardMarkup:
    toggle = InlineKeyboardButton(
        text=t.BTN_MARKETING_OFF if enabled else t.BTN_MARKETING_ON,
        callback_data=MarketingCb(enable=not enabled).pack(),
    )
    back = InlineKeyboardButton(text=t.BTN_BACK, callback_data=HelpCb(section="menu").pack())
    return InlineKeyboardMarkup(inline_keyboard=[[toggle], [back]])


def delete_data() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t.BTN_SEND_DELETE_REQUEST, callback_data=PrivacyCb(action="send").pack()
                )
            ],
            [InlineKeyboardButton(text=t.BTN_BACK, callback_data=HelpCb(section="menu").pack())],
        ]
    )


def legal_gate(pending: list[ActiveLegalVersion]) -> InlineKeyboardMarkup:
    rows = []
    for v in pending:
        text = t.BTN_TERMS if v.doc_type == TERMS else t.BTN_PRIVACY
        rows.append([InlineKeyboardButton(text=text, url=v.public_url)])
    rows.append(
        [
            InlineKeyboardButton(
                text=t.BTN_LEGAL_ACCEPT, callback_data=LegalCb(action="accept").pack()
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t.BTN_DELETE_DATA, callback_data=HelpCb(section="delete").pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
