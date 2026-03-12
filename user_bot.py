import os
import logging
from typing import Dict, Any, List, Optional

import requests
import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

# ---------------------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------------------

# Токен Telegram-бота
TOKEN = os.environ.get("USER_BOT_TOKEN", "8387381145:AAGnHA7Pm5e4O4Gm6L9ol-pDNQeM8xpNdT4")

# Базовый URL вашего Node API
API_BASE_URL = os.environ.get("API_BASE_URL", "https://rfid.gutario.com/api/api")

# Секрет, который Node проверяет в middleware/telegramAuth.js
# Должен совпадать с process.env.TG_BOT_TOKEN на сервере
API_AUTH_TOKEN = os.environ.get("TG_BOT_TOKEN", "b$RdMt8c40Fy*eUYJV[S#q74jhy[+ZTk@QH%go=8[cGcXiNorN+hLrD5BO]vp9BE")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

bot = telebot.TeleBot(TOKEN)

# ---------------------------------------------------------------------
# СТАТУСЫ (синхронизированы с Node)
# ---------------------------------------------------------------------

STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING_INFO = "waiting_info"
STATUS_CLOSED_CONFIRMED = "closed_confirmed"
STATUS_CLOSED_NOT_CONFIRMED = "closed_unconfirmed"
STATUS_REJECTED = "rejected"

# ---------------------------------------------------------------------
# ЯЗЫКИ / ПЕРЕВОДЫ
# ---------------------------------------------------------------------

LANG_RU = "ru"
LANG_KK = "kk"

BTN_NEW_RU = "Подать обращение"
BTN_STATUS_RU = "Проверить статус обращения"
BTN_MAP_SHARE_RU = "Карта для дольщиков"
BTN_MAP_ILLEGAL_RU = "Карта незаконного строительства"

BTN_NEW_KK = "Жаңа өтініш беру"
BTN_STATUS_KK = "Өтініш күйін тексеру"
BTN_MAP_SHARE_KK = "Үлескерлер картасы"
BTN_MAP_ILLEGAL_KK = "Заңсыз құрылыс картасы"

MAP_SHARE_URL = "https://mapc.gutario.com/?status=approved"
MAP_ILLEGAL_URL = "https://mapc.gutario.com/?"


def tr(lang: str, ru: str, kk: str) -> str:
    return ru if lang == LANG_RU else kk


STATUS_LABELS_RU = {
    STATUS_NEW: "🆕 Новое",
    STATUS_IN_PROGRESS: "🟡 В работе",
    STATUS_WAITING_INFO: "🕒 Ожидаем дополнительную информацию",
    STATUS_CLOSED_CONFIRMED: "✅ Закрыто – нарушение подтверждено",
    STATUS_CLOSED_NOT_CONFIRMED: "✅ Закрыто – нарушение не подтвердилось",
    STATUS_REJECTED: "🚫 Отклонено",
}
STATUS_LABELS_KK = {
    STATUS_NEW: "🆕 Жаңа",
    STATUS_IN_PROGRESS: "🟡 Өңделуде",
    STATUS_WAITING_INFO: "🕒 Қосымша ақпарат күтілуде",
    STATUS_CLOSED_CONFIRMED: "✅ Жабылды – бұзу расталды",
    STATUS_CLOSED_NOT_CONFIRMED: "✅ Жабылды – бұзу расталмады",
    STATUS_REJECTED: "🚫 Қабылданбады",
}


def status_label(lang: str, status: str) -> str:
    return (STATUS_LABELS_RU if lang == LANG_RU else STATUS_LABELS_KK).get(
        status, status
    )


def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == LANG_RU:
        kb.row(KeyboardButton(BTN_NEW_RU))
        kb.row(KeyboardButton(BTN_STATUS_RU))
        kb.row(KeyboardButton(BTN_MAP_SHARE_RU, web_app=WebAppInfo(url=MAP_SHARE_URL)))
        kb.row(KeyboardButton(BTN_MAP_ILLEGAL_RU, web_app=WebAppInfo(url=MAP_ILLEGAL_URL)))
    else:
        kb.row(KeyboardButton(BTN_NEW_KK))
        kb.row(KeyboardButton(BTN_STATUS_KK))
        kb.row(KeyboardButton(BTN_MAP_SHARE_KK, web_app=WebAppInfo(url=MAP_SHARE_URL)))
        kb.row(KeyboardButton(BTN_MAP_ILLEGAL_KK, web_app=WebAppInfo(url=MAP_ILLEGAL_URL)))
    return kb


