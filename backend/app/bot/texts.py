"""All user-facing bot texts in one place.

Help-section texts marked TODO(content) are placeholders until the product
owner provides final copy.
"""

# --- Main menu buttons (persistent reply keyboard) ---
BTN_SEARCH = "🔎 Пошук аукціонів"
BTN_MONITORINGS = "🔔 Мої моніторинги"
BTN_CONSULTATION = "💬 Консультація"
BTN_HELP = "ℹ️ Допомога"
BTN_MY_REQUESTS = "📋 Мої заявки"
MENU_BUTTONS = (BTN_SEARCH, BTN_MONITORINGS, BTN_CONSULTATION, BTN_HELP, BTN_MY_REQUESTS)

BTN_SHARE_PHONE = "📱 Поділитися телефоном"
BTN_TERMS = "📄 Умови використання"
BTN_PRIVACY = "🔒 Політика конфіденційності"
BTN_BACK = "◀️ Назад"

# --- Registration ---
WELCOME = (
    "👋 Вітаємо в The Tender!\n\n"
    "Для користування сервісом необхідно підтвердити номер телефону.\n\n"
    "Продовжуючи користуватися сервісом, ви підтверджуєте ознайомлення з актуальними "
    "Умовами використання та Політикою конфіденційності."
)
SHARE_PHONE_PROMPT = "Натисніть кнопку «📱 Поділитися телефоном» нижче 👇"
REGISTRATION_REQUIRED = (
    "Щоб користуватися ботом, спершу підтвердьте номер телефону кнопкою "
    "«📱 Поділитися телефоном» 👇"
)
FOREIGN_CONTACT = (
    "⚠️ Можна поділитися лише власним номером телефону.\n\n"
    "Будь ласка, натисніть кнопку «📱 Поділитися телефоном» нижче 👇"
)
INVALID_PHONE = "⚠️ Не вдалося розпізнати номер телефону. Спробуйте ще раз кнопкою нижче 👇"
REGISTRATION_DONE = "Реєстрацію завершено ✅"

# --- Main menu ---
MAIN_MENU = "Головне меню. Оберіть розділ 👇"
UNKNOWN_INPUT = "Не зовсім зрозумів 🙂 Скористайтеся кнопками меню нижче 👇"


def coming_soon(section: str) -> str:
    return f"🚧 Розділ «{section}» з'явиться в одній з наступних версій бота."


STALE_ACTION = "Ця кнопка вже неактуальна. Скористайтеся меню нижче 👇"

# --- Help ---
HELP_TITLE = "ℹ️ Допомога\n\nОберіть розділ:"
BTN_FAQ = "❓ FAQ"
BTN_ABOUT = "ℹ️ Про Prozorro.Продажі"
BTN_TARIFFS = "💳 Тарифи"
BTN_HELP_CONSULTATION = "💬 Консультація"
BTN_NOTIFICATION_SETTINGS = "⚙️ Налаштування повідомлень"
BTN_DELETE_DATA = "🗑 Видалити мої персональні дані"

# TODO(content): replace with final copy from the product owner.
HELP_FAQ = (
    "❓ <b>Часті запитання</b>\n\n"
    "<i>Текст розділу буде додано найближчим часом.</i>"
)
HELP_ABOUT = (
    "ℹ️ <b>Про Prozorro.Продажі</b>\n\n"
    "<i>Текст розділу буде додано найближчим часом.</i>"
)
HELP_TARIFFS = (
    "💳 <b>Тарифи</b>\n\n"
    "<i>Текст розділу буде додано найближчим часом.</i>"
)
LEGAL_NOT_CONFIGURED = "Документ тимчасово недоступний. Спробуйте пізніше."

# --- Marketing preference ---
MARKETING_TITLE = "⚙️ <b>Налаштування повідомлень</b>"


def marketing_state(enabled: bool) -> str:
    state = "Увімкнено" if enabled else "Вимкнено"
    return (
        f"{MARKETING_TITLE}\n\n"
        f"Маркетингові повідомлення: <b>{state}</b>\n\n"
        "Це налаштування стосується лише інформаційних розсилок. Сповіщення про нові "
        "аукціони за вашими моніторингами та повідомлення щодо консультацій "
        "надходитимуть завжди."
    )


BTN_MARKETING_OFF = "Вимкнути маркетингові повідомлення"
BTN_MARKETING_ON = "Увімкнути маркетингові повідомлення"
SETTINGS_SAVED = "Налаштування збережено ✅"

# --- Personal data deletion ---
DELETE_DATA_EXPLANATION = (
    "🗑 <b>Видалення персональних даних</b>\n\n"
    "Ви можете надіслати запит на видалення ваших персональних даних із сервісу "
    "The Tender у Telegram. Після обробки запиту адміністратором буде видалено ваші "
    "моніторинги та персональні дані (номер телефону, ім'я, username).\n\n"
    "Після видалення бот більше не надсилатиме вам повідомлень."
)
BTN_SEND_DELETE_REQUEST = "Надіслати запит на видалення"
DELETE_REQUEST_CREATED = (
    "Запит на видалення персональних даних прийнято ✅\n\n"
    "Адміністратор опрацює його найближчим часом."
)
DELETE_REQUEST_EXISTS = "Ваш запит на видалення персональних даних уже прийнято й опрацьовується ✅"
