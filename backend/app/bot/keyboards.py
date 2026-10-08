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


def main_menu(roles: set[str]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t.BTN_SEARCH)],
        [KeyboardButton(text=t.BTN_MONITORINGS), KeyboardButton(text=t.BTN_CONSULTATION)],
        [KeyboardButton(text=t.BTN_HELP)],
    ]
    if ROLE_MANAGER in roles:
        rows[-1].append(KeyboardButton(text=t.BTN_MY_REQUESTS))
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


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
        buttons.append(
            [InlineKeyboardButton(text=t.BTN_PRIVACY, url=versions[PRIVACY].public_url)]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


def help_menu(versions: dict[str, ActiveLegalVersion]) -> InlineKeyboardMarkup:
    def cb(text: str, section: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=HelpCb(section=section).pack())

    rows = [
        [cb(t.BTN_FAQ, "faq")],
        [cb(t.BTN_ABOUT, "about")],
        [cb(t.BTN_TARIFFS, "tariffs")],
        [cb(t.BTN_HELP_CONSULTATION, "consult")],
    ]
    if TERMS in versions:
        rows.append([InlineKeyboardButton(text=t.BTN_TERMS, url=versions[TERMS].public_url)])
    if PRIVACY in versions:
        rows.append([InlineKeyboardButton(text=t.BTN_PRIVACY, url=versions[PRIVACY].public_url)])
    rows.append([cb(t.BTN_NOTIFICATION_SETTINGS, "settings")])
    rows.append([cb(t.BTN_DELETE_DATA, "delete")])
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
