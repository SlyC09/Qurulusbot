# admin_bot.py – telebot, карточка обращения, статусы, комментарии, фото

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)

from db import (
    init_db,
    list_appeals,
    get_appeal,
    update_status,
    update_executor,
    export_appeals_csv,
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
ADMIN_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "8514321388:AAE8XjRnjj_B884_4phaHi3J5gPzaULas8c")
USER_TOKEN = os.environ.get("USER_BOT_TOKEN", "8387381145:AAGnHA7Pm5e4O4Gm6L9ol-pDNQeM8xpNdT4")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

bot = telebot.TeleBot(ADMIN_TOKEN)
user_bot = telebot.TeleBot(USER_TOKEN) if USER_TOKEN else None

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
    STATUS_WAITING_INFO: "🕒 Ожидаем инфо",
    STATUS_CLOSED_CONFIRMED: "✅ Закрыто (подтверждено)",
    STATUS_CLOSED_NOT_CONFIRMED: "✅ Закрыто (не подтвердилось)",
    STATUS_REJECTED: "🚫 Отклонено",
}
STATUS_LABELS_KK = {
    STATUS_NEW: "🆕 Жаңа",
    STATUS_IN_PROGRESS: "🟡 Өңделуде",
    STATUS_WAITING_INFO: "🕒 Қосымша ақпарат күтілуде",
    STATUS_CLOSED_CONFIRMED: "✅ Жабылды (бұзу расталды)",
    STATUS_CLOSED_NOT_CONFIRMED: "✅ Жабылды (бұзу расталмады)",
    STATUS_REJECTED: "🚫 Қабылданбады",
}


def status_label(lang: str, status: str) -> str:
    return (STATUS_LABELS_RU if lang == LANG_RU else STATUS_LABELS_KK).get(
        status, status
    )


def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == LANG_RU:
        kb.row(KeyboardButton("🆕 Новые обращения"))
        kb.row(KeyboardButton("📋 Последние обращения"))
        kb.row(KeyboardButton("🔎 По номеру"))
        kb.row(KeyboardButton("📤 Экспорт за 7 дней"))
    else:
        kb.row(KeyboardButton("🆕 Жаңа өтініштер"))
        kb.row(KeyboardButton("📋 Соңғы өтініштер"))
        kb.row(KeyboardButton("🔎 Номер бойынша"))
        kb.row(KeyboardButton("📤 7 күндік экспорт"))
    return kb


# ---------------------------------------------------------------------
# СОСТОЯНИЕ АДМИНА
# ---------------------------------------------------------------------
admin_data: Dict[int, Dict[str, Any]] = {}


def get_lang(chat_id: int) -> str:
    return admin_data.get(chat_id, {}).get("lang", LANG_RU)


def set_lang(chat_id: int, lang: str) -> None:
    data = admin_data.setdefault(chat_id, {})
    data["lang"] = lang


def handle_error(chat_id: int, where: str, e: Exception) -> None:
    logging.exception("Ошибка в %s: %s", where, e)
    lang = get_lang(chat_id)
    try:
        bot.send_message(
            chat_id,
            tr(
                lang,
                f"Произошла ошибка в админ-боте ({where}): {e}",
                f"Әкімшілік ботта қате болды ({where}): {e}",
            ),
        )
    except Exception:
        logging.exception("Не удалось отправить сообщение об ошибке админу")


# ---------------------------------------------------------------------
# ФОРМАТИРОВАНИЕ ОБРАЩЕНИЙ
# ---------------------------------------------------------------------
def format_appeal_short(a: dict, lang: str) -> str:
    return (
        f"{a['public_id']} | {status_label(lang, a['status'])}\n"
        f"{tr(lang,'Адрес','Мекенжай')}: {tr(lang, 'г. Уральск', 'Орал қ.')}, {a['street']}, {a['house']}\n"
        f"{tr(lang,'Тип','Түрі')}: {a['violation_type']}\n"
        f"{tr(lang,'Создано','Құрылған уақыты')}: {a['created_at']}\n"
    )


