import os
import logging
from typing import Dict, Any, List

import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from db import (
    init_db,
    create_appeal,
    get_appeal,
    STATUS_NEW,
    STATUS_IN_PROGRESS,
    STATUS_WAITING_INFO,
    STATUS_CLOSED_CONFIRMED,
    STATUS_CLOSED_NOT_CONFIRMED,
    STATUS_REJECTED,
)

# ---------------------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------------------
TOKEN = os.environ.get("USER_BOT_TOKEN", "8387381145:AAGnHA7Pm5e4O4Gm6L9ol-pDNQeM8xpNdT4")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

bot = telebot.TeleBot(TOKEN)

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
STATE_VIOLATION_TYPE = "violation_type"
STATE_DANGER = "danger"
STATE_NAME = "name"
STATE_CONTACT_METHOD = "contact_method"
STATE_PHONE = "phone"
STATE_EMAIL = "email"
STATE_CAN_CONTACT = "can_contact"
STATE_CONFIRM = "confirm"

# chat_id -> state / data
user_state: Dict[int, str] = {}
user_data: Dict[int, Dict[str, Any]] = {}


def get_lang_by_chat(chat_id: int) -> str:
    return user_data.get(chat_id, {}).get("lang", LANG_RU)


def set_state(chat_id: int, state: str) -> None:
    user_state[chat_id] = state
    logging.debug(f"STATE chat={chat_id} -> {state}")


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
# /status – проверка статуса
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
                    "Введите номер обращения после команды, например:\n/status 25-000001",
                    "Búıryqtan keıin ótinish nómirin jazıńyz, mysaly:\n/status 25-000001",
                ),
            )
            return

        public_id = parts[1].strip()
        appeal = get_appeal(public_id)
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

        st = status_label(lang, appeal["status"])
        comment = appeal.get("last_comment") or tr(
            lang, "Комментарий отсутствует.", "Kommentariı joq."
        )

        text = (
            tr(lang, "Информация по обращению:", "Ótinish turaly aqparat:")
            + f"\n\n{tr(lang, 'Номер', 'Nómir')}: {appeal['public_id']}"
            + f"\n{tr(lang, 'Статус', 'Kúi')}: {st}"
            + f"\n{tr(lang, 'Адрес', 'Mekenjai')}: г. Уральск, "
            f"{appeal.get('street')}, {appeal.get('house')}"
            + f"\n{tr(lang, 'Срок реагирования', 'Jauap merzimi')}: {appeal.get('deadline')}"
            + f"\n\n{tr(lang, 'Комментарий', 'Kommentariı')}: {comment}"
        )
        bot.send_message(chat_id, text)
    except Exception as e:
        handle_error(message.chat.id, "cmd_status", e)


# ---------------------------------------------------------------------
# ХЭНДЛЕР МЕДИА (фото/видео) во время шага PHOTOS
# ---------------------------------------------------------------------
@bot.message_handler(content_types=["photo", "video"])
def handle_media(message):
    try:
        chat_id = message.chat.id
        if user_state.get(chat_id) != STATE_PHOTOS:
            return

        data = user_data.get(chat_id)
        if not data:
            return
        photos: List[str] = data.get("photos", [])

        file_id = None
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.video:
            file_id = message.video.file_id

        if file_id:
            photos.append(file_id)
            data["photos"] = photos
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
                    "Фото сохранено. Выберите: «Добавить ещё фото» или «Готово».",
                    "Foto saqtaldy. «Taǵy foto qosu» nemese «Gotovo» tańdańyz.",
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

        if text.startswith("/"):
            return

        lang = get_lang_by_chat(chat_id)
        state = user_state.get(chat_id, STATE_NONE)
        data = user_data.get(chat_id)

        # ������������� � ���� PHOTOS: ������ «�������� ��� �����»
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
                    "Чтобы проверить статус – /status <номер>.",
                    "Jańa ótinish úshin /new, kúin tekserý úshin – /status <nómir>.",
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
            data["is_anonymous"] = ("аноним" in text.lower()
                                    or "anonim" in text.lower())
            user_data[chat_id] = data
            set_state(chat_id, STATE_PHOTOS)

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row(KeyboardButton(tr(lang, "Готово", "Gotovo")))

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Прикрепите фото или видео объекта (можно из галереи). "
                    "Отправьте 1–5 файлов. Когда закончите, напишите «Готово».",
                    "Nysanǵa qatıstı foto nemese video jiberińiz "
                    "(galereıadan bolady). 1–5 faıl. "
                    "Aıaqtaganda «Gotovo» dep jazyńyz.",
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
            set_state(chat_id, STATE_VIOLATION_TYPE)

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

            public_id = create_appeal(data)

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
    init_db()
    logging.info("User bot Qurylys qadagalau (telebot) started")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()

