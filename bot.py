from __future__ import annotations

import sqlite3
import logging
import os
import sys

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# ---- Проверка обязательных переменных окружения ----
TOKEN = os.getenv("TELEGRAM_TOKEN")
_support_chat_id = os.getenv("SUPPORT_CHAT_ID")

if not TOKEN:
    logger.error("TELEGRAM_TOKEN не задан в .env")
    sys.exit(1)
if not _support_chat_id:
    logger.error("SUPPORT_CHAT_ID не задан в .env")
    sys.exit(1)

try:
    SUPPORT_CHAT_ID = int(_support_chat_id)
except ValueError:
    logger.error(f"SUPPORT_CHAT_ID должен быть числом, получено: {_support_chat_id}")
    sys.exit(1)

conn = sqlite3.connect("support_bot.db", check_same_thread=False)

# ---- таблица маппинга сообщений ----
conn.execute(
    """
CREATE TABLE IF NOT EXISTS messages_mapping (
    user_chat_id       INTEGER,
    user_message_id    INTEGER,
    support_message_id INTEGER,
    PRIMARY KEY(user_chat_id, user_message_id)
)
"""
)

conn.execute(
    """
CREATE INDEX IF NOT EXISTS idx_support_message_id
ON messages_mapping (support_message_id)
"""
)

# ---- таблица связей пользователь → топик ----
conn.execute(
    """
CREATE TABLE IF NOT EXISTS user_topics (
    user_chat_id INTEGER PRIMARY KEY,
    topic_id     INTEGER NOT NULL,
    username     TEXT,
    first_name   TEXT
)
"""
)

# ---- таблица заблокированных пользователей ----
conn.execute(
    """
CREATE TABLE IF NOT EXISTS blocked_users (
    user_chat_id INTEGER PRIMARY KEY,
    blocked_at   TEXT NOT NULL,
    admin_id     INTEGER
)
"""
)

conn.commit()


# Дефолтные тексты
DEFAULT_GREETING = "Здравствуйте! Опишите Вашу проблему, и мы постараемся помочь \U0001F917"

# /help — текст + premium emoji (offsets рассчитываются в help_command)
HELP_TEXT = "\U0001F916 Основной бот — @virtezvpn_bot\n\U0001F49A Отзывы — @virtezvpn_feedback\n\U0001F4CC Новости — @virtezvpn"

FIRST_MESSAGE_TEXT = "Мы получили Ваше сообщение и скоро ответим \U0001F4AC"

MAX_CAPTION_LENGTH = 1024
MAX_MESSAGE_LENGTH = 4096


# ----------------- Утилиты -----------------
def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _shift_entities(entities: tuple[MessageEntity, ...] | None, offset: int) -> list[MessageEntity] | None:
    """Сдвигает offset всех entities на заданное значение."""
    if not entities:
        return None
    shifted = []
    for e in entities:
        shifted.append(MessageEntity(
            type=e.type,
            offset=e.offset + offset,
            length=e.length,
            url=e.url,
            user=e.user,
            language=e.language,
            custom_emoji_id=e.custom_emoji_id,
        ))
    return shifted