def format_appeal_full(a: dict, lang: str) -> str:
    return (
        f"{tr(lang,'Номер','Нөмір')}: {a['public_id']}\n"
        f"{tr(lang,'Статус','Күйі')}: {status_label(lang, a['status'])}\n"
        f"{tr(lang,'Город','Қала')}: {tr(lang, 'Уральск', 'Орал')}\n"
        f"{tr(lang,'Адрес','Мекенжай')}: {a['street']}, {a['house']}\n"
        f"{tr(lang,'Ориентир','Бағдар')}: {a.get('landmark') or '-'}\n"
        f"{tr(lang,'Тип нарушения','Бұзу түрі')}: {a['violation_type']}\n"
        f"{tr(lang,'Опасность','Қауіп деңгейі')}: {a['danger_level']}\n"
        f"{tr(lang,'Описание','Сипаттама')}: {a['description']}\n"
        f"{tr(lang,'Исполнитель','Орындаушы')}: {a.get('executor') or '-'}\n"
        f"{tr(lang,'Заявитель','Өтініш беруші')}: "
        f"{a.get('applicant_name') or tr(lang,'анонимно','анонимді')}\n"
        f"{tr(lang,'Телефон','Телефон')}: {a.get('phone') or '-'}\n"
        f"{tr(lang,'Email','Электрондық пошта')}: {a.get('email') or '-'}\n"
        f"{tr(lang,'Создано','Құрылған уақыты')}: {a['created_at']}\n"
        f"{tr(lang,'Срок реагирования','Жауап мерзімі')}: {a['deadline']}\n"
        f"{tr(lang,'Комментарий','Түсініктеме')}: {a.get('last_comment') or '-'}\n"
    )


# ---------------------------------------------------------------------
# УВЕДОМЛЕНИЕ ПОЛЬЗОВАТЕЛЮ
# ---------------------------------------------------------------------
def notify_user_about_status(appeal: dict, status_for_user: str, comment: str) -> None:
    if not user_bot:
        return
    chat_id = appeal.get("chat_id")
    if not chat_id:
        return

    user_lang = appeal.get("language") or LANG_RU
    if user_lang == LANG_KK:
        text = (
            f"Өтініш {appeal['public_id']} күйі жаңартылды.\n"
            f"Жаңа күйі: {status_for_user}\n"
            f"Түсініктеме: {comment or '-'}"
        )
    else:
        text = (
            f"Статус вашего обращения {appeal['public_id']} обновлён.\n"
            f"Новый статус: {status_for_user}\n"
            f"Комментарий: {comment or '-'}"
        )

    try:
        user_bot.send_message(chat_id, text)
    except Exception as e:
        logging.exception("Не удалось отправить уведомление пользователю: %s", e)


# ---------------------------------------------------------------------
# /start + выбор языка
# ---------------------------------------------------------------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    try:
        chat_id = message.chat.id
        set_lang(chat_id, LANG_RU)

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
        set_lang(chat_id, lang)

        bot.answer_callback_query(call.id)

        bot.edit_message_text(
            tr(
                lang,
                "Админ-панель «Құрылыс қадағалау» (г. Уральск).",
                "«Құрылыс қадағалау» әкімшілік панелі (Орал қ.).",
            ),
            chat_id=chat_id,
            message_id=call.message.message_id,
        )

        bot.send_message(
            chat_id,
            tr(lang, "Главное меню:", "Басты мәзір:"),
            reply_markup=main_menu_keyboard(lang),
        )
    except Exception as e:
        handle_error(call.message.chat.id, "cb_language", e)


