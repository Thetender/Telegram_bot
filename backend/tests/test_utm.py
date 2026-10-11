"""UTM tags on links to thetender.com.ua and the deep-link acquisition source."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from app.models import User
from app.services import links
from app.services.admin import overview
from tests.fake_telegram import contact_update, message_update

UID = 7007


def qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def test_with_utm_tags_only_the_marketplace_site():
    url = links.with_utm(
        "https://thetender.com.ua/auction/LAE001?x=1&utm_source=old#top",
        links.LOT_ALERT,
        "LAE001-UA-20261008-38094",
    )
    assert url == (
        "https://thetender.com.ua/auction/LAE001?x=1&utm_source=telegram&utm_medium=bot"
        "&utm_campaign=lot_alert&utm_content=LAE001-UA-20261008-38094#top"
    )
    assert links.with_utm("https://www.thetender.com.ua", links.MENU, "Register").endswith(
        "/?utm_source=telegram&utm_medium=bot&utm_campaign=menu&utm_content=register"
    )
    for other in (
        "https://prozorro.sale/a",
        "https://diia.gov.ua",
        "https://tg.thetender.com.ua/r/x",
    ):
        assert links.with_utm(other, links.MENU) == other


def test_links_inside_message_text_are_tagged():
    text = 'Сайт: https://thetender.com.ua. <a href="https://thetender.com.ua/t?a=1&amp;b=2">x</a> https://prozorro.sale'
    out = links.tag_text_links(text, links.SUPPORT, "faq_3")
    assert (
        "https://thetender.com.ua/?utm_source=telegram&amp;utm_medium=bot"
        "&amp;utm_campaign=support&amp;utm_content=faq_3. "
        in out
    )
    assert 'href="https://thetender.com.ua/t?a=1&amp;b=2&amp;utm_source=telegram' in out
    assert out.endswith("https://prozorro.sale")


def test_acquisition_source_validation():
    assert links.acquisition_source("site_header") == "site_header"
    assert links.acquisition_source("GROUP_Agro_UA") == "group_agro_ua"
    for bad in (None, "", "380501112233", "group_", "site_other", "x" * 70):
        assert links.acquisition_source(bad) is None


async def test_deep_link_source_saved_on_registration_and_shown_in_stats(harness):
    h = harness
    await h.feed(message_update(UID, "/start site_lot"))
    await h.feed(contact_update(UID, contact_user_id=UID))
    # A later /start with another source does not overwrite the first touch.
    await h.feed(message_update(UID, "/start ads"))
    await h.feed(message_update(UID + 1, "/start"))
    await h.feed(contact_update(UID + 1, contact_user_id=UID + 1, phone="380501112299"))
    async with h.sf() as s:
        user = await s.scalar(select(User).where(User.telegram_id == UID))
        assert user.acquisition_source == "site_lot"
        o = await overview(s, 30, "Europe/Kyiv")
    assert ("site_lot", 1, 1) in o.sources and (None, 1, 1) in o.sources