async def copy_message(message, chat_id: int, caption: str = None, **kwargs):
    """Пересылает сообщение любого типа в указанный чат, сохраняя форматирование и premium emoji."""
    bot = message.get_bot()

    if message.text:
        if caption:
            prefix = f"{caption}\n\n"
            text = prefix + message.text
            entities = _shift_entities(message.entities, len(prefix))
        else:
            text = message.text
            entities = message.entities
        return await bot.send_message(
            chat_id=chat_id,
            text=truncate(text, MAX_MESSAGE_LENGTH),
            entities=entities,
            **kwargs,
        )
    elif message.photo:
        if caption and message.caption:
            prefix = f"{caption}\n\n"
            full_cap = prefix + message.caption
            cap_entities = _shift_entities(message.caption_entities, len(prefix))
        else:
            full_cap = caption or message.caption or ""
            cap_entities = message.caption_entities if not caption else None
        return await bot.send_photo(
            chat_id=chat_id,
            photo=message.photo[-1].file_id,
            caption=truncate(full_cap, MAX_CAPTION_LENGTH),
            caption_entities=cap_entities,
            **kwargs,
        )
    elif message.video:
        if caption and message.caption:
            prefix = f"{caption}\n\n"
            full_cap = prefix + message.caption
            cap_entities = _shift_entities(message.caption_entities, len(prefix))
        else:
            full_cap = caption or message.caption or ""
            cap_entities = message.caption_entities if not caption else None
        return await bot.send_video(
            chat_id=chat_id,
            video=message.video.file_id,
            caption=truncate(full_cap, MAX_CAPTION_LENGTH),
            caption_entities=cap_entities,
            **kwargs,
        )
    elif message.animation:
        if caption and message.caption:
            prefix = f"{caption}\n\n"
            full_cap = prefix + message.caption
            cap_entities = _shift_entities(message.caption_entities, len(prefix))
        else:
            full_cap = caption or message.caption or ""
            cap_entities = message.caption_entities if not caption else None
        return await bot.send_animation(
            chat_id=chat_id,
            animation=message.animation.file_id,
            caption=truncate(full_cap, MAX_CAPTION_LENGTH),
            caption_entities=cap_entities,
            **kwargs,
        )
    elif message.document:
        if caption and message.caption:
            prefix = f"{caption}\n\n"
            full_cap = prefix + message.caption
            cap_entities = _shift_entities(message.caption_entities, len(prefix))
        else:
            full_cap = caption or message.caption or ""
            cap_entities = message.caption_entities if not caption else None
        return await bot.send_document(
            chat_id=chat_id,
            document=message.document.file_id,
            caption=truncate(full_cap, MAX_CAPTION_LENGTH),
            caption_entities=cap_entities,
            **kwargs,
        )
    elif message.voice:
        if caption and message.caption:
            prefix = f"{caption}\n\n"
            full_cap = prefix + message.caption
            cap_entities = _shift_entities(message.caption_entities, len(prefix))
        else:
            full_cap = caption or message.caption or ""
            cap_entities = message.caption_entities if not caption else None
        return await bot.send_voice(
            chat_id=chat_id,
            voice=message.voice.file_id,
            caption=truncate(full_cap, MAX_CAPTION_LENGTH),
            caption_entities=cap_entities,
            **kwargs,
        )
    elif message.audio:
        if caption and message.caption:
            prefix = f"{caption}\n\n"
            full_cap = prefix + message.caption
            cap_entities = _shift_entities(message.caption_entities, len(prefix))
        else:
            full_cap = caption or message.caption or ""
            cap_entities = message.caption_entities if not caption else None
        return await bot.send_audio(
            chat_id=chat_id,
            audio=message.audio.file_id,
            caption=truncate(full_cap, MAX_CAPTION_LENGTH),
            caption_entities=cap_entities,
            **kwargs,
        )
    elif message.video_note:
        return await bot.send_video_note(
            chat_id=chat_id,
            video_note=message.video_note.file_id,
            **kwargs,
        )
    elif message.sticker:
        return await bot.send_sticker(
            chat_id=chat_id,
            sticker=message.sticker.file_id,
            **kwargs,
        )
    elif message.contact:
        return await bot.send_contact(
            chat_id=chat_id,
            phone_number=message.contact.phone_number,
            first_name=message.contact.first_name,
            last_name=message.contact.last_name or "",
            **kwargs,
        )
    elif message.location:
        return await bot.send_location(
            chat_id=chat_id,
            latitude=message.location.latitude,
            longitude=message.location.longitude,
            **kwargs,
        )
    else:
        return None


# ----------------- Работа с БД / Блокировка -----------------