# ---------------------------------------------------------------------
# СПИСКИ ОБРАЩЕНИЙ (с кнопками "Открыть")
# ---------------------------------------------------------------------
def send_new_list(chat_id: int, lang: str) -> None:
    appeals = list_appeals(status=STATUS_NEW, limit=20)
    if not appeals:
        bot.send_message(
            chat_id,
            tr(lang, "Новых обращений нет.", "Жаңа өтініштер жоқ."),
        )
        return

    text_lines: List[str] = [tr(lang, "Новые обращения:", "Жаңа өтініштер:")]
    kb = InlineKeyboardMarkup()
    for a in appeals:
        text_lines.append("")
        text_lines.append(format_appeal_short(a, lang))
        kb.row(
            InlineKeyboardButton(
                a["public_id"], callback_data=f"open:{a['public_id']}"
            )
        )

    bot.send_message(
        chat_id,
        "\n".join(text_lines),
        reply_markup=kb,
    )


def send_all_list(chat_id: int, lang: str) -> None:
    appeals = list_appeals(limit=20)
    if not appeals:
        bot.send_message(
            chat_id,
            tr(lang, "Обращений нет.", "Өтініштер жоқ."),
        )
        return

    text_lines: List[str] = [tr(lang, "Последние обращения:", "Соңғы өтініштер:")]
    kb = InlineKeyboardMarkup()
    for a in appeals:
        text_lines.append("")
        text_lines.append(format_appeal_short(a, lang))
        kb.row(
            InlineKeyboardButton(
                a["public_id"], callback_data=f"open:{a['public_id']}"
            )
        )

    bot.send_message(
        chat_id,
        "\n".join(text_lines),
        reply_markup=kb,
    )


# ---------------------------------------------------------------------
# КАРТОЧКА ОБРАЩЕНИЯ (со статусами, исполнителем и фото)
# ---------------------------------------------------------------------
def send_appeal_card(chat_id: int, lang: str, public_id: str) -> None:
    a = get_appeal(public_id)
    if not a:
        bot.send_message(
            chat_id,
            tr(lang, "Обращение не найдено.", "Өтініш табылмады."),
        )
        return

    # сначала фото, если есть
    photos_raw = a.get("photos") or "[]"
    try:
        photos = json.loads(photos_raw)
    except Exception:
        photos = []

    if photos:
        try:
            if len(photos) == 1:
                bot.send_photo(chat_id, photos[0])
            else:
                media = [InputMediaPhoto(p) for p in photos[:10]]
                bot.send_media_group(chat_id, media)
        except Exception as e:
            logging.exception("Не удалось отправить фото: %s", e)

    # потом текстовая карточка + кнопки
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(
            tr(lang, "Изменить статус", "Статусты өзгерту"),
            callback_data=f"change_status:{public_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            tr(lang, "Назначить исполнителя", "Орындаушыны тағайындау"),
            callback_data=f"assign_executor:{public_id}",
        )
    )

    bot.send_message(
        chat_id,
        format_appeal_full(a, lang),
        reply_markup=kb,
    )


# ---------------------------------------------------------------------
# КОМАНДЫ /new /all /appeal /export
# ---------------------------------------------------------------------
@bot.message_handler(commands=["new"])
def cmd_new(message):
    try:
        chat_id = message.chat.id
        lang = get_lang(chat_id)
        send_new_list(chat_id, lang)
    except Exception as e:
        handle_error(message.chat.id, "cmd_new", e)


@bot.message_handler(commands=["all"])
def cmd_all(message):
    try:
        chat_id = message.chat.id
        lang = get_lang(chat_id)
        send_all_list(chat_id, lang)
    except Exception as e:
        handle_error(message.chat.id, "cmd_all", e)


@bot.message_handler(commands=["appeal"])
def cmd_appeal(message):
    try:
        chat_id = message.chat.id
        lang = get_lang(chat_id)
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Укажите номер обращения, например:\n/appeal 25-000001",
                    "Өтініш нөмірін көрсетіңіз, мысалы: 25-000001",
                ),
            )
            return
        public_id = parts[1].strip()
        send_appeal_card(chat_id, lang, public_id)
    except Exception as e:
        handle_error(message.chat.id, "cmd_appeal", e)


