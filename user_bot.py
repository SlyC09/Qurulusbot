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
    STATUS_NEW: "🆕 Jańa",
    STATUS_IN_PROGRESS: "🟡 Óńdelýde",
    STATUS_WAITING_INFO: "🕒 Qosymsha aqparat kútilýde",
    STATUS_CLOSED_CONFIRMED: "✅ Jabýldy – buzý rastaldy",
    STATUS_CLOSED_NOT_CONFIRMED: "✅ Jabýldy – buzý rastalmády",
    STATUS_REJECTED: "🚫 Qabyldanbadı",
}


def status_label(lang: str, status: str) -> str:
    return (STATUS_LABELS_RU if lang == LANG_RU else STATUS_LABELS_KK).get(
        status, status
    )


def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == LANG_RU:
        kb.add(KeyboardButton("/new – Подать обращение"))
        kb.add(KeyboardButton("/status – Статус обращения"))
    else:
        kb.add(KeyboardButton("/new – Jańa ótinish"))
        kb.add(KeyboardButton("/status – Ótinish kúiі"))
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

# NEW: необязательный комментарий пользователя
STATE_USER_COMMENT_ASK = "user_comment_ask"
STATE_USER_COMMENT = "user_comment"

STATE_VIOLATION_TYPE = "violation_type"
STATE_DANGER = "danger"
STATE_NAME = "name"
STATE_CONTACT_METHOD = "contact_method"
STATE_PHONE = "phone"
STATE_EMAIL = "email"
STATE_CAN_CONTACT = "can_contact"
STATE_CONFIRM = "confirm"

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
                f"Botte qate boldy ({where}): {e}",
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
    user_comment = data.get("user_comment") or ""
    violation_type = data.get("violation_type") or ""
    danger_level_text = data.get("danger_level") or ""

    payload = {
        "street": street,
        "house": house,
        "landmark": landmark,
        "description": description,
        "userComment": user_comment or None,  # NEW: комментарий пользователя (опц.)
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


def fetch_appeal_status(public_id: str) -> Optional[Dict[str, Any]]:
    """
    Запрашиваем статус обращения по публичному номеру.
    GET /api/telegram/appeals/:appealNumber
    """
    url = f"{API_BASE_URL}/telegram/appeals/{public_id}"
    resp = requests.get(url, headers=_api_headers(), timeout=10)

    if resp.status_code == 404:
        return None

    resp.raise_for_status()
    return resp.json()


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
            InlineKeyboardButton("Qazaq tili", callback_data="lang_kk"),
        )
        bot.send_message(
            chat_id,
            "Выберите язык / Tıldı tańdańyz:",
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
                "Сервис Qurylys qadagalau принимает обращения "
                "о возможных нарушениях в строительстве в г. Уральск.\n\n"
                "Команды:\n"
                "/new – подать новое обращение\n"
                "/status <номер> – проверить статус",
                "Qurylys qadagalau qyzmeti Oral qalasyndaǵy qurylysqa "
                "qatýstı múmkindik buzýlar turaly ótinishterdi qab̆yldaıdy.\n\n"
                "Búıryqtar:\n"
                "/new – jańa ótinish\n"
                "/status <nómir> – kúin tekserý",
            ),
            chat_id=chat_id,
            message_id=call.message.message_id,
        )

        bot.send_message(
            chat_id,
            tr(lang, "Выберите действие:", "Əreketti tańdańyz:"),
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
            KeyboardButton(tr(lang, "Да, согласен", "Iá, kelisemіn")),
            KeyboardButton(tr(lang, "Нет", "Joq")),
        )

        bot.send_message(
            chat_id,
            tr(
                lang,
                "Перед отправкой обращения нужно согласиться на обработку "
                "персональных данных и передачу информации в уполномоченные "
                "органы г. Уральск.\n\nВы согласны?",
                "Ótinis jiberý úshin derekterdi óńdeýge jäne Oral qalasynyń "
                "uәkiletti organdaryna jіberýge kelisý qajet.\n\nKelisесiz бе?",
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
                    "Введите номер обращения после команды, например:\n/status 25_000001",
                    "Búıryqtan keıin ótinish nómirin jazıńyz, mysaly:\n/status 25_000001",
                ),
            )
            return

        public_id = parts[1].strip()

        try:
            appeal = fetch_appeal_status(public_id)
        except Exception as e:
            handle_error(chat_id, "cmd_status(fetch_appeal_status)", e)
            return

        if not appeal:
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    f"Обращение с номером {public_id} не найдено.",
                    f"{public_id} nómirli ótinish tabylmady.",
                ),
            )
            return

        status_code = appeal.get("status") or STATUS_NEW
        st = status_label(lang, status_code)

        comment = appeal.get("lastComment") or tr(
            lang, "Комментарий отсутствует.", "Kommentariı joq."
        )

        addr = appeal.get("address") or "г. Уральск"
        deadline = appeal.get("deadline") or tr(
            lang, "не задан", "kórsetilmegen"
        )

        text = (
            tr(lang, "Информация по обращению:", "Ótinish turaly aqparat:")
            + f"\n\n{tr(lang, 'Номер', 'Nómir')}: {appeal.get('number') or public_id}"
            + f"\n{tr(lang, 'Статус', 'Kúi')}: {st}"
            + f"\n{tr(lang, 'Адрес', 'Mekenjai')}: {addr}"
            + f"\n{tr(lang, 'Срок реагирования', 'Jauap merzimi')}: {deadline}"
            + f"\n\n{tr(lang, 'Комментарий', 'Kommentariı')}: {comment}"
        )
        bot.send_message(chat_id, text)
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
                        "Taǵy foto qosu",
                    )
                ),
                KeyboardButton(tr(lang, "Готово", "Gotovo")),
            )

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Файл сохранён. Выберите: «Добавить ещё фото» или «Готово».",
                    "Faıl saqtaldy. «Taǵy foto qosu» nemese «Gotovo» tańdańyz.",
                ),
                reply_markup=kb,
            )
    except Exception as e:
        handle_error(message.chat.id, "handle_media", e)