def is_user_blocked(user_chat_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM blocked_users WHERE user_chat_id = ?", (user_chat_id,)).fetchone()
    return row is not None


def toggle_user_block(user_chat_id: int, admin_id: int) -> bool:
    """Блокирует или разблокирует пользователя. Возвращает True если заблокирован."""
    if is_user_blocked(user_chat_id):
        conn.execute("DELETE FROM blocked_users WHERE user_chat_id = ?", (user_chat_id,))
        conn.commit()
        return False
    else:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO blocked_users (user_chat_id, blocked_at, admin_id) VALUES (?, ?, ?)",
            (user_chat_id, now, admin_id),
        )
        conn.commit()
        return True


# ----------------- Работа с БД / топики -----------------

def get_user_topic(user_chat_id: int) -> int | None:
    """Возвращает topic_id для пользователя или None."""
    row = conn.execute(
        "SELECT topic_id FROM user_topics WHERE user_chat_id = ?",
        (user_chat_id,),
    ).fetchone()
    return row[0] if row else None


def get_user_by_topic(topic_id: int) -> int | None:
    """Возвращает user_chat_id по topic_id."""
    row = conn.execute(
        "SELECT user_chat_id FROM user_topics WHERE topic_id = ?",
        (topic_id,),
    ).fetchone()
    return row[0] if row else None


def save_user_topic(user_chat_id: int, topic_id: int, username: str | None, first_name: str | None):
    conn.execute(
        "INSERT OR REPLACE INTO user_topics (user_chat_id, topic_id, username, first_name) VALUES (?, ?, ?, ?)",
        (user_chat_id, topic_id, username, first_name),
    )
    conn.commit()


def update_user_info(user_chat_id: int, username: str | None, first_name: str | None):
    conn.execute(
        "UPDATE user_topics SET username = ?, first_name = ? WHERE user_chat_id = ?",
        (username, first_name, user_chat_id),
    )
    conn.commit()


def save_mapping(user_chat_id: int, user_message_id: int, support_message_id: int):
    conn.execute(
        "INSERT OR REPLACE INTO messages_mapping (user_chat_id, user_message_id, support_message_id) VALUES (?, ?, ?)",
        (user_chat_id, user_message_id, support_message_id),
    )
    conn.commit()


def find_user_by_support_message(support_message_id: int) -> tuple | None:
    return conn.execute(
        "SELECT user_chat_id, user_message_id FROM messages_mapping WHERE support_message_id = ?",
        (support_message_id,),
    ).fetchone()


async def get_or_create_topic(context: ContextTypes.DEFAULT_TYPE, user_chat_id: int, username: str | None, first_name: str | None) -> tuple[int | None, bool]:
    """Возвращает (topic_id, is_new) для пользователя, создавая топик если нужно."""
    topic_id = get_user_topic(user_chat_id)
    if topic_id:
        update_user_info(user_chat_id, username, first_name)
        return topic_id, False

    display_name = username or first_name or f"User{user_chat_id}"
    topic_name = f"💬 {display_name}"

    try:
        forum_topic = await context.bot.create_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            name=topic_name[:128],
        )
        topic_id = forum_topic.message_thread_id
        logger.info(f"Создан топик {topic_id} для пользователя {user_chat_id}")
    except Exception as e:
        logger.error(f"Ошибка создания топика: {e}")
        return None

    save_user_topic(user_chat_id, topic_id, username, first_name)

    username_display = f"@{username}" if username else "Не указан"
    user_info = (
        f"🫡 <b>Досье на челика/b>\n\n"
        f"🆔 ID: <code>{user_chat_id}</code>\n"
        f"👤 По паспорту: {first_name or 'аноним'}\n"
        f"📱 Юзер: {username_display}"
    )
    try:
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=topic_id,
            text="🚨 Ало, работаем",
        )
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=topic_id,
            text=user_info,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка отправки информации о пользователе: {e}")

    return topic_id, True