# ---------------------------------------------------------------------
# СОСТОЯНИЯ
# ---------------------------------------------------------------------

STATE_NONE = None
STATE_CONSENT = "consent"
STATE_IDENTITY = "identity"
STATE_PHOTOS = "photos"
STATE_STREET = "street"
STATE_HOUSE = "house"
STATE_LANDMARK = "landmark"
STATE_DESCRIPTION = "description"
STATE_VIOLATION_TYPE = "violation_type"
STATE_DANGER = "danger"
STATE_NAME = "name"
STATE_CONTACT_METHOD = "contact_method"
STATE_PHONE = "phone"
STATE_EMAIL = "email"
STATE_CAN_CONTACT = "can_contact"
STATE_CONFIRM = "confirm"
STATE_STATUS_INPUT = "status_input"

# chat_id -> state / data
user_state: Dict[int, Optional[str]] = {}
user_data: Dict[int, Dict[str, Any]] = {}


def get_lang_by_chat(chat_id: int) -> str:
    return user_data.get(chat_id, {}).get("lang", LANG_RU)


def set_state(chat_id: int, state: Optional[str]) -> None:
    user_state[chat_id] = state
    logging.debug("STATE chat=%s -> %s", chat_id, state)


# ---------------------------------------------------------------------
# ОБЩИЙ ОБРАБОТЧИК ОШИБОК
# ---------------------------------------------------------------------
def handle_error(chat_id: int, where: str, e: Exception) -> None:
    logging.exception("Ошибка в %s: %s", where, e)
    lang = get_lang_by_chat(chat_id)
    try:
        bot.send_message(
            chat_id,
            tr(
                lang,
                f"Произошла ошибка в боте ({where}): {e}",
                f"Ботта қате болды ({where}): {e}",
            ),
        )
    except Exception:
        logging.exception("Не удалось отправить сообщение об ошибке пользователю")


# ---------------------------------------------------------------------
# ВЗАИМОДЕЙСТВИЕ С NODE API
# ---------------------------------------------------------------------

def _api_headers() -> Dict[str, str]:
    return {
        "x-telegram-bot-token": API_AUTH_TOKEN,
        "Content-Type": "application/json",
    }


def send_appeal_to_backend(data: Dict[str, Any]) -> str:
    """
    Отправляем обращение в Node API, возвращаем публичный номер.
    POST /api/telegram/appeals
    """
    url = f"{API_BASE_URL}/telegram/appeals"

    street = data.get("street") or ""
    house = data.get("house") or ""
    landmark = data.get("landmark") or ""
    description = data.get("description") or ""
    violation_type = data.get("violation_type") or ""
    danger_level_text = data.get("danger_level") or ""

    payload = {
        "street": street,
        "house": house,
        "landmark": landmark,
        "description": description,
        "violationType": violation_type,
        "dangerText": danger_level_text,
        "isAnonymous": data.get("is_anonymous", False),
        "applicantName": data.get("applicant_name"),
        "phone": data.get("phone"),
        "email": data.get("email"),
        "canContact": data.get("can_contact"),
        "files": data.get("photos") or [],  # список { file_id, type }
        "telegramUserId": data.get("chat_id"),
        "language": data.get("language"),
    }

    logging.info("Sending appeal to backend: %s", payload)

    resp = requests.post(url, json=payload, headers=_api_headers(), timeout=15)
    resp.raise_for_status()
    body = resp.json()
    logging.info("Backend response: %s", body)

    public_id = body.get("appealNumber") or str(body.get("appealId"))
    return public_id


def fetch_appeal_status(public_id: str, telegram_user_id: int) -> Optional[Dict[str, Any]]:
    """
    Запрашиваем статус обращения по публичному номеру.
    GET /api/telegram/appeals/:appealNumber
    Передаем telegramUserId, чтобы сервер отдал только "свои" обращения.
    """
    url = f"{API_BASE_URL}/telegram/appeals/{public_id}"

    headers = _api_headers()
    headers["x-telegram-user-id"] = str(telegram_user_id)

    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code == 404:
        return None

    resp.raise_for_status()
    return resp.json()


