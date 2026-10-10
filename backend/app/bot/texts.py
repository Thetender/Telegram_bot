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

# --- Search (Iteration 2) ---
SEARCH_TITLE = "🔎 <b>Пошук аукціонів</b>"
SEARCH_HINT = (
    "Оберіть параметр пошуку. Після вибору ви побачите всі обрані параметри "
    "й зможете запустити пошук."
)
CHOOSE_PARAM = "Оберіть параметр, який хочете додати або змінити (✅ — вже обрані):"
SEARCH_NEED_PARAM = "Оберіть хоча б один параметр пошуку."

BTN_P_DEAL = "Продаж / Оренда"
BTN_P_TYPE = "Тип аукціону"
BTN_P_CATEGORY = "Категорія"
BTN_P_REGION = "Регіон"
BTN_P_CITY = "Місто"
BTN_P_KEYWORDS = "Ключові слова"
BTN_P_ORGANIZER = "Організатор"
BTN_P_PRICE = "Ціна"
BTN_P_AREA = "Площа"
BTN_RUN_SEARCH = "🔍 Шукати"
BTN_EDIT_PARAMS = "✏️ Додати / змінити параметри"
BTN_CLEAR_PARAMS = "🗑 Очистити параметри"
BTN_MAIN_MENU = "◀️ Головне меню"
BTN_DONE = "✅ Готово"
BTN_CANCEL = "✖️ Скасувати"
BTN_SKIP = "⏭ Пропустити"
BTN_CLEAR_FIELD = "🗑 Очистити"
BTN_YES_CLEAR = "Так, очистити"
BTN_NO = "Ні"

CHOOSE_DEAL = "Оберіть: продаж чи оренда?"
CHOOSE_TYPE = "Оберіть тип аукціону:"
CHOOSE_CATEGORY = "Оберіть категорію:"
CHOOSE_REGIONS = "Оберіть один або кілька регіонів і натисніть «✅ Готово»."
CHOOSE_AREA_UNIT = "Оберіть одиницю виміру площі:"
CONFIRM_CLEAR = "Очистити всі параметри пошуку?"

PROMPT_CITY = "Введіть назву населеного пункту, наприклад: <i>Біла Церква</i>"
PROMPT_KEYWORDS = (
    "Введіть ключові слова — слово чи фразу з опису аукціону. "
    "Можна також ввести номер аукціону."
)
PROMPT_ORGANIZER = "Введіть назву організатора або код ЄДРПОУ."
PROMPT_MIN_PRICE = "Введіть <b>мінімальну</b> стартову ціну, грн, або натисніть «Пропустити»."
PROMPT_MAX_PRICE = "Введіть <b>максимальну</b> стартову ціну, грн, або натисніть «Пропустити»."
PROMPT_MIN_AREA = "Введіть <b>мінімальну</b> площу ({unit}) або натисніть «Пропустити»."
PROMPT_MAX_AREA = "Введіть <b>максимальну</b> площу ({unit}) або натисніть «Пропустити»."
BAD_NUMBER = "⚠️ Введіть число, наприклад <i>150000</i> або <i>2,5</i>."
BAD_RANGE = "⚠️ Максимальне значення не може бути меншим за мінімальне ({min}). Введіть ще раз."
BAD_TEXT = "⚠️ Введіть текст до 200 символів."
RANGE_NOT_SET = "Жодної межі не вказано — параметр не застосовано."

CATEGORIES_UNAVAILABLE = "⚠️ Не вдалося завантажити категорії. Спробуйте ще раз трохи пізніше."

