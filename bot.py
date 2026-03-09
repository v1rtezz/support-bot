from __future__ import annotations

import sqlite3
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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

# Временная зона МСК
MSK = ZoneInfo("Europe/Moscow")

conn = sqlite3.connect("support_bot.db", check_same_thread=False)

# ---- таблица маппинга сообщений ----
conn.execute(
    """
CREATE TABLE IF NOT EXISTS messages_mapping (
    user_chat_id       INTEGER,
    user_message_id    INTEGER,
    support_message_id INTEGER,
    ticket_id          INTEGER,
    PRIMARY KEY(user_chat_id, user_message_id)
)
"""
)

# ---- индекс для поиска по support_message_id ----
conn.execute(
    """
CREATE INDEX IF NOT EXISTS idx_support_message_id
ON messages_mapping (support_message_id)
"""
)

# ---- таблица тикетов ----
conn.execute(
    """
CREATE TABLE IF NOT EXISTS tickets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_chat_id   INTEGER NOT NULL,
    username       TEXT,
    first_name     TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    topic_id       INTEGER
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
DEFAULT_GREETING = (
    "Здравствуйте!\n\n"
    "Напишите Ваш вопрос, и мы ответим Вам в ближайшее время.\n\n"
    "🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК"
)

DEFAULT_HELP = (
    "🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК\n\n"
    "📝 Заполняйте тикет внимательно и кратко, но максимально подробно. "
    "Помните, что это не чат с техподдержкой в реальном времени. Все тикеты обрабатываются в порядке очереди.\n\n"
    "⌛️ Возможно придётся подождать некоторое время, прежде чем вы получите ответ на свой вопрос."
)

MAX_CAPTION_LENGTH = 1024
MAX_MESSAGE_LENGTH = 4096


# ----------------- Утилиты -----------------
def format_datetime(iso_string: str) -> str:
    """Конвертирует ISO datetime в читаемый формат МСК"""
    try:
        dt = datetime.fromisoformat(iso_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_msk = dt.astimezone(MSK)
        return dt_msk.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_string


def truncate(text: str, limit: int) -> str:
    """Обрезает текст до limit символов, добавляя '…' если обрезан"""
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
    """Пересылает сообщение любого типа в указанный чат, сохраняя форматирование и premium emoji.

    caption — header, добавляемый к медиа/тексту (обрезается до лимитов).
    Entities корректно сдвигаются при добавлении header.
    """
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
    """Проверяет, заблокирован ли пользователь"""
    row = conn.execute("SELECT 1 FROM blocked_users WHERE user_chat_id = ?", (user_chat_id,)).fetchone()
    return row is not None


def toggle_user_block(user_chat_id: int, admin_id: int) -> bool:
    """Блокирует или разблокирует пользователя."""
    if is_user_blocked(user_chat_id):
        conn.execute("DELETE FROM blocked_users WHERE user_chat_id = ?", (user_chat_id,))
        conn.commit()
        return False
    else:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO blocked_users (user_chat_id, blocked_at, admin_id) VALUES (?, ?, ?)",
            (user_chat_id, now, admin_id),
        )
        conn.commit()
        return True


# ----------------- Работа с БД / тикетами -----------------
def get_open_ticket(user_chat_id: int):
    """Возвращает ID и topic_id открытого тикета пользователя"""
    row = conn.execute(
        """
        SELECT id, topic_id FROM tickets
        WHERE user_chat_id = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_chat_id,),
    ).fetchone()
    return row if row else None