def send_status_info(chat_id: int, lang: str, public_id: str) -> None:
    try:
        appeal = fetch_appeal_status(public_id, chat_id)
    except Exception as e:
        handle_error(chat_id, "send_status_info(fetch_appeal_status)", e)
        return

    if not appeal:
        bot.send_message(
            chat_id,
            tr(
                lang,
                f"Обращение с номером {public_id} не найдено.",
                f"{public_id} нөмірлі өтініш табылмады.",
            ),
        )
        return

    status_code = appeal.get("status") or STATUS_NEW
    st = status_label(lang, status_code)

    comment = appeal.get("lastComment") or tr(
        lang, "Комментарий отсутствует.", "Түсініктеме жоқ."
    )

    addr = appeal.get("address") or tr(lang, "г. Уральск", "Орал қ.")
    deadline = appeal.get("deadline") or tr(lang, "не задан", "көрсетілмеген")

    text = (
        tr(lang, "Информация по обращению:", "Өтініш туралы ақпарат:")
        + f"\n\n{tr(lang, 'Номер', 'Нөмір')}: {appeal.get('number') or public_id}"
        + f"\n{tr(lang, 'Статус', 'Күйі')}: {st}"
        + f"\n{tr(lang, 'Адрес', 'Мекенжай')}: {addr}"
        + f"\n{tr(lang, 'Срок реагирования', 'Жауап мерзімі')}: {deadline}"
        + f"\n\n{tr(lang, 'Комментарий', 'Түсініктеме')}: {comment}"
    )
    bot.send_message(chat_id, text)


def send_map_link(chat_id: int, lang: str, kind: str) -> None:
    if kind == "share":
        title = tr(lang, BTN_MAP_SHARE_RU, BTN_MAP_SHARE_KK)
        url = MAP_SHARE_URL
    else:
        title = tr(lang, BTN_MAP_ILLEGAL_RU, BTN_MAP_ILLEGAL_KK)
        url = MAP_ILLEGAL_URL

    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(tr(lang, "Открыть карту", "Картаны ашу"), url=url))
    bot.send_message(chat_id, f"{title}:\n{url}", reply_markup=kb)


# ---------------------------------------------------------------------
# /start + выбор языка (inline-кнопки)
# ---------------------------------------------------------------------

@bot.message_handler(commands=["start"])
def cmd_start(message):
    try:
        chat_id = message.chat.id
        user_data.setdefault(chat_id, {})["lang"] = LANG_RU
        user_state[chat_id] = STATE_NONE

        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("Русский", callback_data="lang_ru"),
            InlineKeyboardButton("Қазақ тілі", callback_data="lang_kk"),
        )
        bot.send_message(
            chat_id,
            "Выберите язык / Тілді таңдаңыз:",
            reply_markup=markup,
        )
    except Exception as e:
        handle_error(message.chat.id, "cmd_start", e)


@bot.callback_query_handler(func=lambda c: c.data in ("lang_ru", "lang_kk"))
def cb_language(call):
    try:
        chat_id = call.message.chat.id
        lang = LANG_RU if call.data == "lang_ru" else LANG_KK
        user_data.setdefault(chat_id, {})["lang"] = lang

        bot.answer_callback_query(call.id)

        bot.edit_message_text(
            tr(
                lang,
                "Сервис Qurylys baqylau принимает обращения "
                "о возможных нарушениях в строительстве в г. Уральск.\n\n"
                "Выберите действие в меню ниже.",
                "Құрылыс қадағалау қызметі Орал қаласындағы құрылысқа "
                "қатысты мүмкін бұзулар туралы өтініштерді қабылдайды.\n\n"
                "Төмендегі мәзірден әрекетті таңдаңыз.",
            ),
            chat_id=chat_id,
            message_id=call.message.message_id,
        )

        bot.send_message(
            chat_id,
            tr(lang, "Выберите действие:", "Әрекетті таңдаңыз:"),
            reply_markup=main_menu_keyboard(lang),
        )
    except Exception as e:
        handle_error(call.message.chat.id, "cb_language", e)