# --- Search results ---
RESULTS_FOUND = "Знайдено аукціонів: <b>{count}</b>"
ZERO_RESULTS = (
    "😕 За заданими параметрами зараз немає активних аукціонів.\n\n"
    "Увімкніть моніторинг і отримайте повідомлення одразу, як відповідний аукціон "
    "з'явиться в системі."
)
RESULT_PRICE = "💰 Стартова ціна: {price}"
RESULT_LINK = "🔗 Посилання на аукціон"
DEMO_BANNER = "🧪 <i>Демо-дані: тестовий режим без підключення до The Tender API.</i>"
API_ERROR = (
    "⚠️ Не вдалося отримати дані від The Tender. Параметри пошуку збережено — "
    "спробуйте ще раз."
)
BTN_MORE = "Показати ще"
BTN_ENABLE_MONITORING = "🔔 Увімкнути моніторинг"
BTN_CHANGE_PARAMS = "⚙️ Змінити параметри"
BTN_NEW_SEARCH = "🔎 Новий пошук"
BTN_RETRY = "🔄 Повторити"
PAGE_ALREADY_SHOWN = "Цю сторінку вже показано 👆"
MONITORING_SOON = "🔔 Моніторинги з'являться в одній з наступних версій бота."
CONFIRM_REPLACE_DRAFT = (
    "У вас є незавершені параметри пошуку.\n\nЗамінити їх параметрами з цього пошуку?"
)
CONFIRM_NEW_SEARCH = "У вас є незавершені параметри пошуку.\n\nОчистити їх і почати новий пошук?"
BTN_YES_REPLACE = "Так, замінити"
BTN_YES_NEW = "Так, новий пошук"
BTN_KEEP_CURRENT = "Ні, залишити поточні"

# --- Consultations (Iteration 3) ---
CONSULT_TITLE = "💬 <b>Консультація</b>"
CONSULT_INTRO = (
    "Наш менеджер зателефонує вам за номером <b>{phone}</b> і допоможе з участю "
    "в аукціонах.\n\nНатисніть кнопку нижче, щоб залишити заявку."
)
BTN_ORDER_CONSULTATION = "📞 Замовити консультацію"
CONSULT_WORKDAY = (
    "Заявку передано ✅\n\nНаш менеджер зв'яжеться з вами найближчим часом.\n\n"
    "📞 {company_phone}"
)
CONSULT_WEEKEND = (
    "Заявку передано ✅\n\nПоки тривають вихідні, наші менеджери змагаються за право першими "
    "вам зателефонувати 😄 Переможець зв'яжеться з вами в перший робочий день.\n\n"
    "📞 {company_phone}"
)
CONSULT_ALREADY = (
    "Ваша заявка на консультацію вже прийнята ✅\n\n"
    "Менеджер зв'яжеться з вами найближчим часом.\n\n📞 {company_phone}"
)

# Manager side
REQ_CARD = (
    "📋 <b>Заявка на консультацію №{id}</b>\n\n"
    "👤 {name}\n📞 {phone}\n💬 {username}\n🕒 {created}"
)
REQ_NEW_HEADER = "🆕 <b>Нова заявка!</b>"
BTN_CLAIM = "✅ Опрацювати"
BTN_INTERESTED = "👍 Зацікавлений"
BTN_DECLINED = "👎 Відмова"
REQ_IN_PROGRESS_MINE = "🟡 <b>В роботі у вас.</b> Після розмови оберіть результат:"
REQ_CLAIMED_BY = "✅ Взяв в роботу: <b>{manager}</b>"
REQ_CLAIMED_ALERT = "Заявку вже взяв в роботу {manager}"
REQ_COMPLETED = "✅ <b>Завершено:</b> {result}"
REQ_CLOSED_BY_ADMIN = "⛔️ <b>Закрито адміністратором</b>"
REQ_NOT_YOURS = "Ця заявка вже не у вас в роботі."
RESULT_LABELS = {"INTERESTED": "👍 Зацікавлений", "DECLINED": "👎 Відмова"}
ADMIN_FALLBACK_HEADER = (
    "⚠️ <b>Немає менеджерів, яким вдалося надіслати заявку.</b>\n"
    "Опрацюйте її, будь ласка, або призначте менеджера."
)

MY_REQUESTS_TITLE = "📋 <b>Мої заявки</b>"
BTN_ACTIVE = "🟡 В роботі"
BTN_COMPLETED = "✅ Завершені"
MY_REQUESTS_EMPTY_ACTIVE = "Зараз у вас немає заявок в роботі."
MY_REQUESTS_EMPTY_DONE = "Завершених заявок ще немає."