def get_last_closed_ticket(user_chat_id: int):
    """Возвращает ID и topic_id последнего закрытого тикета пользователя"""
    row = conn.execute(
        """
        SELECT id, topic_id FROM tickets
        WHERE user_chat_id = ? AND status = 'closed' AND topic_id IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_chat_id,),
    ).fetchone()
    return row if row else None


def get_ticket_by_topic_id(topic_id: int):
    """Возвращает открытый тикет по topic_id"""
    row = conn.execute(
        "SELECT id, user_chat_id FROM tickets WHERE topic_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (topic_id,),
    ).fetchone()
    return row if row else None


async def create_or_reopen_ticket(context: ContextTypes.DEFAULT_TYPE, user_chat_id: int, username: str = None, first_name: str = None) -> tuple:
    """Создает новый тикет или переоткрывает последний закрытый (переиспользуя топик)."""
    now = datetime.now(timezone.utc).isoformat()

    # Пробуем переиспользовать закрытый тикет с существующим топиком
    closed = get_last_closed_ticket(user_chat_id)
    if closed:
        ticket_id, topic_id = closed
        conn.execute(
            "UPDATE tickets SET status = 'open', updated_at = ? WHERE id = ?",
            (now, ticket_id),
        )
        conn.commit()
        await update_topic_status(context, ticket_id, "open")
        logger.info(f"Переоткрыт тикет #{ticket_id} (топик {topic_id}) для пользователя {user_chat_id}")
        return ticket_id, topic_id

    # Нет закрытого тикета — создаём новый топик
    topic_id = None
    display_name = username if username else (first_name if first_name else f"User{user_chat_id}")
    topic_name = f"🟢 {display_name}"

    try:
        forum_topic = await context.bot.create_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            name=topic_name[:128]
        )
        topic_id = forum_topic.message_thread_id
        logger.info(f"Создан топик {topic_id} для пользователя {user_chat_id}")
    except Exception as e:
        logger.error(f"Ошибка создания топика: {e}")

    conn.execute(
        """
        INSERT INTO tickets (user_chat_id, username, first_name, status, created_at, updated_at, topic_id)
        VALUES (?, ?, ?, 'open', ?, ?, ?)
        """,
        (user_chat_id, username, first_name, now, now, topic_id),
    )
    conn.commit()
    ticket_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    if topic_id:
        username_display = f"@{username}" if username else "Не указан"
        user_info = (
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"🆔 ID: <code>{user_chat_id}</code>\n"
            f"👤 Имя: {first_name or 'Не указано'}\n"
            f"📱 Username: {username_display}\n"
            f"🎫 Тикет: #{ticket_id}"
        )
        try:
            # Первое сообщение (закрепляется автоматически) — служебное
            await context.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=topic_id,
                text="📩 Новое обращение"
            )
            # Второе — информация о пользователе (не пропадёт)
            await context.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=topic_id,
                text=user_info,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки информации о пользователе: {e}")

    return ticket_id, topic_id


def update_ticket_status(ticket_id: int, status: str):
    """Обновляет статус тикета"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, ticket_id),
    )
    conn.commit()


def touch_ticket(ticket_id: int):
    """Обновляет updated_at тикета"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
    conn.commit()


def update_ticket_user_info(ticket_id: int, username: str | None, first_name: str | None):
    """Обновляет username и first_name в тикете"""
    conn.execute(
        "UPDATE tickets SET username = ?, first_name = ? WHERE id = ?",
        (username, first_name, ticket_id),
    )
    conn.commit()


def get_ticket_status(ticket_id: int) -> str | None:
    """Возвращает статус тикета"""
    row = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row[0] if row else None


async def update_topic_status(context: ContextTypes.DEFAULT_TYPE, ticket_id: int, status: str):
    """Обновляет название топика при изменении статуса"""
    row = conn.execute(
        "SELECT topic_id, username, first_name, user_chat_id FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if not row or not row[0]:
        return

    topic_id, username, first_name, user_chat_id = row

    status_emoji = "🔴" if status == "closed" else "🟢"
    display_name = username if username else (first_name if first_name else f"User{user_chat_id}")
    topic_name = f"{status_emoji} {display_name}"

    try:
        await context.bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=topic_id,
            name=topic_name[:128]
        )
        logger.info(f"Обновлено название топика {topic_id} на '{topic_name}'")
    except Exception as e:
        logger.error(f"Ошибка обновления названия топика: {e}")


def get_ticket_by_support_message(support_message_id: int):
    row = conn.execute(
        "SELECT ticket_id FROM messages_mapping WHERE support_message_id = ?",
        (support_message_id,),
    ).fetchone()
    return row[0] if row else None


def save_mapping(user_chat_id, user_message_id, support_message_id, ticket_id):
    conn.execute(
        """
        INSERT OR REPLACE INTO messages_mapping (
            user_chat_id, user_message_id, support_message_id, ticket_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (user_chat_id, user_message_id, support_message_id, ticket_id),
    )
    conn.commit()