# ---------------------------------------------------------------------
# /new – запуск диалога
# ---------------------------------------------------------------------

@bot.message_handler(commands=["new"])
def cmd_new(message):
    try:
        chat_id = message.chat.id
        lang = get_lang_by_chat(chat_id)

        user_data[chat_id] = {
            "lang": lang,
            "photos": [],
        }
        set_state(chat_id, STATE_CONSENT)

        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row(
            KeyboardButton(tr(lang, "Да, согласен", "Иә, келісемін")),
            KeyboardButton(tr(lang, "Нет", "Жоқ")),
        )

        bot.send_message(
            chat_id,
            tr(
                lang,
                "Перед отправкой обращения нужно согласиться на обработку "
                "персональных данных и передачу информации в уполномоченные "
                "органы г. Уральск.\n\nВы согласны?",
                "Өтініш жіберу үшін деректерді өңдеуге және Орал қаласының "
                "уәкілетті органдарына жіберуге келісу қажет.\n\nКелісесіз бе?",
            ),
            reply_markup=kb,
        )
    except Exception as e:
        handle_error(message.chat.id, "cmd_new", e)


# ---------------------------------------------------------------------
# /status – проверка статуса (через Node API)
# ---------------------------------------------------------------------

@bot.message_handler(commands=["status"])
def cmd_status(message):
    try:
        chat_id = message.chat.id
        lang = get_lang_by_chat(chat_id)
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Введите номер обращения, например: 25_000001",
                    "Өтініш нөмірін енгізіңіз, мысалы: 25_000001",
                ),
            )
            return

        public_id = parts[1].strip()
        send_status_info(chat_id, lang, public_id)
    except Exception as e:
        handle_error(message.chat.id, "cmd_status", e)


# ---------------------------------------------------------------------
# ХЭНДЛЕР МЕДИА (фото/видео/документы) во время шага PHOTOS
# ---------------------------------------------------------------------

@bot.message_handler(content_types=["photo", "video", "document"])
def handle_media(message):
    try:
        chat_id = message.chat.id
        if user_state.get(chat_id) != STATE_PHOTOS:
            return

        data = user_data.get(chat_id)
        if not data:
            return

        media_list: List[Dict[str, Any]] = data.get("photos", [])

        file_id = None
        media_type = None

        if message.photo:
            file_id = message.photo[-1].file_id
            media_type = "photo"
        elif message.video:
            file_id = message.video.file_id
            media_type = "video"
        elif message.document:
            file_id = message.document.file_id
            media_type = "document"

        if file_id:
            media_list.append(
                {
                    "file_id": file_id,
                    "type": media_type,
                }
            )
            data["photos"] = media_list
            user_data[chat_id] = data

            lang = get_lang_by_chat(chat_id)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Добавить ещё фото",
                        "Қосымша фото қосу",
                    )
                ),
                KeyboardButton(tr(lang, "Готово", "Дайын")),
            )

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Файл сохранён. Выберите: «Добавить ещё фото» или «Готово».",
                    "Файл сақталды. «Қосымша фото қосу» немесе «Дайын» таңдаңыз.",
                ),
                reply_markup=kb,
            )
    except Exception as e:
        handle_error(message.chat.id, "handle_media", e)


# ---------------------------------------------------------------------
# ОСНОВНОЙ ХЭНДЛЕР ТЕКСТА (весь диалог)
# ---------------------------------------------------------------------