@bot.message_handler(commands=["export"])
def cmd_export(message):
    try:
        chat_id = message.chat.id
        lang = get_lang(chat_id)
        days = 7
        start_date = datetime.utcnow() - timedelta(days=days)
        end_date = datetime.utcnow()

        bio = export_appeals_csv(start_date=start_date, end_date=end_date)
        bot.send_document(
            chat_id,
            (bio.name, bio),
            caption=tr(
                lang,
                f"Обращения за последние {days} дней.",
                f"Соңғы {days} күндегі өтініштер.",
            ),
        )
    except Exception as e:
        handle_error(message.chat.id, "cmd_export", e)


# ---------------------------------------------------------------------
# ТЕКСТ + МЕНЮ
# ---------------------------------------------------------------------
@bot.message_handler(func=lambda m: m.text is not None and not m.text.startswith("/"))
def handle_text(message):
    try:
        chat_id = message.chat.id
        text = message.text.strip()
        data = admin_data.setdefault(chat_id, {"lang": LANG_RU})
        lang = data.get("lang", LANG_RU)

        # ждём номер для карточки
        if data.get("awaiting_public_id"):
            data["awaiting_public_id"] = False
            public_id = text
            send_appeal_card(chat_id, lang, public_id)
            return

        # ждём комментарий после выбора статуса
        if data.get("awaiting_comment"):
            data["awaiting_comment"] = False
            pending = data.get("pending_status")
            if not pending:
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Внутренняя ошибка: нет сохранённого статуса.",
                        "Ішкі қате: статус сақталмаған.",
                    ),
                )
                return

            public_id = pending["public_id"]
            code = pending["code"]
            comment = None if text == "-" else text

            code_map = {
                "new": STATUS_NEW,
                "work": STATUS_IN_PROGRESS,
                "wait": STATUS_WAITING_INFO,
                "ok": STATUS_CLOSED_CONFIRMED,
                "nok": STATUS_CLOSED_NOT_CONFIRMED,
                "rej": STATUS_REJECTED,
            }
            status = code_map[code]

            update_status(public_id, status, comment)
            appeal = get_appeal(public_id)
            status_for_user = status_label(
                appeal.get("language") or LANG_RU, status
            )
            notify_user_about_status(appeal, status_for_user, comment or "")

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    f"Статус обращения {public_id} изменён на "
                    f"{status_label(lang, status)}.\nКомментарий: {comment or '-'}",
                    f"{public_id} өтінішінің статусы "
                    f"{status_label(lang, status)} болып өзгертілді.\n"
                    f"Түсініктеме: {comment or '-'}",
                ),
            )
            data["pending_status"] = None
            return

        # ждём исполнителя
        if data.get("awaiting_executor"):
            data["awaiting_executor"] = False
            public_id = data.get("pending_executor_public_id")
            if not public_id:
                bot.send_message(
                    chat_id,
                    tr(
                        lang,
                        "Внутренняя ошибка: нет номера обращения.",
                        "Ішкі қате: өтініш нөмірі жоқ.",
                    ),
                )
                return

            executor = text
            update_executor(public_id, executor)

            bot.send_message(
                chat_id,
                tr(
                    lang,
                    f"Исполнитель по обращению {public_id} установлен: {executor}",
                    f"{public_id} өтініші бойынша орындаушы орнатылды: {executor}",
                ),
            )
            data["pending_executor_public_id"] = None
            return

        # кнопки меню
        if "Новые обращения" in text or "Жаңа өтініштер" in text:
            send_new_list(chat_id, lang)
            return

        if "Последние обращения" in text or "Соңғы өтініштер" in text:
            send_all_list(chat_id, lang)
            return

        if "По номеру" in text or "Номер бойынша" in text:
            data["awaiting_public_id"] = True
            bot.send_message(
                chat_id,
                tr(
                    lang,
                    "Введите номер обращения:",
                    "Өтініш нөмірін енгізіңіз:",
                ),
            )
            return

        if "Экспорт" in text or "экспорт" in text:
            cmd_export(message)
            return

        # fallback
        bot.send_message(
            chat_id,
            tr(
                lang,
                "Используйте кнопки меню или команды /new, /all, /appeal, /export.",
                "Мәзір батырмаларын пайдаланыңыз.",
            ),
            reply_markup=main_menu_keyboard(lang),
        )
    except Exception as e:
        handle_error(message.chat.id, "handle_text", e)