# ---------------------------------------------------------------------
# Вспомогательная: показать выбор типа нарушения
# ---------------------------------------------------------------------
def send_violation_type_prompt(chat_id: int, lang: str) -> None:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(
        KeyboardButton(
            tr(lang, "Строительство без разрешения", "Rúqsatsyz qurylys")
        )
    )
    kb.row(
        KeyboardButton(
            tr(
                lang,
                "Самовольная пристройка / перепланировка",
                "Óz betterińshe qurylys",
            )
        )
    )
    kb.row(
        KeyboardButton(
            tr(
                lang,
                "Захват дворовой / общественной территории",
                "Aýlaq/qoǵamdyq aumaqty basyp alu",
            )
        )
    )
    kb.row(
        KeyboardButton(
            tr(
                lang,
                "Нарушение благоустройства",
                "Abattylandyrý erejesin búzý",
            )
        )
    )
    kb.row(KeyboardButton(tr(lang, "Затрудняюсь ответить", "Aıta almaı́myn")))

    bot.send_message(
        chat_id,
        tr(lang, "Выберите тип нарушения:", "Buzý túrin tańdańyz:"),
        reply_markup=kb,
    )


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

        # В состоянии PHOTOS: обработка текстов "Добавить ещё фото"
        if state == STATE_PHOTOS:
            add_more_text = tr(lang, "Добавить ещё фото", "Taǵy foto qosu")
            if text == add_more_text:
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Отправьте ещё фото или видео объекта.",
                        "Taǵy foto nemese video jiberińiz.",
                    ),
                )
                return

        if state is STATE_NONE or not data:
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Чтобы подать обращение, введите /new.\n"
                    "Чтобы проверить статус – /status <номер>. ",
                    "Jańa ótinish úshin /new, kúin tekserý úshin – /status <nómir>. ",
                ),
                reply_markup=main_menu_keyboard(lang),
            )
            return

        # 1. согласие
        if state == STATE_CONSENT:
            if "нет" in text.lower() or "joq" in text.lower():
                user_state[chat_id] = STATE_NONE
                user_data.pop(chat_id, None)
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Без согласия я не могу принять обращение. "
                        "Если передумаете – введите /new.",
                        "Kelisim bolmasa, ótinish qab̆yldanbaıdy. "
                        "Qaıta ózgertseńiz – /new jazıńyz.",
                    ),
                    reply_markup=main_menu_keyboard(lang),
                )
                return

            set_state(chat_id, STATE_IDENTITY)
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(tr(lang, "От своего имени", "Óz atyńyzdan")),
                KeyboardButton(tr(lang, "Анонимно", "Anonimdi")),
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Вы хотите подать обращение:\n1) От своего имени\n2) Анонимно",
                    "Ótinisti qalaı jiberesiz:\n1) Óz atyńyzdan\n2) Anonimdi túrde",
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
            kb.row(KeyboardButton(tr(lang, "Готово", "Gotovo")))

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Прикрепите фото или видео объекта (можно из галереи). "
                    "Отправьте 1–5 файлов. Когда закончите, нажмите кнопку «Готово».",
                    "Nysanǵa qatıstı foto nemese video jiberińiz "
                    "(galereıadan bolady). 1–5 faıl. "
                    "Aıaqtaganda «Gotovo» batyrmasyn basyńyz.",
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
                    "Oral qalasyndaǵy nysan kóshesin kórsetińiz "
                    "(mysaly: «Pobeda kóshesi»).",
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
                    "Endi úı / telem nómirin jazıńyz (mysaly: «10-úı»).",
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
                    "Qosymsha orientir jazıńyz (aýlaq, qasynda ne bar). "
                    "Eger qajet emes bolsa – «Joq» dep jazyńyz.",
                ),
            )
            return

        # 6. ориентир
        if state == STATE_LANDMARK:
            if text.lower() in ("нет", "joq"):
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
                    "Jagdaídy sypattanyz: naqty ne salynyp jatyr, "
                    "nege zańsyz dep oılaísyz, qaı uaqyttan beri.",
                ),
            )
            return

        # 7. описание
        if state == STATE_DESCRIPTION:
            data["description"] = text
            user_data[chat_id] = data

            # NEW: спросим про необязательный комментарий
            set_state(chat_id, STATE_USER_COMMENT_ASK)
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(tr(lang, "Да", "Iá")),
                KeyboardButton(tr(lang, "Нет", "Joq")),
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Хотите добавить дополнительный комментарий к обращению? (необязательно)",
                    "Ótiniske qosymsha kommentariı qosasız ba? (mіndetti emes)",
                ),
                reply_markup=kb,
            )
            return

        # 7.1 спросить/добавить комментарий пользователя
        if state == STATE_USER_COMMENT_ASK:
            low = text.lower()

            # более терпимый парсинг "да/нет" под обе локали
            yes = (
                ("да" in low)
                or ("иә" in low)  # на всякий случай, если введут кириллицей
                or ("иа" in low)
                or ("iá" in low)
                or (low == "ia")
                or ("yes" in low)
            )
            no = (("нет" in low) or ("joq" in low) or ("жоқ" in low))

            if yes:
                set_state(chat_id, STATE_USER_COMMENT)
                kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                kb.row(KeyboardButton(tr(lang, "Пропустить", "Ótkizý")))
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Напишите комментарий (например: уточнение по месту/времени).",
                        "Kommentariıńyzdy jazıńyz (mysaly: oryn/uaqyt boıynsha naqtylaý).",
                    ),
                    reply_markup=kb,
                )
                return

            if no:
                data["user_comment"] = ""
                user_data[chat_id] = data
                set_state(chat_id, STATE_VIOLATION_TYPE)
                send_violation_type_prompt(chat_id, lang)
                return

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Пожалуйста, выберите «Да» или «Нет».",
                    "«Iá» nemese «Joq» tańdańyz.",
                ),
            )
            return

        # 7.2 ввод комментария пользователя
        if state == STATE_USER_COMMENT:
            skip_text = tr(lang, "Пропустить", "Ótkizý")
            if text == skip_text or text.lower() in ("нет", "joq", "жоқ"):
                data["user_comment"] = ""
            else:
                data["user_comment"] = text

            user_data[chat_id] = data
            set_state(chat_id, STATE_VIOLATION_TYPE)
            send_violation_type_prompt(chat_id, lang)
            return

        # 8. тип нарушения
        if state == STATE_VIOLATION_TYPE:
            data["violation_type"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_DANGER)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(
                KeyboardButton(
                    tr(lang, "Да, есть явная опасность", "Iá, ańǵarılǵan qaýip bar")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(lang, "Есть потенциальная опасность", "Múmkindik qaýip bar")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нет угроз, только нарушение документов",
                        "Qaýip joq, tek qujattar búzylǵan",
                    )
                )
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Есть ли, по вашему мнению, угроза безопасности?",
                    "Óz oııńyzsha qaýipsizdikke qaýip bar ma?",
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
                    "Resmı jauap úshin aty-jónińizdi jazıńyz.",
                ),
            )
            return

        # 10. ФИО
        if state == STATE_NAME:
            data["applicant_name"] = text
            user_data[chat_id] = data
            set_state(chat_id, STATE_CONTACT_METHOD)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(KeyboardButton("Телефон"), KeyboardButton("Email"))
            bot.send_message(
                chat_id,
                tr(lang, "Как с вами лучше связаться?", "Qaı arqyly baılanysqan jaqsy?"),
                reply_markup=kb,
            )
            return

        # 11. способ связи
        if state == STATE_CONTACT_METHOD:
            if "mail" in text.lower():
                data["contact_method"] = "email"
                user_data[chat_id] = data
                set_state(chat_id, STATE_EMAIL)
                bot.send_message(
                    chat_id,
                    tr(lang, "Введите ваш email:", "Email mekenjaıyn jazıńyz:"),
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
                        "+7 formatynda telefon nómirin jazıńyz...",
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
                    tr(lang, "Да, можно связаться", "Iá, baılanysýǵa bolady")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нет, только письменный ответ",
                        "Joq, tek jazbasha jauap",
                    )
                )
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Можно ли с вами связаться для уточнения информации?",
                    "Qosymsha aqparat úshin sizben baılanysýǵa bola ma?",
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
                    tr(lang, "Да, можно связаться", "Iá, baılanysýǵa bolady")
                )
            )
            kb.row(
                KeyboardButton(
                    tr(
                        lang,
                        "Нет, только письменный ответ",
                        "Joq, tek jazbasha jauap",
                    )
                )
            )
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Можно ли с вами связаться для уточнения информации?",
                    "Qosymsha aqparat úshin sizben baılanysýǵa bola ma?",
                ),
                reply_markup=kb,
            )
            return

        # 14. можно ли связаться
        if state == STATE_CAN_CONTACT:
            data["can_contact"] = not (
                "нет" in text.lower() or "joq" in text.lower()
            )
            user_data[chat_id] = data
            set_state(chat_id, STATE_CONFIRM)
            send_confirm(chat_id, lang, data)
            return

        # 15. подтверждение
        if state == STATE_CONFIRM:
            if ("отмен" in text.lower()) or ("boldyr" in text.lower()):
                user_state[chat_id] = STATE_NONE
                user_data.pop(chat_id, None)
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Обращение отменено. Чтобы начать заново – введите /new.",
                        "Ótinish boldyrý boldy. Qaıta bastau úshin – /new jazıńyz.",
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
                    f"командой /status {public_id}",
                    f"Ótinisingiz tirkelді.\nNómiri: {public_id}\n"
                    "Osy nómirdi saqtyńyz – kúin /status {public_id} búıryǵymen "
                    "tekserýge bolady.",
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
        tr(lang, "Проверьте данные обращения:", "Ótinish derekterin tekserińiz:"),
        "",
        f"{tr(lang, 'Город', 'Qala')}: Уральск",
        f"{tr(lang, 'Улица', 'Kóshe')}: {data.get('street', '')}",
        f"{tr(lang, 'Дом / участок', 'Úi / telem')}: {data.get('house', '')}",
        f"{tr(lang, 'Ориентир', 'Orientir')}: {data.get('landmark', '')}",
        "",
        f"{tr(lang, 'Тип нарушения', 'Buzý túri')}: {data.get('violation_type', '')}",
        f"{tr(lang, 'Опасность', 'Qaýip deńgeıi')}: {data.get('danger_level', '')}",
        "",
        f"{tr(lang, 'Описание', 'Sypattama')}: {data.get('description', '')}",
        f"{tr(lang, 'Фото/видео', 'Foto/video')}: {len(photos)}",
    ]

    # NEW: отображаем комментарий пользователя (если есть)
    user_comment = (data.get("user_comment") or "").strip()
    if user_comment:
        lines.append(f"{tr(lang, 'Комментарий', 'Kommentariı')}: {user_comment}")

    if not is_anonymous:
        lines += [
            "",
            f"{tr(lang, 'Заявитель', 'Ótinisshi')}: {data.get('applicant_name', '')}",
            f"Телефон: {data.get('phone', '')}",
            f"Email: {data.get('email', '')}",
        ]
    else:
        lines.append("")
        lines.append(tr(lang, "Обращение анонимное.", "Ótinish anonimdik."))

    lines.append("")
    lines.append(
        tr(
            lang,
            "Отправить обращение?\nНапишите «Отправить» или «Отменить».",
            "Ótinish jiberilsin be?\n«Jiberý» nemese «Boldyrmaý» dep jazyńyz.",
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