@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        chat_id = message.chat.id
        text = (message.text or "").strip()

        # Команды уже обработаны отдельными хэндлерами
        if text.startswith("/"):
            return

        lang = get_lang_by_chat(chat_id)
        state = user_state.get(chat_id, STATE_NONE)
        data = user_data.get(chat_id)

        if text in (BTN_MAP_SHARE_RU, BTN_MAP_SHARE_KK):
            send_map_link(chat_id, lang, "share")
            return
        if text in (BTN_MAP_ILLEGAL_RU, BTN_MAP_ILLEGAL_KK):
            send_map_link(chat_id, lang, "illegal")
            return

        if state is STATE_NONE:
            if text in (BTN_NEW_RU, BTN_NEW_KK):
                cmd_new(message)
                return
            if text in (BTN_STATUS_RU, BTN_STATUS_KK):
                user_data.setdefault(chat_id, {})["lang"] = lang
                set_state(chat_id, STATE_STATUS_INPUT)
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Введите номер обращения:",
                        "Өтініш нөмірін енгізіңіз:",
                    ),
                )
                return

        if state == STATE_STATUS_INPUT:
            send_status_info(chat_id, lang, text)
            user_data.setdefault(chat_id, {})["lang"] = lang
            set_state(chat_id, STATE_NONE)
            return

        # В состоянии PHOTOS: обработка текстов "Добавить ещё фото"
        if state == STATE_PHOTOS:
            add_more_text = tr(lang, "Добавить ещё фото", "Қосымша фото қосу")
            if text == add_more_text:
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Отправьте ещё фото или видео объекта.",
                        "Қосымша фото немесе видео жіберіңіз.",
                    ),
                )
                return

        if state is STATE_NONE or not data:
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Выберите действие кнопками меню ниже.",
                    "Жаңа өтініш жіберу немесе күйін тексеру үшін мәзірдегі батырманы таңдаңыз.",
                ),
                reply_markup=main_menu_keyboard(lang),
            )
            return

        # 1. согласие
        if state == STATE_CONSENT:
            if "нет" in text.lower() or "жоқ" in text.lower():
                user_state[chat_id] = STATE_NONE
                user_data.pop(chat_id, None)
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Без согласия я не могу принять обращение. "
                        "Если передумаете, нажмите «Подать обращение».",
                        "Келісім болмаса, өтініш қабылданбайды. "
                        "Қайта жіберу үшін «Жаңа өтініш беру» батырмасын басыңыз.",
                    ),
                    reply_markup=main_menu_keyboard(lang),
                )
                return

            set_state(chat_id, STATE_IDENTITY)
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(tr(lang, "От своего имени", "Өз атыңыздан")),
                KeyboardButton(tr(lang, "Анонимно", "Анонимді")),
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Вы хотите подать обращение:\n1) От своего имени\n2) Анонимно",
                    "Өтіністі қалай жібересіз:\n1) Өз атыңыздан\n2) Анонимді түрде",
                ),
                reply_markup=kb,
            )
            return

        # 2. формат обращения
        if state == STATE_IDENTITY:
            data["is_anonymous"] = (
                "аноним" in text.lower() or "anonim" in text.lower()
            )
            user_data[chat_id] = data
            set_state(chat_id, STATE_PHOTOS)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(KeyboardButton(tr(lang, "Готово", "Дайын")))

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Прикрепите фото или видео объекта (можно из галереи). "
                    "Отправьте 1–5 файлов. Когда закончите, нажмите кнопку «Готово».",
                    "Нысанға қатысты фото немесе видео жіберіңіз "
                    "(галереядан болады). 1–5 файл. "
                    "Аяқтағанда «Дайын» батырмасын басыңыз.",
                ),
                reply_markup=kb,
            )
            return

        # 3. фото – любое текстовое => «готово»
        if state == STATE_PHOTOS:
            set_state(chat_id, STATE_STREET)
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Укажите улицу объекта в г. Уральск (например: «ул. Победы»).",
                    "Орал қаласындағы нысан көшесін көрсетіңіз "
                    "(мысалы: «Жеңіс көшесі»).",
                ),
            )
            return

        # 4. улица
        if state == STATE_STREET:
            data["street"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_HOUSE)
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Теперь введите дом / участок (например: «дом 10»).",
                    "Енді үй / телім нөмірін жазыңыз (мысалы: «10-үй»).",
                ),
            )
            return

        # 5. дом
        if state == STATE_HOUSE:
            data["house"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_LANDMARK)
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Уточните ориентир (двор, рядом с чем стройка). "
                    "Если уточнять нечего – напишите «Нет».",
                    "Қосымша ориентир жазыңыз (аула, қасында не бар). "
                    "Егер қажет емес болса – «Жоқ» деп жазыңыз.",
                ),
            )
            return

        # 6. ориентир
        if state == STATE_LANDMARK:
            if text.lower() in ("нет", "жоқ"):
                data["landmark"] = ""
            else:
                data["landmark"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_DESCRIPTION)
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Опишите ситуацию: что строят, почему считаете это "
                    "незаконным и с какого времени.",
                    "Жағдайды сипаттаңыз: нақты не салынып жатыр, "
                    "неге заңсыз деп ойлайсыз, қай уақыттан бері.",
                ),
            )
            return

        # 7. описание
        if state == STATE_DESCRIPTION:
            data["description"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_VIOLATION_TYPE)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(
                    tr(lang, "Строительство без разрешения", "Рұқсатсыз құрылыс")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Самовольная пристройка / перепланировка",
                        "Өз бетінше құрылыс",
                    )
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Захват дворовой / общественной территории",
                        "Аула/қоғамдық аумақты басып алу",
                    )
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нарушение благоустройства",
                        "Абаттандыру ережесін бұзу",
                    )
                )
            )
            kb.row(KeyboardButton(tr(lang, "Затрудняюсь ответить", "Айта алмаймын")))

            bot.send_message(
                chat_id,
                tr(lang, "Выберите тип нарушения:", "Бұзу түрін таңдаңыз:"),
                reply_markup=kb,
            )
            return

        # 8. тип нарушения
        if state == STATE_VIOLATION_TYPE:
            data["violation_type"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_DANGER)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(
                    tr(lang, "Да, есть явная опасность", "Иә, айқын қауіп бар")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(lang, "Есть потенциальная опасность", "Потенциалды қауіп бар")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нет угроз, только нарушение документов",
                        "Қауіп жоқ, тек құжаттар бұзылған",
                    )
                )
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Есть ли, по вашему мнению, угроза безопасности?",
                    "Өз ойыңызша қауіпсіздікке қауіп бар ма?",
                ),
                reply_markup=kb,
            )
            return

        # 9. опасность
        if state == STATE_DANGER:
            data["danger_level"] = text
            user_data[chat_id] = data

            if data.get("is_anonymous"):
                set_state(chat_id, STATE_CONFIRM)
                send_confirm(chat_id, lang, data)
                return

            set_state(chat_id, STATE_NAME)
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Укажите ваши ФИО (для официального ответа).",
                    "Ресми жауап үшін аты-жөніңізді жазыңыз.",
                ),
            )
            return

        # 10. ФИО
        if state == STATE_NAME:
            data["applicant_name"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_CONTACT_METHOD)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(tr(lang, "Телефон", "Телефон")),
                KeyboardButton(tr(lang, "Email", "Электрондық пошта")),
            )
            bot.send_message(
                chat_id,
                tr(lang, "Как с вами лучше связаться?", "Сізбен қалай байланысқан дұрыс?"),
                reply_markup=kb,
            )
            return

        # 11. способ связи
        if state == STATE_CONTACT_METHOD:
            if "mail" in text.lower() or "пошта" in text.lower():
                data["contact_method"] = "email"
                user_data[chat_id] = data
                set_state(chat_id, STATE_EMAIL)
                bot.send_message(
                    chat_id,
                    tr(lang, "Введите ваш email:", "Электрондық поштаңызды жазыңыз:"),
                )
            else:
                data["contact_method"] = "phone"
                user_data[chat_id] = data
                set_state(chat_id, STATE_PHONE)
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Введите номер телефона в формате +7...",
                        "+7 форматында телефон нөмірін жазыңыз...",
                    ),
                )
            return

        # 12. телефон
        if state == STATE_PHONE:
            data["phone"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_CAN_CONTACT)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(
                    tr(lang, "Да, можно связаться", "Иә, байланысуға болады")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нет, только письменный ответ",
                        "Жоқ, тек жазбаша жауап",
                    )
                )
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Можно ли с вами связаться для уточнения информации?",
                    "Қосымша ақпарат үшін сізбен байланысуға бола ма?",
                ),
                reply_markup=kb,
            )
            return

        # 13. email
        if state == STATE_EMAIL:
            data["email"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_CAN_CONTACT)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(
                    tr(lang, "Да, можно связаться", "Иә, байланысуға болады")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нет, только письменный ответ",
                        "Жоқ, тек жазбаша жауап",
                    )
                )
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Можно ли с вами связаться для уточнения информации?",
                    "Қосымша ақпарат үшін сізбен байланысуға бола ма?",
                ),
                reply_markup=kb,
            )
            return

        # 14. можно ли связаться
        if state == STATE_CAN_CONTACT:
            data["can_contact"] = not (
                "нет" in text.lower() or "жоқ" in text.lower()
            )
            user_data[chat_id] = data
            set_state(chat_id, STATE_CONFIRM)
            send_confirm(chat_id, lang, data)
            return

        # 15. подтверждение
        if state == STATE_CONFIRM:
            if ("отмен" in text.lower()) or ("болдыр" in text.lower()):
                user_state[chat_id] = STATE_NONE
                user_data.pop(chat_id, None)
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Обращение отменено. Чтобы начать заново, нажмите «Подать обращение».",
                        "Өтініш тоқтатылды. Қайта бастау үшін «Жаңа өтініш беру» батырмасын басыңыз.",
                    ),
                    reply_markup=main_menu_keyboard(lang),
                )
                return

            data["chat_id"] = chat_id
            data["user_id"] = message.from_user.id
            data["language"] = lang

            try:
                public_id = send_appeal_to_backend(data)
            except Exception as e:
                handle_error(chat_id, "send_appeal_to_backend", e)
                return

            user_state[chat_id] = STATE_NONE
            user_data.pop(chat_id, None)

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    f"Ваше обращение зарегистрировано.\nНомер: {public_id}\n"
                    "Сохраните этот номер – по нему можно проверить статус "
                    "через кнопку «Проверить статус обращения».",
                    f"Өтінішіңіз тіркелді.\nНөмірі: {public_id}\n"
                    "Осы нөмірді сақтаңыз – күйін «Өтініш күйін тексеру» "
                    "батырмасы арқылы тексеруге болады.",
                ),
                reply_markup=main_menu_keyboard(lang),
            )
            return

    except Exception as e:
        handle_error(message.chat.id, "handle_text", e)