def find_user_by_support_message(support_message_id):
    return conn.execute(
        """
        SELECT user_chat_id, user_message_id, ticket_id
        FROM messages_mapping
        WHERE support_message_id = ?
        """,
        (support_message_id,),
    ).fetchone()


def get_all_open_tickets(limit: int = 50):
    return conn.execute(
        """
        SELECT id, user_chat_id, username, first_name, created_at, updated_at
        FROM tickets
        WHERE status = 'open'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_user_chat_id_by_ticket(ticket_id: int):
    row = conn.execute(
        "SELECT user_chat_id FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    return row[0] if row else None


# ----------------- Хендлеры пользователя -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_user_blocked(update.effective_user.id):
        return

    await update.message.reply_text(DEFAULT_GREETING)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_user_blocked(update.effective_user.id):
        return

    await update.message.reply_text(DEFAULT_HELP)


async def forward_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    user_chat_id = message.chat_id
    user_message_id = message.message_id

    if is_user_blocked(user_chat_id):
        return

    ticket_data = get_open_ticket(user_chat_id)

    if ticket_data is None:
        ticket_id, topic_id = await create_or_reopen_ticket(context, user_chat_id, user.username, user.first_name)
        await message.reply_text(
            "Мы получили ваше обращение и скоро ответим \U0001F4AC",
            entities=[
                MessageEntity(
                    type=MessageEntity.CUSTOM_EMOJI,
                    offset=len("Мы получили ваше обращение и скоро ответим "),
                    length=2,  # emoji занимает 2 символа (surrogate pair)
                    custom_emoji_id="5244583512878648726",
                ),
            ],
        )
    else:
        ticket_id, topic_id = ticket_data

    # Обновляем данные пользователя на случай если сменились
    update_ticket_user_info(ticket_id, user.username, user.first_name)

    username = f"@{user.username}" if user.username else "Не указан"
    header = f"💬 {user.first_name or 'Не указано'} ({username}):"

    send_kwargs = {}
    if topic_id:
        send_kwargs["message_thread_id"] = topic_id

    keyboard = [
        [InlineKeyboardButton("❌ Заблокировать/Разблокировать", callback_data=f"block_{user_chat_id}")]
    ]
    send_kwargs["reply_markup"] = InlineKeyboardMarkup(keyboard)

    try:
        sent_message = await copy_message(message, SUPPORT_CHAT_ID, caption=header, **send_kwargs)

        if sent_message:
            save_mapping(
                user_chat_id,
                user_message_id,
                sent_message.message_id,
                ticket_id,
            )
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
    ticket_id = None

    # Способ 1: ответ на конкретное сообщение — ищем по маппингу
    if message.reply_to_message:
        found = find_user_by_support_message(message.reply_to_message.message_id)
        if found:
            user_chat_id, _, ticket_id = found

    # Способ 2: сообщение в топике без reply — ищем тикет по topic_id
    if not user_chat_id and message.message_thread_id:
        ticket_data = get_ticket_by_topic_id(message.message_thread_id)
        if ticket_data:
            ticket_id, user_chat_id = ticket_data

    if not user_chat_id:
        return

    if is_user_blocked(user_chat_id):
        await message.reply_text("⛔️ Этот пользователь заблокирован. Он не получит сообщение.")
        return

    try:
        await copy_message(message, user_chat_id)
        if ticket_id:
            touch_ticket(ticket_id)
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
        "SELECT username, first_name FROM tickets WHERE user_chat_id = ? ORDER BY id DESC LIMIT 1",
        (target_user_id,),
    ).fetchone()
    if res:
        username, first_name = res
        username_str = f"@{username}" if username else "без юзернейма"
        user_info = f"{first_name or 'Пользователь'} ({username_str})"
    else:
        user_info = f"Пользователь {target_user_id}"

    if is_blocked_now:
        text = f"👨 {user_info}\n❗️ Пользователь заблокирован"
    else:
        text = f"👨 {user_info}\n❗️ Пользователь разблокирован"

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        message_thread_id=query.message.message_thread_id,
        text=text
    )


# --------- Команды для операторов в чате поддержки ---------
async def open_tickets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.chat_id != SUPPORT_CHAT_ID:
        return

    rows = get_all_open_tickets()

    if not rows:
        await message.reply_text("Открытых тикетов нет ✅")
        return

    header = "📂 Открытые тикеты:\n\n"
    blocks = []
    for ticket_id, user_chat_id, username, first_name, created_at, updated_at in rows:
        created_fmt = format_datetime(created_at)
        username_display = f"@{username}" if username else "Не указан"
        first_name_display = first_name or "Не указано"

        blocks.append(
            f"🎫 Тикет #{ticket_id}\n"
            f"👤 {first_name_display}\n"
            f"📱 {username_display}\n"
            f"🆔 ID: {user_chat_id}\n"
            f"📅 Создан: {created_fmt}"
        )

    # Разбиваем на сообщения по целым блокам
    current_text = header
    for block in blocks:
        candidate = current_text + block + "\n\n"
        if len(candidate) > MAX_MESSAGE_LENGTH:
            await message.reply_text(current_text.rstrip())
            current_text = block + "\n\n"
        else:
            current_text = candidate

    if current_text.strip():
        await message.reply_text(current_text.rstrip())


async def close_ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Команду /close нужно вызывать ответом на сообщение тикета.")
        return

    ticket_id = get_ticket_by_support_message(message.reply_to_message.message_id)
    if not ticket_id:
        await message.reply_text("Не удалось определить тикет для этого сообщения.")
        return

    if get_ticket_status(ticket_id) == "closed":
        await message.reply_text(f"Тикет #{ticket_id} уже закрыт.")
        return

    user_chat_id = get_user_chat_id_by_ticket(ticket_id)

    update_ticket_status(ticket_id, "closed")
    await update_topic_status(context, ticket_id, "closed")
    await message.reply_text(f"✅ Тикет #{ticket_id} закрыт.")

    if user_chat_id:
        try:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text="✅ Обращение завершено"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления пользователю {user_chat_id}: {e}")


async def reopen_ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Команду /reopen нужно вызывать ответом на сообщение тикета.")
        return

    ticket_id = get_ticket_by_support_message(message.reply_to_message.message_id)
    if not ticket_id:
        await message.reply_text("Не удалось определить тикет для этого сообщения.")
        return

    if get_ticket_status(ticket_id) == "open":
        await message.reply_text(f"Тикет #{ticket_id} уже открыт.")
        return

    update_ticket_status(ticket_id, "open")
    await update_topic_status(context, ticket_id, "open")
    await message.reply_text(f"♻️ Тикет #{ticket_id} снова открыт.")


async def ticket_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Команду /ticket нужно вызывать ответом на сообщение тикета.")
        return

    ticket_id = get_ticket_by_support_message(message.reply_to_message.message_id)
    if not ticket_id:
        await message.reply_text("Не удалось определить тикет для этого сообщения.")
        return

    row = conn.execute(
        "SELECT user_chat_id, status, created_at, updated_at FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if not row:
        await message.reply_text("Тикет не найден в базе.")
        return

    user_chat_id, status, created_at, updated_at = row
    created_fmt = format_datetime(created_at)
    updated_fmt = format_datetime(updated_at)

    is_blocked = is_user_blocked(user_chat_id)
    block_status = "ДА ⛔️" if is_blocked else "НЕТ ✅"

    text = (
        f"📄 Тикет #{ticket_id}\n"
        f"Пользователь: {user_chat_id}\n"
        f"Статус тикета: {status}\n"
        f"Заблокирован: {block_status}\n"
        f"Создан: {created_fmt}\n"
        f"Обновлён: {updated_fmt}"
    )
    await message.reply_text(text)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")


def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # команды для операторов
    application.add_handler(CommandHandler("close", close_ticket_cmd))
    application.add_handler(CommandHandler("reopen", reopen_ticket_cmd))
    application.add_handler(CommandHandler("ticket", ticket_info_cmd))
    application.add_handler(CommandHandler("open_tickets", open_tickets_cmd))

    # Обработчик нажатия на кнопку Block/Unblock
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