# ---------------------------------------------------------------------
# CALLBACK-КНОПКИ: открыть, статус, исполнитель
# ---------------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: c.data.startswith("open:"))
def cb_open(call):
    try:
        chat_id = call.message.chat.id
        lang = get_lang(chat_id)
        _, public_id = call.data.split(":", 1)
        bot.answer_callback_query(call.id)
        send_appeal_card(chat_id, lang, public_id)
    except Exception as e:
        handle_error(call.message.chat.id, "cb_open", e)


@bot.callback_query_handler(func=lambda c: c.data.startswith("change_status:"))
def cb_change_status(call):
    try:
        chat_id = call.message.chat.id
        lang = get_lang(chat_id)
        _, public_id = call.data.split(":", 1)

        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton(
                status_label(lang, STATUS_NEW),
                callback_data=f"pick_status:{public_id}:new",
            )
        )
        kb.row(
            InlineKeyboardButton(
                status_label(lang, STATUS_IN_PROGRESS),
                callback_data=f"pick_status:{public_id}:work",
            )
        )
        kb.row(
            InlineKeyboardButton(
                status_label(lang, STATUS_WAITING_INFO),
                callback_data=f"pick_status:{public_id}:wait",
            )
        )
        kb.row(
            InlineKeyboardButton(
                status_label(lang, STATUS_CLOSED_CONFIRMED),
                callback_data=f"pick_status:{public_id}:ok",
            )
        )
        kb.row(
            InlineKeyboardButton(
                status_label(lang, STATUS_CLOSED_NOT_CONFIRMED),
                callback_data=f"pick_status:{public_id}:nok",
            )
        )
        kb.row(
            InlineKeyboardButton(
                status_label(lang, STATUS_REJECTED),
                callback_data=f"pick_status:{public_id}:rej",
            )
        )

        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            tr(lang, "Выберите новый статус:", "Жаңа статус таңдаңыз:"),
            reply_markup=kb,
        )
    except Exception as e:
        handle_error(call.message.chat.id, "cb_change_status", e)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pick_status:"))
def cb_pick_status(call):
    try:
        chat_id = call.message.chat.id
        lang = get_lang(chat_id)
        _, public_id, code = call.data.split(":")

        data = admin_data.setdefault(chat_id, {})
        data["pending_status"] = {"public_id": public_id, "code": code}
        data["awaiting_comment"] = True

        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            tr(
                lang,
                "Введите комментарий для заявителя (или '-' если без комментария):",
                "Өтініш берушіге арналған түсініктемені жазыңыз "
                "(немесе түсініктеме болмаса '-'):",
            ),
        )
    except Exception as e:
        handle_error(call.message.chat.id, "cb_pick_status", e)


@bot.callback_query_handler(func=lambda c: c.data.startswith("assign_executor:"))
def cb_assign_executor(call):
    try:
        chat_id = call.message.chat.id
        lang = get_lang(chat_id)
        _, public_id = call.data.split(":", 1)

        data = admin_data.setdefault(chat_id, {})
        data["awaiting_executor"] = True
        data["pending_executor_public_id"] = public_id

        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            tr(
                lang,
                "Введите исполнителя (ФИО или отдел):",
                "Орындаушыны енгізіңіз (АТЖ немесе бөлім атауы):",
            ),
        )
    except Exception as e:
        handle_error(call.message.chat.id, "cb_assign_executor", e)


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    init_db()
    logging.info("Admin bot Qurylys qadagalau Admin (telebot) started")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