# ---------------------------------------------------------------------
# Вспомогательная: показать сводку перед отправкой
# ---------------------------------------------------------------------

def send_confirm(chat_id: int, lang: str, data: Dict[str, Any]) -> None:
    photos = data.get("photos") or []
    is_anonymous = data.get("is_anonymous", False)

    lines = [
        tr(lang, "Проверьте данные обращения:", "Өтініш деректерін тексеріңіз:"),
        "",
        f"{tr(lang, 'Город', 'Қала')}: {tr(lang, 'Уральск', 'Орал')}",
        f"{tr(lang, 'Улица', 'Көше')}: {data.get('street', '')}",
        f"{tr(lang, 'Дом / участок', 'Үй / телім')}: {data.get('house', '')}",
        f"{tr(lang, 'Ориентир', 'Бағдар')}: {data.get('landmark', '')}",
        "",
        f"{tr(lang, 'Тип нарушения', 'Бұзу түрі')}: {data.get('violation_type', '')}",
        f"{tr(lang, 'Опасность', 'Қауіп деңгейі')}: {data.get('danger_level', '')}",
        "",
        f"{tr(lang, 'Описание', 'Сипаттама')}: {data.get('description', '')}",
        f"{tr(lang, 'Фото/видео', 'Фото/видео')}: {len(photos)}",
    ]
    if not is_anonymous:
        lines += [
            "",
            f"{tr(lang, 'Заявитель', 'Өтініш беруші')}: {data.get('applicant_name', '')}",
            f"Телефон: {data.get('phone', '')}",
            f"{tr(lang, 'Email', 'Электрондық пошта')}: {data.get('email', '')}",
        ]
    else:
        lines.append("")
        lines.append(tr(lang, "Обращение анонимное.", "Өтініш анонимді."))

    lines.append("")
    lines.append(
        tr(
            lang,
            "Отправить обращение?\nНапишите «Отправить» или «Отменить».",
            "Өтініш жіберілсін бе?\n«Жіберу» немесе «Болдырмау» деп жазыңыз.",
        )
    )

    bot.send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    logging.info("User bot Qurylys qadagalau (telebot) started")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