# ----------------- Хендлеры пользователя -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_user_blocked(update.effective_user.id):
        return
    await update.message.reply_text(DEFAULT_GREETING)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_user_blocked(update.effective_user.id):
        return
    # 🤖 → pos 0 len 2, 💚 → after first \n, 📌 → after second \n
    line1 = "\U0001F916 Основной бот — @virtezvpn_bot\n"
    line2 = "\U0001F49A Отзывы — @virtezvpn_feedback\n"
    entities = [
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2, custom_emoji_id="5361741454685256344"),
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=len(line1), length=2, custom_emoji_id="5337080053119336309"),
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=len(line1) + len(line2), length=2, custom_emoji_id="5424818078833715060"),
    ]
    await update.message.reply_text(HELP_TEXT, entities=entities)


async def forward_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    user_chat_id = message.chat_id

    if is_user_blocked(user_chat_id):
        return

    topic_id, is_new = await get_or_create_topic(context, user_chat_id, user.username, user.first_name)
    if not topic_id:
        return

    if is_new:
        confirm_text = FIRST_MESSAGE_TEXT
        confirm_entities = [
            MessageEntity(
                type=MessageEntity.CUSTOM_EMOJI,
                offset=len("Мы получили Ваше сообщение и скоро ответим "),
                length=2,
                custom_emoji_id="5244583512878648726",
            ),
        ]
        await message.reply_text(confirm_text, entities=confirm_entities)

    username = f"@{user.username}" if user.username else "Не указан"
    header = f"💬 {user.first_name or 'Не указано'} ({username}):"

    keyboard = [
        [InlineKeyboardButton("🔨 Дать пизды/помиловать", callback_data=f"block_{user_chat_id}")]
    ]

    try:
        sent_message = await copy_message(
            message,
            SUPPORT_CHAT_ID,
            caption=header,
            message_thread_id=topic_id,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        if sent_message:
            save_mapping(user_chat_id, message.message_id, sent_message.message_id)
    except Exception as e:
        logger.error(f"Ошибка при пересылке сообщения: {e}")


# ----------------- Хендлеры поддержки -----------------
async def reply_from_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.chat_id != SUPPORT_CHAT_ID:
        return

    # Игнорируем сообщения от самого бота
    if message.from_user and message.from_user.id == context.bot.id:
        return

    user_chat_id = None

    # Способ 1: ответ на конкретное сообщение — ищем по маппингу
    if message.reply_to_message:
        found = find_user_by_support_message(message.reply_to_message.message_id)
        if found:
            user_chat_id = found[0]

    # Способ 2: сообщение в топике без reply — ищем пользователя по topic_id
    if not user_chat_id and message.message_thread_id:
        user_chat_id = get_user_by_topic(message.message_thread_id)

    if not user_chat_id:
        return

    if is_user_blocked(user_chat_id):
        await message.reply_text("🚫 Этот пацан на нарах, до него не доходит")
        return

    try:
        await copy_message(message, user_chat_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа пользователю: {e}")


# ----------------- Обработка кнопок -----------------
async def block_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("block_"):
        return

    try:
        target_user_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        return

    admin_id = query.from_user.id
    is_blocked_now = toggle_user_block(target_user_id, admin_id)

    res = conn.execute(
        "SELECT username, first_name FROM user_topics WHERE user_chat_id = ?",
        (target_user_id,),
    ).fetchone()
    if res:
        username, first_name = res
        username_str = f"@{username}" if username else "ноунейм"
        user_info = f"{first_name or 'Пацан'} ({username_str})"
    else:
        user_info = f"Пацан {target_user_id}"

    if is_blocked_now:
        text = f"🚔 {user_info} поехал на нары"
    else:
        text = f"🕊 {user_info} откинулся, добро пожаловать на волю"

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        message_thread_id=query.message.message_thread_id,
        text=text,
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")


def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CallbackQueryHandler(block_user_callback, pattern="^block_"))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.ALL ^ filters.COMMAND),
            forward_to_support,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Chat(SUPPORT_CHAT_ID) & ~filters.COMMAND,
            reply_from_support,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
