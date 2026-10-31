import os
import io
import asyncio
import random
import html
import re
import urllib.parse
import json
import aiohttp
import tempfile
from datetime import datetime, timezone
from aiohttp import web
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, BusinessMessagesDeleted, BusinessConnection,
    BufferedInputFile, BotCommand, MenuButtonCommands,
    BotCommandScopeDefault, BotCommandScopeChat
)
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from simpleeval import simple_eval
from cachetools import TTLCache

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# КОНФИГ
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")
SUPERADMIN_ID = 6548121776

GIFT_SATELLITE_TOKEN = os.environ.get(
    "GIFT_SATELLITE_TOKEN",
    "f5fca5357f0721d8f95fed1a3874b60ae097ee7500eb2a2e41cbfc74cc201683"
)
GIFT_SATELLITE_BASE = "https://gift-satellite.dev/api"

NFTMATCH_API_KEY = os.environ.get("NFTMATCH_API_KEY", "")
NFTMATCH_BASE = "https://nftmatch.pro/api"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

MENU_PHOTO = "https://files.catbox.moe/bph5iz.webp"
CAPTION_LIMIT = 1024

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

BOT_ID: int = 8855259798   # константа — наш бот
BOT_USERNAME = "your_bot_username"

try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    connections_collection = db['connections']
    users_collection = db['users']
    archive_collection = db['chat_archive']
    mutes_collection = db['mutes']
except Exception as e:
    print(f"Ошибка БД: {e}")

muted_chats: dict[str, float | None] = {}
afk_cooldowns = TTLCache(maxsize=2000, ttl=300)
# active_amute: dict без TTL — сессия живёт бессрочно до явного .амут стоп
# Структура: { "conn_id_peer_id": {"text": str, "message_id": int, "until_ts": float|None} }
active_amute: dict[str, dict] = {}
action_dedup = TTLCache(maxsize=2000, ttl=4)
self_destruct_ids = TTLCache(maxsize=1000, ttl=30)
active_fakes: dict[str, asyncio.Task] = {}
copied_messages_cache = {}
# Блокировка для race condition в active_amute при конкурентных удалениях
_amute_lock: dict[str, asyncio.Lock] = {}
# message_id-ы уже обработанные специализированными хендлерами — catch-all их пропускает
_handled_msg_ids: TTLCache = TTLCache(maxsize=5000, ttl=30)


# ==========================================
# УТИЛИТЫ МУТОВ И АВТООТВЕТЧИКА
# ==========================================
def parse_mute_duration(text: str) -> tuple[int | None, str]:
    rest = text[4:].strip().lower()
    if not rest:
        return None, "навсегда"

    match = re.match(r"^(\d+)\s*(с|сек|s|м|мин|m|ч|час|h|д|дн|d)?", rest)
    if not match:
        return None, "навсегда"

    val = int(match.group(1))
    unit = match.group(2) or "м"

    if val <= 0:
        return None, "навсегда"

    if unit.startswith(("с", "s")):
        return val, f"{val} сек."
    if unit.startswith(("ч", "h")):
        return val * 3600, f"{val} ч."
    if unit.startswith(("д", "d")):
        return val * 86400, f"{val} дн."
    return val * 60, f"{val} мин."


def format_remaining_mute(until_ts: float | None) -> str:
    if until_ts is None:
        return "навсегда"
    rem = int(until_ts - datetime.now(timezone.utc).timestamp())
    if rem <= 0:
        return "истёк"
    if rem < 60:
        return f"{rem} сек."
    if rem < 3600:
        return f"{rem // 60} мин."
    if rem < 86400:
        return f"{rem // 3600} ч. {(rem % 3600) // 60} мин."
    return f"{rem // 86400} дн."


async def is_chat_muted(conn_id: str, peer_id: int) -> bool:
    mute_key = f"{conn_id}_{peer_id}"
    if mute_key not in muted_chats:
        return False

    until_ts = muted_chats[mute_key]
    if until_ts is not None and datetime.now(timezone.utc).timestamp() >= until_ts:
        muted_chats.pop(mute_key, None)
        with suppress(Exception):
            await mutes_collection.delete_one({"mute_key": mute_key})
        return False
    return True
    
async def _is_peer_muted_anywhere(peer_id: int) -> bool:
    """Проверяет, есть ли активный мут на этого пира в ЛЮБОМ нашем подключении."""
    suffix = f"_{peer_id}"
    now = datetime.now(timezone.utc).timestamp()
    for mute_key, until_ts in list(muted_chats.items()):
        if not mute_key.endswith(suffix):
            continue
        if until_ts is None or now < until_ts:
            return True
        else:
            muted_chats.pop(mute_key, None)
    return False

async def handle_owner_activity(owner_id: int, peer_id: int):
    afk_cooldowns[(owner_id, peer_id)] = True
    try:
        result = await users_collection.update_one(
            {"user_id": owner_id, "is_afk": True},
            {"$set": {"is_afk": False}}
        )
        if result.modified_count > 0:
            with suppress(Exception):
                await bot.send_message(
                    chat_id=owner_id,
                    text="🌅 <b>Автоответчик автоматически выключен</b>, так как вы написали сообщение в чате.",
                    parse_mode="HTML"
                )
    except Exception as e:
        print(f"handle_owner_activity error: {e}")


# ==========================================
# ПОДКЛЮЧЕНИЕ БИЗНЕС-БОТА
# ==========================================
async def is_bot_connected(user_id: int) -> bool:
    conn = await connections_collection.find_one({"user_id": user_id})
    return bool(conn)


def is_foreign_bot(message) -> bool:
    """
    Возвращает True если сообщение отправлено ботом, который НЕ является нашим ботом.
    Используется для блокировки обхода мута через чужих бизнес-ботов.
    Проверяем:
      1. from_user.is_bot — явный флаг бота
      2. via_bot — сообщение отправлено через inline-бота
      3. from_user.id == BOT_ID — пропускаем только себя
    """
    u = getattr(message, "from_user", None)
    if u is None:
        return False
    # Наш бот — не блокируем
    if u.id == BOT_ID:
        return False
    # Любой другой бот — блокируем
    if getattr(u, "is_bot", False):
        return True
    # Сообщение через inline-бота (via_bot) — блокируем
    via = getattr(message, "via_bot", None)
    if via and via.id != BOT_ID:
        return True
    return False


def get_connect_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть настройки Telegram", url="tg://settings/edit")],
        [InlineKeyboardButton(text="✅ Я подключил — проверить", callback_data="check_connection")]
    ])


def get_connect_text() -> str:
    return (
        "1. Настройки профиля - <a href=\"tg://settings/edit\">tg://settings/edit</a>\n\n"
        "2. «Автоматизация чатов»\n\n"
        f"3. В поиск <code>{BOT_USERNAME}</code> - добавить\n\n"
        "4. Дайте все права на сообщения"
    )


# ==========================================
# ПОКАЗ МЕНЮ
# ==========================================
async def show_menu(call: CallbackQuery, caption: str, kb, with_photo: bool = True):
    msg = call.message
    if len(caption) > CAPTION_LIMIT:
        with_photo = False
    try:
        if with_photo:
            if msg.photo:
                await msg.edit_caption(caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                with suppress(Exception):
                    await msg.delete()
                await bot.send_photo(
                    chat_id=call.from_user.id,
                    photo=MENU_PHOTO,
                    caption=caption,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
        else:
            if msg.photo:
                with suppress(Exception):
                    await msg.delete()
                await bot.send_message(
                    chat_id=call.from_user.id,
                    text=caption,
                    reply_markup=kb,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            else:
                with suppress(TelegramBadRequest):
                    await msg.edit_text(
                        caption, reply_markup=kb,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            print(f"show_menu TB: {e}")
    except Exception as e:
        print(f"show_menu error: {e}")


# ==========================================
# ФОРМАТИРОВАНИЕ ТЕКСТА (КАПС / ЖИРНЫЙ / КУРСИВ)
# ==========================================
def apply_format(text: str, mode: list) -> str:
    if not mode or not isinstance(mode, list):
        return text

    def _do(t: str) -> str:
        r = t.upper() if "caps" in mode else t
        r = html.escape(r)
        if "bold" in mode:
            r = f"<b>{r}</b>"
        if "italic" in mode:
            r = f"<i>{r}</i>"
        return r

    result = _do(text)
    if len(result) <= 4096:
        return result

    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(_do(text[:mid])) <= 4096:
            low = mid
        else:
            high = mid - 1
    return _do(text[:low])


def format_mode_label(mode: list) -> str:
    if not mode:
        return "ВЫКЛ"
    labels = {"caps": "КАПС", "bold": "жирный", "italic": "курсив"}
    return " + ".join(labels[m] for m in mode if m in labels)


# ==========================================
# GOOGLE DRIVE
# ==========================================
_drive_service = None
_folder_cache = TTLCache(maxsize=1000, ttl=86400)
drive_lock = asyncio.Lock()
ROOT_FOLDER_NAME = "TelegramArchiveBot"


def _init_drive():
    global _drive_service
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        print("⚠️ Google Drive: ключи не заданы в переменных окружения")
        return None
    try:
        creds = Credentials(
            token=None,
            refresh_token=GOOGLE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        creds.refresh(Request())
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        print("✅ Google Drive подключён")
        return _drive_service
    except Exception as e:
        print(f"❌ Google Drive init error: {e}")
        return None


def _drive():
    return _drive_service


def _find_folder(name: str, parent_id: str | None) -> str | None:
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    try:
        result = _drive().files().list(q=q, fields="files(id, name)", pageSize=1).execute()
        files = result.get("files", [])
        return files[0]["id"] if files else None
    except Exception as e:
        print(f"Drive find_folder error: {e}")
        return None


def _create_folder(name: str, parent_id: str | None) -> str:
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    folder = _drive().files().create(body=meta, fields="id").execute()
    return folder["id"]


def get_or_create_folder(path: str) -> str | None:
    if not _drive():
        return None
    if path in _folder_cache:
        return _folder_cache[path]
    parts = [p for p in path.split("/") if p]
    parent = None
    current_path = ""
    for part in parts:
        current_path = f"{current_path}/{part}" if current_path else part
        if current_path in _folder_cache:
            parent = _folder_cache[current_path]
            continue
        existing = _find_folder(part, parent)
        if existing:
            _folder_cache[current_path] = existing
            parent = existing
        else:
            parent = _create_folder(part, parent)
            _folder_cache[current_path] = parent
    return parent


async def async_get_or_create_folder(path: str):
    async with drive_lock:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, get_or_create_folder, path)


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", (name or "unknown"))[:80]


# ==========================================
# КЕШ СОБЕСЕДНИКОВ
# ==========================================
_peer_cache = TTLCache(maxsize=5000, ttl=86400)


async def get_peer_info(conn_id: str, peer_id: int) -> dict:
    key = (conn_id, peer_id)
    if key in _peer_cache:
        return _peer_cache[key]

    doc = await archive_collection.find_one(
        {"conn_id": conn_id, "chat_id": peer_id, "is_owner": False},
        sort=[("created_at", -1)]
    )
    if doc:
        info = {
            "first_name": doc.get("first_name") or "Без имени",
            "username": doc.get("username") or ""
        }
        _peer_cache[key] = info
        return info

    try:
        chat = await bot.get_chat(peer_id)
        info = {
            "first_name": chat.first_name or "Без имени",
            "username": chat.username or ""
        }
    except Exception:
        info = {"first_name": "Без имени", "username": ""}

    _peer_cache[key] = info
    return info


# ==========================================
# АРХИВАЦИЯ
# ==========================================
def _extract_media_info(message: Message):
    if message.photo:
        return "photo", message.photo[-1].file_id, "jpg", "image/jpeg", message.photo[-1].file_size
    if message.video:
        return "video", message.video.file_id, "mp4", "video/mp4", message.video.file_size
    if message.video_note:
        return "video_note", message.video_note.file_id, "mp4", "video/mp4", message.video_note.file_size
    if message.animation:
        return "animation", message.animation.file_id, "mp4", "video/mp4", message.animation.file_size
    if message.document:
        fname = message.document.file_name or "file"
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "bin"
        return "document", message.document.file_id, ext, message.document.mime_type or "application/octet-stream", message.document.file_size
    if message.audio:
        return "audio", message.audio.file_id, "mp3", message.audio.mime_type or "audio/mpeg", message.audio.file_size
    if message.voice:
        return "voice", message.voice.file_id, "ogg", "audio/ogg", message.voice.file_size
    if message.sticker:
        return "sticker", message.sticker.file_id, "webp", "image/webp", message.sticker.file_size
    return None, None, None, None, None


async def check_and_save_protected_media(message: Message, owner_id: int):
    reply = message.reply_to_message
    if not reply or not getattr(reply, "has_protected_content", False):
        return

    file_id, filename = None, "saved"
    if reply.photo:
        file_id, filename = reply.photo[-1].file_id, "saved.jpg"
    elif reply.video:
        file_id, filename = reply.video.file_id, "saved.mp4"
    elif reply.video_note:
        file_id, filename = reply.video_note.file_id, "saved_note.mp4"
    elif reply.animation:
        file_id, filename = reply.animation.file_id, "saved.mp4"
    elif reply.voice:
        file_id, filename = reply.voice.file_id, "saved_voice.ogg"
    elif reply.audio:
        file_id, filename = reply.audio.file_id, reply.audio.file_name or "saved_audio.mp3"
    elif reply.document:
        file_id, filename = reply.document.file_id, reply.document.file_name or "saved.bin"

    if not file_id:
        return

    try:
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        input_file = BufferedInputFile(buffer.read(), filename=filename)
        await bot.send_document(chat_id=owner_id, document=input_file, caption="🕵️ Сохранено из исчезающего сообщения")
    except Exception as e:
        print(f"save_replied_media error: {e}")


async def upload_media_bg(archive_id, file_id, media_type, ext, mime, peer_name_raw, peer_id, owner_name, file_size):
    tmp_path = None
    try:
        if file_size and file_size > 20 * 1024 * 1024:
            print(f"⚠️ Файл слишком большой ({file_size} байт), пропускаем для {peer_id}")
            return

        peer_name = _sanitize(peer_name_raw or f"peer_{peer_id}")
        owner_folder = _sanitize(owner_name)
        folder_path = f"{ROOT_FOLDER_NAME}/{owner_folder}/{peer_name} ({peer_id})/media"

        folder_id = await async_get_or_create_folder(folder_path)
        if not folder_id:
            return

        file_obj = await bot.get_file(file_id)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{ts}_{media_type}.{ext}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            tmp_path = tmp.name
        await bot.download_file(file_obj.file_path, destination=tmp_path)

        def _sync_upload():
            media = MediaFileUpload(tmp_path, mimetype=mime, resumable=True)
            meta = {"name": filename, "parents": [folder_id]}
            return _drive().files().create(body=meta, media_body=media, fields="id, webViewLink, webContentLink").execute()

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _sync_upload)
        if result:
            await archive_collection.update_one(
                {"_id": archive_id},
                {"$set": {"drive_link": result.get("webViewLink"), "drive_file_id": result.get("id")}}
            )
            print(f"✅ Медиа загружено: {media_type} для {peer_id}")
    except Exception as e:
        print(f"❌ upload_media_bg error: {e}")
    finally:
        if tmp_path:
            with suppress(Exception):
                os.remove(tmp_path)


async def save_message_quick(message: Message, conn_id: str, peer_id: int, owner_id: int, owner_name: str, is_owner: bool):
    text = message.text or message.caption or ""
    media_type, file_id, ext, mime, file_size = _extract_media_info(message)

    if is_owner:
        peer_info = await get_peer_info(conn_id, peer_id)
    else:
        peer_info = {
            "first_name": message.from_user.first_name or "Без имени",
            "username": message.from_user.username or ""
        }
        _peer_cache[(conn_id, peer_id)] = peer_info

    doc = {
        "conn_id": conn_id,
        "chat_id": peer_id,
        "owner_id": owner_id,
        "message_id": message.message_id,
        "user_id": message.from_user.id,
        "first_name": message.from_user.first_name or "Без имени",
        "username": message.from_user.username or "",
        "peer_first_name": peer_info["first_name"],
        "peer_username": peer_info["username"],
        "text": text,
        "is_owner": is_owner,
        "media_type": media_type,
        "media_file_id": file_id,
        "drive_link": None,
        "drive_file_id": None,
        "is_deleted": False,
        "is_edited": False,
        "old_text": None,
        "is_viewed": False,
        "created_at": datetime.now(timezone.utc),
    }

    try:
        result = await archive_collection.insert_one(doc)
        doc["_id"] = result.inserted_id
    except Exception as e:
        print(f"save_message_quick insert error: {e}")
        return None

    if media_type and file_id:
        # ИСПРАВЛЕНО: Теперь передаем имя СОБЕСЕДНИКА (peer_info), а не отправителя
        task = asyncio.create_task(upload_media_bg(
            doc["_id"], file_id, media_type, ext, mime,
            peer_info["first_name"], peer_id, owner_name, file_size
        ))
        task.add_done_callback(
            lambda t: print(f"❌ upload_media_bg error: {t.exception()}") if not t.cancelled() and t.exception() else None
        )

    return doc


# ==========================================
# УВЕДОМЛЕНИЯ
# ==========================================
async def notify_owner_about_deletion(
    owner_id, sender_name, sender_username, text,
    media_type=None, drive_link=None, deleted_at=None, file_id=None
):
    time_str = deleted_at.strftime("%d.%m %H:%M") if deleted_at else datetime.now().strftime("%d.%m %H:%M")
    uname = f" @{sender_username}" if sender_username else ""

    media_sent = False
    if file_id and media_type:
        caption = f"🗑 Удалено · {sender_name}{uname}"
        try:
            if media_type == "photo":
                await bot.send_photo(chat_id=owner_id, photo=file_id, caption=caption)
                media_sent = True
            elif media_type == "video":
                await bot.send_video(chat_id=owner_id, video=file_id, caption=caption)
                media_sent = True
            elif media_type == "video_note":
                await bot.send_video_note(chat_id=owner_id, video_note=file_id)
                media_sent = True
            elif media_type == "animation":
                await bot.send_animation(chat_id=owner_id, animation=file_id, caption=caption)
                media_sent = True
            elif media_type == "voice":
                await bot.send_voice(chat_id=owner_id, voice=file_id)
                media_sent = True
            elif media_type == "audio":
                await bot.send_audio(chat_id=owner_id, audio=file_id, caption=caption)
                media_sent = True
            elif media_type == "document":
                await bot.send_document(chat_id=owner_id, document=file_id, caption=caption)
                media_sent = True
            elif media_type == "sticker":
                await bot.send_sticker(chat_id=owner_id, sticker=file_id)
                media_sent = True
        except Exception as e:
            print(f"notify send_media error: {e}")

    parts = ["🗑 <b>Удалено сообщение</b>"]
    parts.append(f"👤 От: <b>{html.escape(sender_name)}</b>{html.escape(uname)}")
    parts.append(f"🕐 {time_str}")

    if text:
        preview = text[:500] + ("..." if len(text) > 500 else "")
        parts.append("")
        parts.append(f"💬 {html.escape(preview)}")

    if media_type:
        if media_sent:
            parts.append(f"📎 [{media_type}] — отправлено выше")
        elif drive_link:
            parts.append(f'📎 <a href="{drive_link}">[{media_type} · Drive]</a>')
        else:
            parts.append(f"📎 [{media_type}] — нет доступа к файлу")

    with suppress(Exception):
        await bot.send_message(
            chat_id=owner_id,
            text="\n".join(parts),
            parse_mode="HTML",
            disable_web_page_preview=True
        )


async def notify_owner_about_edit(owner_id, sender_name, sender_username, old_text, new_text, edited_at=None):
    time_str = edited_at.strftime("%d.%m %H:%M") if edited_at else datetime.now().strftime("%d.%m %H:%M")
    uname = f" @{sender_username}" if sender_username else ""
    old_preview = (old_text or "")[:400] + ("..." if len(old_text or "") > 400 else "")
    new_preview = (new_text or "")[:400] + ("..." if len(new_text or "") > 400 else "")

    body = (
        f"✏️ <b>Изменено сообщение</b>\n"
        f"👤 От: <b>{html.escape(sender_name)}</b>{html.escape(uname)}\n"
        f"🕐 {time_str}\n\n"
        f"📝 <b>Было:</b>\n<i>{html.escape(old_preview or '[пусто]')}</i>\n\n"
        f"📝 <b>Стало:</b>\n{html.escape(new_preview or '[пусто]')}"
    )
    with suppress(Exception):
        await bot.send_message(chat_id=owner_id, text=body, parse_mode="HTML", disable_web_page_preview=True)


# ==========================================
# GIFT SATELLITE
# ==========================================
async def fetch_gift_satellite(endpoint: str, params: dict = None, retry: int = 2):
    if not GIFT_SATELLITE_TOKEN:
        return None
    headers = {"Authorization": f"Token {GIFT_SATELLITE_TOKEN}"}
    url = f"{GIFT_SATELLITE_BASE}{endpoint}"
    for attempt in range(retry + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429 and attempt < retry:
                        await asyncio.sleep(1.5)
                        continue
                    return None
        except Exception as e:
            print(f"GiftSatellite error: {e}")
            if attempt < retry:
                await asyncio.sleep(1.0)
    return None


# ==========================================
# NFTMATCH API
# ==========================================
async def fetch_nftmatch(endpoint: str, params: dict = None, retry: int = 2):
    """GET-запрос к nftmatch.pro. Возвращает dict/list или None."""
    if not NFTMATCH_API_KEY:
        return None
    headers = {"Authorization": NFTMATCH_API_KEY}
    url = f"{NFTMATCH_BASE}{endpoint}"
    for attempt in range(retry + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429 and attempt < retry:
                        await asyncio.sleep(1.5)
                        continue
                    return None
        except Exception as e:
            print(f"NFTMatch error: {e}")
            if attempt < retry:
                await asyncio.sleep(1.0)
    return None




async def get_gift_card_data(slug: str) -> dict:
    """
    Собирает данные по подарку из Gift Satellite + nftmatch.pro.
    Возвращает dict с ключами:
      slug, collection_name, model, backdrop, symbol, number,
      floor_ton, collection_floor_ton,
      mono_score (float 0-100 или None),
      model_offers (list из Gift Satellite)
    """
    result: dict = {"slug": slug}

    # --- Gift Satellite: базовые данные и рыночные цены ---
    gift_info = await fetch_gift_satellite(f"/gift/by-slug/{slug}")
    if gift_info:
        result["collection_name"] = gift_info.get("collectionName", "")
        result["model"] = gift_info.get("modelName", "")
        result["backdrop"] = gift_info.get("backdropName", "")
        result["symbol"] = gift_info.get("symbolName", "")
        result["number"] = gift_info.get("number")

    collection = result.get("collection_name", "")
    model_name = result.get("model", "")

    async def _noop():
        return None

    # URL-encode имени коллекции (пробелы → %20), иначе aiohttp может отрезать хвост
    collection_encoded = urllib.parse.quote(collection, safe="")

    # Загружаем офферы параллельно с nftmatch
    gs_task = asyncio.create_task(
        fetch_gift_satellite(f"/search/tg/{collection_encoded}") if collection else _noop()
    )

    # --- nftmatch.pro: монохромность модели через список моделей коллекции ---
    nm_task = asyncio.create_task(
        fetch_nftmatch(f"/ListGifts/{collection_encoded}/AllModelNames") if collection else _noop()
    )

    offers_raw, nm_models = await asyncio.gather(gs_task, nm_task, return_exceptions=True)

    # Логируем ошибки gather
    if isinstance(offers_raw, Exception):
        print(f"[get_gift_card_data] offers error: {offers_raw}")
        offers_raw = None
    if isinstance(nm_models, Exception):
        print(f"[get_gift_card_data] nftmatch error: {nm_models}")
        nm_models = None

    # Gift Satellite офферы
    if isinstance(offers_raw, list):
        model_offers = [o for o in offers_raw if o.get("modelName") == model_name]
        result["model_offers"] = model_offers
        model_prices = [o["normalizedPrice"] for o in model_offers if o.get("normalizedPrice")]
        if model_prices:
            result["floor_ton"] = min(model_prices)
        all_prices = [o["normalizedPrice"] for o in offers_raw if o.get("normalizedPrice")]
        if all_prices:
            result["collection_floor_ton"] = min(all_prices)
    else:
        print(f"[get_gift_card_data] offers_raw not list: {type(offers_raw)} slug={slug}")

    # nftmatch: монохромность
    if isinstance(nm_models, list) and model_name:
        for item in nm_models:
            if isinstance(item, dict) and item.get("nameModel", "").lower() == model_name.lower():
                # isMonohrome — bool, но нам нужен % → берём из MatchV2ColorProfile если есть
                result["is_monochrome"] = item.get("isMonohrome", False)
                result["floor_ton_nm"] = item.get("floorPrice")  # floor из nftmatch (TON)
                break

    # Уточняем монохромность через MatchV2ColorProfile (даёт cubes с долями цветов)
    if collection and model_name and NFTMATCH_API_KEY:
        profile = await fetch_nftmatch(
            "/MonoCoof/MatchV2ColorProfile",
            params={"nameGift": collection, "nameModel": model_name}
        )
        if isinstance(profile, dict):
            cubes = profile.get("cubes") or []
            if cubes:
                # Монохромность = максимальная доля одного цвета среди всех кубов
                weights = [c.get("weight", 0) for c in cubes if isinstance(c, dict)]
                if weights:
                    total = sum(weights)
                    if total > 0:
                        result["mono_score"] = round(max(weights) / total * 100)

    return result


def format_gift_card(data: dict, exchange_cache: dict | None = None) -> str:
    """
    Форматирует карточку подарка в новом стиле:

    🎁 Plush Pepe #274 (кликабельная ссылка)
    ━━━━━━━━━━━━━━━━━━━━
    ⚫️ Монохромность: 87%

    💎 Рыночные данные:
    ├ Floor (модель): 12.50 TON | 65.20 USDT | 5 980 RUB | 4 346 ⭐
    └ Floor (коллекция): 8.10 TON | …
    """
    if not data or not data.get("collection_name"):
        return "❌ Подарок не найден."

    slug = data.get("slug", "")
    collection = data.get("collection_name", "Подарок")
    number = data.get("number", "?")
    model = data.get("model", "")
    backdrop = data.get("backdrop", "")
    symbol = data.get("symbol", "")

    cache = exchange_cache or EXCHANGE_CACHE

    lines: list[str] = []

    # Заголовок — кликабельная ссылка
    if slug:
        link = f'<a href="https://t.me/nft/{slug}">{html.escape(collection)} #{number}</a>'
    else:
        link = f"<b>{html.escape(collection)} #{number}</b>"
    lines.append(f"🎁 {link}")

    # Атрибуты (Модель / Фон / Символ)
    attrs = []
    if model:    attrs.append(f"Модель: <b>{html.escape(model)}</b>")
    if backdrop: attrs.append(f"Фон: <b>{html.escape(backdrop)}</b>")
    if symbol:   attrs.append(f"Символ: <b>{html.escape(symbol)}</b>")
    if attrs:
        lines.append(" · ".join(attrs))

    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Монохромность
    mono_score = data.get("mono_score")
    if mono_score is not None:
        bar_filled = round(mono_score / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines.append(f"⚫️ <b>Монохромность: {mono_score}%</b>")
        lines.append(f"<code>[{bar}]</code>")
    elif data.get("is_monochrome"):
        lines.append("⚫️ <b>Монохромная модель</b>")

    lines.append("")
    lines.append("💎 <b>Рыночные данные:</b>")

    def _price_line(floor_ton: float, prefix: str) -> str:
        parts = [f"{floor_ton:.2f} TON"]
        r_ton = cache.get("TON")
        if r_ton:
            usdt = floor_ton * r_ton
            parts.append(f"{usdt:.2f} USDT")
            r_rub = cache.get("RUB")
            if r_rub:
                rub = usdt / r_rub
                parts.append(f"{rub:,.0f} RUB".replace(",", " "))
            r_stars = cache.get("STARS")
            if r_stars:
                stars = usdt / r_stars
                parts.append(f"{int(round(stars))} ⭐")
        return f"{prefix} " + " | ".join(parts)

    floor_ton = data.get("floor_ton") or data.get("floor_ton_nm")
    coll_floor = data.get("collection_floor_ton")

    has_prices = floor_ton is not None or coll_floor is not None

    if floor_ton is not None and coll_floor is not None:
        lines.append(_price_line(floor_ton, "├ Floor (модель):"))
        lines.append(_price_line(coll_floor, "└ Floor (коллекция):"))
    elif floor_ton is not None:
        lines.append(_price_line(floor_ton, "└ Floor (модель):"))
    elif coll_floor is not None:
        lines.append(_price_line(coll_floor, "└ Floor (коллекция):"))
    else:
        lines.append("<i>Рыночные данные недоступны</i>")

    return "\n".join(lines)


# ==========================================
# КУРСЫ И МАТЕМАТИКА
# ==========================================
EXCHANGE_CACHE = {}
STARS_USD_RATE = 0.015

CURRENCY_ALIASES = {
    "usd": "USDT", "usdt": "USDT", "доллар": "USDT", "доллары": "USDT", "долларов": "USDT", "доллара": "USDT",
    "долл": "USDT", "долл.": "USDT", "бакс": "USDT", "баксы": "USDT", "баксов": "USDT", "бакса": "USDT",
    "бачей": "USDT", "бач": "USDT", "бач.": "USDT", "баксик": "USDT", "баксиков": "USDT", "баксика": "USDT",
    "зеленый": "USDT", "зелёный": "USDT", "зеленых": "USDT", "зелёных": "USDT", "зелень": "USDT", "зелени": "USDT",
    "юсд": "USDT", "юсдт": "USDT", "усд": "USDT", "усдт": "USDT", "тетер": "USDT", "тетеры": "USDT", 
    "тетеров": "USDT", "тетера": "USDT", "тезер": "USDT", "тезеры": "USDT", "тезеров": "USDT", "тезера": "USDT",
    "юста": "USDT", "юст": "USDT", "$": "USDT", "💵": "USDT", "💰": "USDT", "💲": "USDT",
    "rub": "RUB", "руб": "RUB", "руб.": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "рубля": "RUB", "рублю": "RUB",
    "рубас": "RUB", "рубасов": "RUB", "рубаса": "RUB", "рубасы": "RUB", "рубчик": "RUB", "рубчики": "RUB", "рубчиков": "RUB",
    "деревянный": "RUB", "деревянных": "RUB", "деревянные": "RUB", "р": "RUB", "р.": "RUB", "₽": "RUB",
    "eur": "EUR", "евро": "EUR", "еврик": "EUR", "еврики": "EUR", "евриков": "EUR", "еврика": "EUR", "евра": "EUR", "евров": "EUR", "€": "EUR",
    "cny": "CNY", "юань": "CNY", "юани": "CNY", "юаней": "CNY", "юаня": "CNY", "юан": "CNY", "yuan": "CNY", 
    "юанчики": "CNY", "женьминьби": "CNY", "жэньминьби": "CNY", "¥": "CNY",
    "btc": "BTC", "биткоин": "BTC", "биткоины": "BTC", "биткоинов": "BTC", "биткоина": "BTC", "биткойн": "BTC", 
    "биткойны": "BTC", "биткойнов": "BTC", "биток": "BTC", "битки": "BTC", "битков": "BTC", "битка": "BTC", 
    "бит": "BTC", "биты": "BTC", "битов": "BTC", "₿": "BTC",
    "ton": "TON", "тон": "TON", "тона": "TON", "тонов": "TON", "тоны": "TON", "тоник": "TON", "тоника": "TON", 
    "тоников": "TON", "тонику": "TON", "грам": "TON", "грама": "TON", "грамов": "TON", "граммы": "TON", 
    "грамм": "TON", "gram": "TON", "grams": "TON", "павлуша": "TON", "дуров": "TON", "💎": "TON",
    "stars": "STARS", "star": "STARS", "звезда": "STARS", "звёзда": "STARS", "звезды": "STARS", "звёзды": "STARS",
    "звезд": "STARS", "звёзд": "STARS", "звезде": "STARS", "звёзде": "STARS", "звездочка": "STARS", "звёздочка": "STARS",
    "звездочки": "STARS", "звёздочки": "STARS", "звездочек": "STARS", "звёздочек": "STARS", "зв": "STARS", "зв.": "STARS",
    "звездулька": "STARS", "звездульки": "STARS", "starчик": "STARS", "стар": "STARS", "стары": "STARS", "старов": "STARS",
    "⭐": "STARS", "🌟": "STARS", "✨": "STARS",
    "eth": "ETH", "эфир": "ETH", "эфира": "ETH", "эфиров": "ETH", "эфиры": "ETH", "эфириум": "ETH", "эфирка": "ETH", 
    "эфирки": "ETH", "эфирок": "ETH", "эфирчик": "ETH", "эфирчика": "ETH", "Ξ": "ETH",
    "kzt": "KZT", "тенге": "KZT", "теньге": "KZT", "теньга": "KZT", "тг": "KZT", "тг.": "KZT", "₸": "KZT",
    "uah": "UAH", "гривна": "UAH", "гривны": "UAH", "гривен": "UAH", "гривне": "UAH", "гривня": "UAH", "гривню": "UAH",
    "грн": "UAH", "грн.": "UAH", "₴": "UAH",
    "gbp": "GBP", "фунт": "GBP", "фунты": "GBP", "фунтов": "GBP", "фунта": "GBP", "стерлинг": "GBP", "стерлинги": "GBP", 
    "стерлингов": "GBP", "£": "GBP",
    "jpy": "JPY", "иена": "JPY", "иены": "JPY", "иен": "JPY", "йена": "JPY", "йены": "JPY", "йен": "JPY",
}

ALL_CURRENCIES = ["RUB", "USDT", "EUR", "CNY", "KZT", "UAH", "GBP", "JPY", "BTC", "ETH", "TON", "STARS"]
_CURRENCY_ALT = "|".join(sorted([re.escape(a) for a in CURRENCY_ALIASES.keys()], key=len, reverse=True))

WORD_NUMBERS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900, "тысяча": 1000,
}

_NUM_WORDS = "|".join(sorted(WORD_NUMBERS.keys(), key=len, reverse=True))
_SINGLE_NUM = rf"(?:\d+[.,]?\d*|{_NUM_WORDS})"
_NUM = rf"{_SINGLE_NUM}(?:\s+{_SINGLE_NUM})*"
_OPS = r"[+\-*/xх^]"
_WORDS = r"(?:плюс|минус|умножить на|разделить на|поделить на|умножить|разделить|поделить|сложить|вычесть|делить|степень|в степени|х)"

AUTO_MATH_PATTERN = re.compile(rf"^\s*{_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})+\s*$", re.IGNORECASE)
AUTO_MATH_CURRENCY_PATTERN = re.compile(rf"^\s*({_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})*)\s{{0,3}}({_CURRENCY_ALT})\s*$", re.IGNORECASE)

MIXED_CURRENCY_FULL = re.compile(
    rf"^\s*{_SINGLE_NUM}\s*(?:{_CURRENCY_ALT})(?:\s*[+\-]\s*{_SINGLE_NUM}\s*(?:{_CURRENCY_ALT}))+\s*$",
    re.IGNORECASE
)
MIXED_CURRENCY_TERM = re.compile(rf"({_SINGLE_NUM})\s*({_CURRENCY_ALT})", re.IGNORECASE)

OPERATORS = {
    "умножить на": "*", "разделить на": "/", "поделить на": "/", "в степени": "**", "степень": "**",
    "плюс": "+", "сложить": "+", "минус": "-", "вычесть": "-",
    "умножить": "*", "разделить": "/", "делить": "/", "поделить": "/", "х": "*", "x": "*", "^": "**",
}


def parse_math_expression(text: str) -> str:
    text = text.lower().strip()
    for word, op in sorted(OPERATORS.items(), key=lambda x: -len(x[0])):
        if word in ("^",):
            text = text.replace("^", " ** ")
        else:
            text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", f" {op} ", text)

    tokens = re.findall(r"\*\*|[+\-*/()]|\d+(?:\.\d+)?|[а-яёa-z]+", text)
    result = []
    current_num = None

    for tok in tokens:
        if tok in WORD_NUMBERS:
            val = WORD_NUMBERS[tok]
            current_num = (current_num + val) if current_num is not None else val
        else:
            if current_num is not None:
                result.append(str(current_num))
                current_num = None
            if re.fullmatch(r"\*\*|[+\-*/()]|\d+(?:\.\d+)?", tok):
                result.append(tok)

    if current_num is not None:
        result.append(str(current_num))

    return " ".join(result).strip()


def normalize_math_input(text: str) -> str:
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return re.sub(r"\s{1,}", " ", text.strip())


def format_math_expression(expr: str) -> str:
    expr = re.sub(r"\s+", " ", expr).strip()
    expr = re.sub(r"\s*(\*\*|[+\-*/])\s*", r" \1 ", expr)
    return re.sub(r"\s+", " ", expr).strip()


async def fetch_json(url: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return None


async def force_update_all_rates():
    try:
        fiat_data = await fetch_json("https://open.er-api.com/v6/latest/USD")
        if fiat_data and "rates" in fiat_data:
            rates = fiat_data["rates"]
            for cur in ["RUB", "EUR", "CNY", "UAH", "KZT", "GBP", "JPY"]:
                if rates.get(cur):
                    EXCHANGE_CACHE[cur] = 1.0 / rates[cur]

        for sym, code in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]:
            price = None
            for url in [
                f"https://api.binance.com/api/v3/ticker/price?symbol={sym}",
                f"https://api.mexc.com/api/v3/ticker/price?symbol={sym}",
            ]:
                data = await fetch_json(url)
                if data and "price" in data:
                    try:
                        price = float(data["price"])
                        break
                    except (ValueError, TypeError):
                        pass
            if price is None:
                cg_id = {"BTC": "bitcoin", "ETH": "ethereum"}.get(code)
                if cg_id:
                    cg_data = await fetch_json(
                        f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
                    )
                    if cg_data and cg_id in cg_data:
                        price = float(cg_data[cg_id]["usd"])
            if price is not None:
                EXCHANGE_CACHE[code] = price

        ton_price = None
        for url in [
            "https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT",
            "https://api.mexc.com/api/v3/ticker/price?symbol=TONUSDT",
            "https://api.mexc.com/api/v3/ticker/price?symbol=GRAMUSDT",
        ]:
            data = await fetch_json(url)
            if data and "price" in data:
                try:
                    ton_price = float(data["price"])
                    break
                except (ValueError, TypeError):
                    pass

        if ton_price is None:
            cg_data = await fetch_json(
                "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"
            )
            if cg_data and "the-open-network" in cg_data:
                ton_price = float(cg_data["the-open-network"]["usd"])

        if ton_price is not None:
            EXCHANGE_CACHE["TON"] = ton_price

        EXCHANGE_CACHE["USD"] = 1.0
        EXCHANGE_CACHE["USDT"] = 1.0
        EXCHANGE_CACHE["STARS"] = STARS_USD_RATE
    except Exception as e:
        print(f"Ошибка обновления курсов: {e}")


async def update_rates_loop():
    while True:
        try:
            await force_update_all_rates()
        except Exception as e:
            print(f"❌ update_rates_loop error: {e}")
        await asyncio.sleep(300)


async def convert_currency(amount: float, from_cur: str, to_cur: str):
    if from_cur == to_cur:
        return amount
    if from_cur not in EXCHANGE_CACHE or to_cur not in EXCHANGE_CACHE:
        await force_update_all_rates()
    r_from = EXCHANGE_CACHE.get(from_cur)
    r_to = EXCHANGE_CACHE.get(to_cur)
    if r_from is None or r_to is None:
        return None
    return (amount * r_from) / r_to


EMOJI_MAP = {
    "RUB": "🇷🇺", "USDT": "💵", "EUR": "🇪🇺", "CNY": "🇨🇳", "KZT": "🇰🇿",
    "UAH": "🇺🇦", "GBP": "🇬🇧", "JPY": "🇯🇵", "BTC": "₿", "ETH": "Ξ",
    "TON": "💎", "STARS": "⭐",
}


def _format_converted(target: str, result: float) -> str:
    if target in ("BTC", "ETH", "TON"):
        formatted = f"{result:.6f}".rstrip("0").rstrip(".")
    elif target == "STARS":
        formatted = f"{result:.2f}".rstrip("0").rstrip(".")
    elif target in ("RUB", "KZT", "UAH", "JPY"):
        formatted = f"{result:.2f}"
    else:
        formatted = f"{result:.3f}".rstrip("0").rstrip(".")
    return formatted or "0"


async def send_currency_conversion(message: Message, amount: float, from_cur: str, original_expr: str = None, owner_id: int = None):
    conn_id = getattr(message, "business_connection_id", None)
    if not owner_id:
        owner_id = message.from_user.id

    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    display_currencies = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])

    if from_cur == "USD":
        from_cur = "USDT"

    if original_expr:
        header = f"🔄 Конвертация {original_expr} {from_cur}"
    else:
        amount_str = f"{amount:.6f}".rstrip("0").rstrip(".")
        header = f"🔄 Конвертация {amount_str} {from_cur}"

    lines = [f"<b>{header}</b>", ""]
    shown = set()
    results_found = 0

    for target in display_currencies:
        if target == "USD":
            target = "USDT"
        if target in shown or target == from_cur:
            continue
        shown.add(target)

        result = await convert_currency(amount, from_cur, target)
        if result is None:
            continue
        lines.append(f"{target} {EMOJI_MAP.get(target, '•')}: <code>{_format_converted(target, result)}</code>")
        results_found += 1

    if results_found == 0:
        lines.append("<i>Нет доступных валют для конвертации</i>")

    with suppress(Exception):
        await bot.send_message(
            chat_id=message.chat.id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_to_message_id=message.message_id,
            business_connection_id=conn_id
        )


def parse_mixed_currency(text: str):
    text = text.strip()
    parsed = []
    pos = 0
    for m in MIXED_CURRENCY_TERM.finditer(text):
        between = text[pos:m.start()].strip()
        if parsed:
            if between not in ("+", "-"):
                return None
            parsed.append(between)
        elif between:
            return None

        num_str = m.group(1)
        cur_str = m.group(2).lower()
        code = CURRENCY_ALIASES.get(cur_str)
        if not code:
            return None

        try:
            if re.fullmatch(r"\d+[.,]?\d*", num_str):
                num = float(num_str.replace(",", "."))
            else:
                parsed_expr = parse_math_expression(num_str)
                if not parsed_expr:
                    return None
                num = float(simple_eval(parsed_expr))
        except Exception:
            return None

        parsed.append({"num": num, "cur": code})
        pos = m.end()

    if text[pos:].strip():
        return None
    if len(parsed) < 3:
        return None
    return parsed


async def eval_mixed_currency(parsed):
    if not EXCHANGE_CACHE:
        await force_update_all_rates()

    usdt_values = []
    for item in parsed:
        if isinstance(item, dict):
            rate = EXCHANGE_CACHE.get(item["cur"])
            if rate is None:
                await force_update_all_rates()
                rate = EXCHANGE_CACHE.get(item["cur"])
                if rate is None:
                    return None
            usdt_values.append(item["num"] * rate)
        else:
            usdt_values.append(item)

    result = usdt_values[0]
    i = 1
    while i < len(usdt_values) - 1:
        op = usdt_values[i]
        val = usdt_values[i + 1]
        if op == "+":
            result += val
        elif op == "-":
            result -= val
        i += 2
    return result


async def send_mixed_currency_conversion(message, parsed, result_usdt, owner_id):
    conn_id = getattr(message, "business_connection_id", None)
    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    display_currencies = list(owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"]))

    expr_parts = []
    for item in parsed:
        if isinstance(item, dict):
            expr_parts.append(f"{item['num']:g} {item['cur']}")
        else:
            expr_parts.append(item)
    expr = " ".join(expr_parts)

    lines = [f"<b>🔄 {expr}</b>", ""]

    if "USDT" not in display_currencies:
        display_currencies.append("USDT")

    shown = set()
    for target in display_currencies:
        if target == "USD":
            target = "USDT"
        if target in shown:
            continue
        shown.add(target)
        rate = EXCHANGE_CACHE.get(target)
        if rate is None:
            continue
        result = result_usdt / rate
        lines.append(f"{target} {EMOJI_MAP.get(target, '•')}: <code>{_format_converted(target, result)}</code>")

    with suppress(Exception):
        await bot.send_message(
            chat_id=message.chat.id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_to_message_id=message.message_id,
            business_connection_id=conn_id
        )


# ==========================================
# СОСТОЯНИЯ И AFK
# ==========================================
class UserStates(StatesGroup):
    waiting_for_afk_text = State()
    waiting_for_afk_time = State()


def check_auto_afk(start_h: int, end_h: int, tz_offset: int = 3) -> bool:
    now = datetime.now(timezone.utc)
    local_hour = (now.hour + tz_offset) % 24
    if start_h < end_h:
        return start_h <= local_hour < end_h
    return local_hour >= start_h or local_hour < end_h


# ==========================================
# ВЕБ-СЕРВЕР И STARTUP
# ==========================================
_web_runner: web.AppRunner | None = None


async def dummy_handler(request):
    return web.Response(text="Multi-bot is running!")


async def start_web_server() -> web.AppRunner:
    global _web_runner
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    _web_runner = runner
    print(f"✅ Веб-сервер запущен на порту {port}")
    return runner


async def on_startup():
    global BOT_USERNAME, BOT_ID

    with suppress(Exception):
        await archive_collection.create_index([("owner_id", 1), ("conn_id", 1), ("chat_id", 1), ("created_at", -1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("message_id", 1)])
        await archive_collection.create_index([("user_id", 1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("is_owner", 1), ("created_at", -1)])
    with suppress(Exception):
        await mutes_collection.create_index([("mute_key", 1)], unique=True)

    try:
        now_ts = datetime.now(timezone.utc).timestamp()
        stored_mutes = await mutes_collection.find({}).to_list(length=None)
        for m in stored_mutes:
            until_ts = m.get("until_ts")
            if until_ts is not None and now_ts >= until_ts:
                await mutes_collection.delete_one({"_id": m["_id"]})
            else:
                muted_chats[m["mute_key"]] = until_ts
        print(f"✅ Загружено активных мутов из базы: {len(muted_chats)}")
    except Exception as e:
        print(f"Ошибка загрузки мутов: {e}")

    asyncio.create_task(update_rates_loop())
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _init_drive)

    try:
        me = await bot.get_me()
        global BOT_USERNAME
        BOT_USERNAME = me.username or "your_bot_username"
        print(f"✅ Бот: @{BOT_USERNAME} (ID: {BOT_ID})")
    except Exception as e:
        print(f"❌ Не удалось получить данные бота: {e}")

    with suppress(Exception):
        me = await fetch_gift_satellite("/user/me")
        if me:
            print(f"✅ Gift Satellite: {me.get('username')}")

    with suppress(Exception):
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Главное меню")], scope=BotCommandScopeDefault())
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="admin", description="👑 Админ-панель")
        ], scope=BotCommandScopeChat(chat_id=SUPERADMIN_ID))
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


dp.startup.register(on_startup)


# ==========================================
# ГЛАВНОЕ МЕНЮ
# ==========================================
async def get_user_main_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    is_afk = user_data.get("is_afk", False)
    format_mode = user_data.get("format_mode", [])
    if not isinstance(format_mode, list):
        format_mode = []

    if is_afk:
        afk_btn = InlineKeyboardButton(text="🟢 Автоответчик — ВКЛЮЧЕН", callback_data="afk_menu")
    else:
        afk_btn = InlineKeyboardButton(text="🔴 Автоответчик — ВЫКЛЮЧЕН", callback_data="afk_menu")

    fmt_label = format_mode_label(format_mode)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds")],
        [
            InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes"),
            InlineKeyboardButton(text="🗑 Удалённые и изменения", callback_data="deleted_menu"),
        ],
        [
            InlineKeyboardButton(text="👤 Мой статус", callback_data="user_status"),
            InlineKeyboardButton(text="💱 Валюты", callback_data="currency_settings"),
        ],
        [InlineKeyboardButton(text=f"✍️ Форматирование: {fmt_label}", callback_data="format_menu")],
        [afk_btn],
    ])


@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": message.from_user.id},
            {"$set": {"user_id": message.from_user.id}},
            upsert=True
        )

    if message.from_user.id != SUPERADMIN_ID and not await is_bot_connected(message.from_user.id):
        try:
            await message.answer_photo(photo=MENU_PHOTO, caption=get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        except Exception:
            await message.answer(get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        return

    kb = await get_user_main_kb(message.from_user.id)
    text = "👋 <b>Твой личный бот-секретарь.</b>\nУправляй настройками ниже:"
    try:
        await message.answer_photo(photo=MENU_PHOTO, caption=text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "check_connection")
async def check_connection_handler(call: CallbackQuery):
    conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    if not conns:
        await call.answer(
            "❌ Бот ещё не подключён.\n\n"
            "Открой настройки → Автоматизация чатов → Добавить бота → "
            f"введи @{BOT_USERNAME}",
            show_alert=True
        )
        return
    await call.answer("✅ Подключено!", show_alert=True)
    kb = await get_user_main_kb(call.from_user.id)
    await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return
    kb = await get_user_main_kb(call.from_user.id)
    await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)


# ==========================================
# ФОРМАТИРОВАНИЕ (меню)
# ==========================================
async def _render_format_menu(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if not isinstance(mode, list):
        mode = []

    builder = InlineKeyboardBuilder()
    for key, label in [("caps", "🔠 КАПС"), ("bold", "🅱️ Жирный"), ("italic", "🇮 Курсив")]:
        prefix = "✅ " if key in mode else ""
        builder.button(text=f"{prefix}{label}", callback_data=f"format_toggle|{key}")
    builder.button(text="👁 Предпросмотр", callback_data="format_preview")
    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(2, 1, 1)

    text = (
        "✍️ <b>Форматирование сообщений</b>\n\n"
        "Выбери один или несколько стилей — они будут применяться ко всем твоим "
        "текстовым сообщениям (не командам).\n\n"
        f"<b>Сейчас:</b> {format_mode_label(mode)}"
    )
    await show_menu(call, text, builder.as_markup(), with_photo=True)


@dp.callback_query(F.data == "format_menu")
async def format_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return
    await _render_format_menu(call)


@dp.callback_query(F.data.startswith("format_toggle|"))
async def format_toggle_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    key = parts[1]
    if key not in ("caps", "bold", "italic"):
        return

    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if not isinstance(mode, list):
        mode = []

    if key in mode:
        mode.remove(key)
    else:
        mode.append(key)

    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"format_mode": mode}},
        upsert=True
    )
    await _render_format_menu(call)


@dp.callback_query(F.data == "format_preview")
async def format_preview_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if not mode:
        await call.answer("Включи хотя бы один стиль", show_alert=True)
        return
    sample = "Привет! Это пример твоего форматирования текста"
    formatted = apply_format(sample, mode)
    with suppress(Exception):
        await bot.send_message(
            chat_id=call.from_user.id,
            text=f"👁 <b>Предпросмотр:</b>\n\n{formatted}",
            parse_mode="HTML"
        )
    with suppress(TelegramBadRequest):
        await call.answer("Отправлено в чат")


# ==========================================
# МЕНЮ УДАЛЁНОК
# ==========================================
@dp.callback_query(F.data == "deleted_menu")
async def deleted_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    owner_id = call.from_user.id
    pipeline = [
        {"$match": {
            "owner_id": owner_id,
            "$or": [{"is_deleted": True}, {"is_edited": True}]
        }},
        {"$group": {
            "_id": {"conn_id": "$conn_id", "chat_id": "$chat_id"},
            "del_count": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}},
            "edit_count": {"$sum": {"$cond": [{"$eq": ["$is_edited", True]}, 1, 0]}},
            "first_name": {"$last": "$peer_first_name"},
            "username": {"$last": "$peer_username"},
        }},
        {"$sort": {"del_count": -1, "edit_count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)

    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="user_main")
        await show_menu(call, "🗑 <b>Удалённые и изменения</b>\n\nПока пусто.", builder.as_markup(), with_photo=True)
        return

    for ch in chats:
        conn_id = ch["_id"]["conn_id"]
        chat_id = ch["_id"]["chat_id"]
        name = ch.get("first_name") or "Без имени"
        uname = f"@{ch['username']}" if ch.get("username") else ""
        del_c = ch.get("del_count", 0)
        edit_c = ch.get("edit_count", 0)
        parts_label = []
        if del_c: parts_label.append(f"🗑{del_c}")
        if edit_c: parts_label.append(f"✏️{edit_c}")
        label = f"{name} {uname} · {' '.join(parts_label)}"
        builder.button(text=label[:60], callback_data=f"delchat|{conn_id}|{chat_id}")

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    await show_menu(call, "🗑 <b>Собеседники (удалённые / изменения):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("delchat|"))
async def deleted_chat_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Ошибка навигации", show_alert=True)
        return
    _, conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id

    base = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id}
    del_total = await archive_collection.count_documents({**base, "is_deleted": True})
    edit_total = await archive_collection.count_documents({**base, "is_edited": True})

    builder = InlineKeyboardBuilder()
    if del_total:
        builder.button(text=f"📋 Все удалённые ({del_total})", callback_data=f"delshow|{conn_id}|{chat_id}|del|0")
    if edit_total:
        builder.button(text=f"✏️ Все изменения ({edit_total})", callback_data=f"delshow|{conn_id}|{chat_id}|edit|0")
    if del_total + edit_total > 30:
        builder.button(text="📥 Скачать архивом", callback_data=f"deldl|{conn_id}|{chat_id}")
    builder.button(text="🔙 Назад", callback_data="deleted_menu")
    builder.adjust(1)

    await show_menu(
        call,
        f"🗑 <b>Активность собеседника</b>\n\n"
        f"Удалено: <b>{del_total}</b>\n"
        f"Изменено: <b>{edit_total}</b>",
        builder.as_markup(), with_photo=True
    )


PAGE_SIZE = 10


async def _render_page(conn_id: str, chat_id: int, owner_id: int, mode: str, offset: int):
    base = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id}
    if mode == "del":
        query = {**base, "is_deleted": True}
        sort_field = "created_at"
    elif mode == "edit":
        query = {**base, "is_edited": True}
        sort_field = "edited_at"
    else:
        return None, 0, 0, False

    total = await archive_collection.count_documents(query)
    msgs = await archive_collection.find(query).sort(sort_field, -1).skip(offset).limit(PAGE_SIZE).to_list(length=PAGE_SIZE)
    msgs.reverse()

    if not msgs:
        return None, 0, total, False

    lines = []
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m %H:%M") if isinstance(ts, datetime) else "?"
        author = m.get("first_name") or "Без имени"
        is_owner = m.get("is_owner", False)

        prefix = ""
        if m.get("is_deleted") and not m.get("is_edited"):
            prefix = "🗑 "
        elif m.get("is_edited"):
            prefix = "✏️ "

        sender = "Я" if is_owner else html.escape(author)

        if m.get("is_edited"):
            old_t = html.escape(m.get("old_text") or "[не сохранено]")
            new_t = html.escape(m.get("text") or "")
            body = f"<b>Было:</b> <i>{old_t[:400]}</i>\n<b>Стало:</b> {new_t[:400]}"
        else:
            body = html.escape(m.get("text") or "[медиа]")
            if m.get("media_type") and m.get("drive_link"):
                body += f'\n📎 <a href="{m["drive_link"]}">[{m["media_type"]}]</a>'
            elif m.get("media_type"):
                body += f'\n📎 [{m["media_type"]}] (загрузка...)'

        lines.append(f"{prefix}<b>{ts_str} · {sender}</b>\n{body}\n{'─' * 18}")

    header = f"<b>📄 Показано {offset + 1}–{offset + len(msgs)} из {total}</b>\n\n"
    text = header + "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n<i>...обрезано</i>"

    has_more = (offset + len(msgs)) < total
    return text, len(msgs), total, has_more


@dp.callback_query(F.data.startswith("delshow|"))
async def show_deleted_messages(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 5:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id, chat_id_str, mode, offset_str = parts
    chat_id = int(chat_id_str)
    offset = int(offset_str)
    owner_id = call.from_user.id

    text, shown, total, has_more = await _render_page(conn_id, chat_id, owner_id, mode, offset)
    if not text:
        await call.answer("Пусто", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if has_more:
        builder.button(
            text=f"⬇️ Показать ещё {min(PAGE_SIZE, total - offset - shown)}",
            callback_data=f"delshow|{conn_id}|{chat_id}|{mode}|{offset + shown}"
        )
    builder.button(text="🔙 К собеседнику", callback_data=f"delchat|{conn_id}|{chat_id}")
    builder.adjust(1)

    with suppress(Exception):
        await call.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup(), disable_web_page_preview=True)
    with suppress(TelegramBadRequest):
        await call.answer()


def _build_archive_html(msgs: list, chat_title: str) -> str:
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        f'<title>Архив: {html.escape(chat_title)}</title>',
        '<style>body{font-family:sans-serif;background:#0e1621;color:#e9edf1;padding:20px;max-width:900px;margin:0 auto;}',
        '.m{padding:10px 14px;background:#17212b;border-radius:8px;margin-bottom:6px;}',
        '.m.edit{background:#2b3f52;border-left:3px solid #f0a72c;}',
        '.m.del{background:#3a2124;border-left:3px solid #e05555;}',
        '.h{color:#5eb5f7;font-size:13px;margin-bottom:4px;}',
        '.o{color:#8899a6;text-decoration:line-through;}',
        '.t{white-space:pre-wrap;}</style></head><body>',
        f'<h1>Архив: {html.escape(chat_title)}</h1>',
        f'<p>Сообщений: {len(msgs)} · {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>'
    ]
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m.%Y %H:%M") if isinstance(ts, datetime) else "?"
        author = html.escape(m.get("first_name") or "Без имени")
        if m.get("is_owner"):
            author = "Я"
        cls = "m"
        if m.get("is_deleted"):
            cls += " del"
        if m.get("is_edited"):
            cls += " edit"

        if m.get("is_edited") and m.get("old_text"):
            body = f'<div class="o">{html.escape(m.get("old_text"))}</div><div class="t">{html.escape(m.get("text") or "")}</div>'
        else:
            body = f'<div class="t">{html.escape(m.get("text") or "[медиа]")}</div>'
        if m.get("drive_link"):
            body += f'<div><a href="{m["drive_link"]}" style="color:#5eb5f7;">📎 {m.get("media_type")}</a></div>'

        parts.append(f'<div class="{cls}"><div class="h">{ts_str} · {author}</div>{body}</div>')
    parts.append('</body></html>')
    return "".join(parts)


@dp.callback_query(F.data.startswith("deldl|"))
async def download_archive(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id

    msgs = await archive_collection.find(
        {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id,
         "$or": [{"is_deleted": True}, {"is_edited": True}]}
    ).sort("created_at", 1).to_list(length=5000)

    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return

    chat_title = msgs[0].get("peer_first_name") or msgs[0].get("first_name") or f"chat_{chat_id}"
    content = _build_archive_html(msgs, chat_title).encode("utf-8")
    filename = f"archive_{chat_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.html"

    input_file = BufferedInputFile(content, filename=filename)
    with suppress(Exception):
        await bot.send_document(chat_id=owner_id, document=input_file,
                                caption=f"📥 Архив: {len(msgs)} событий")
    with suppress(TelegramBadRequest):
        await call.answer("Отправлено в личку")


# ==========================================
# АДМИНКА
# ==========================================
def get_admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💼 Мои твинки", callback_data="admin_twinks")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes")],
    ])


@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return
    await message.answer("👑 <b>Панель управления:</b>", reply_markup=get_admin_main_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_main")
async def back_to_main_admin(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await show_menu(call, "👑 <b>Панель управления:</b>", get_admin_main_kb(), with_photo=True)


@dp.callback_query(F.data == "admin_twinks")
async def admin_twinks(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    conns = await connections_collection.find({}).to_list(length=100)
    builder = InlineKeyboardBuilder()
    if not conns:
        builder.button(text="🔙 Назад", callback_data="admin_main")
        await show_menu(call, "Нет подключённых аккаунтов.", builder.as_markup(), with_photo=True)
        return

    for c in conns:
        name = c.get("first_name", "Без имени")
        uname = f"@{c.get('username', '')}" if c.get("username") else ""
        conn_id = c["business_connection_id"]
        count = await archive_collection.count_documents({"conn_id": conn_id})
        label = f"💼 {name} {uname} ({count})"
        builder.button(text=label[:60], callback_data=f"twink|{conn_id}")
    builder.button(text="🔙 Назад", callback_data="admin_main")
    builder.adjust(1)
    await show_menu(call, "💼 <b>Выбери аккаунт (твинк):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("twink|"))
async def twink_chats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id = parts

    pipeline = [
        {"$match": {"conn_id": conn_id}},
        {"$group": {
            "_id": {"chat_id": "$chat_id"},
            "count": {"$sum": 1},
            "deleted": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}},
            "first_name": {"$last": "$peer_first_name"},
            "username": {"$last": "$peer_username"},
        }},
        {"$sort": {"count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)

    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="admin_twinks")
        await show_menu(call, "У этого аккаунта пока нет переписок.", builder.as_markup(), with_photo=True)
        return

    for ch in chats:
        chat_id = ch["_id"]["chat_id"]
        name = ch.get("first_name") or "Без имени"
        uname = f"@{ch['username']}" if ch.get("username") else ""
        label = f"{name} {uname} · {ch['count']} msg"
        if ch.get("deleted"):
            label += f" · 🗑 {ch['deleted']}"
        builder.button(text=label[:60], callback_data=f"twinkchat|{conn_id}|{chat_id}")
    builder.button(text="🔙 Назад", callback_data="admin_twinks")
    builder.adjust(1)
    await show_menu(call, "💬 <b>Переписки аккаунта:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("twinkchat|"))
async def twink_full_chat(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)

    total = await archive_collection.count_documents({"conn_id": conn_id, "chat_id": chat_id})
    deleted = await archive_collection.count_documents({"conn_id": conn_id, "chat_id": chat_id, "is_deleted": True})
    msgs = await archive_collection.find({"conn_id": conn_id, "chat_id": chat_id}).sort("created_at", -1).limit(50).to_list(length=50)
    msgs.reverse()

    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return

    lines = []
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m %H:%M") if isinstance(ts, datetime) else "?"
        author = html.escape(m.get("first_name") or "Без имени")
        if m.get("is_owner"):
            author = "Я"
        prefix = ""
        if m.get("is_deleted"): prefix += "🗑"
        if m.get("is_edited"): prefix += "✏️"
        text_body = html.escape(m.get("text") or "[медиа]")
        if m.get("media_type") and m.get("drive_link"):
            text_body += f'\n📎 <a href="{m["drive_link"]}">[{m["media_type"]}]</a>'
        lines.append(f"{prefix} <b>{ts_str} · {author}</b>\n{text_body}\n{'─' * 15}")

    peer_name = msgs[-1].get("peer_first_name") or "Собеседник"
    peer_uname = msgs[-1].get("peer_username") or ""
    peer_str = f"{peer_name} @{peer_uname}" if peer_uname else peer_name

    header = f"<b>💬 Переписка с {html.escape(peer_str)}</b>\nВсего: {total} · Удалено: {deleted}\nПоказано: последние {len(msgs)}\n{'━' * 18}\n\n"
    full_text = header + "\n".join(lines)
    if len(full_text) > 3900:
        full_text = full_text[:3900] + "\n<i>...обрезано</i>"

    with suppress(Exception):
        await call.message.answer(full_text, parse_mode="HTML", disable_web_page_preview=True)
    with suppress(TelegramBadRequest):
        await call.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_main")

    total_archive = await archive_collection.count_documents({})
    total_deleted = await archive_collection.count_documents({"is_deleted": True})
    total_edited = await archive_collection.count_documents({"is_edited": True})
    total_conns = await connections_collection.count_documents({})

    await show_menu(
        call,
        f"📊 <b>Статистика:</b>\n\n"
        f"Бизнесов: {total_conns}\n"
        f"Сообщений в архиве: {total_archive}\n"
        f"Удалённых: {total_deleted}\n"
        f"Изменённых: {total_edited}",
        builder.as_markup(), with_photo=True
    )


@dp.callback_query(F.data == "admin_mutes")
async def admin_mutes(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    has_mutes = False
    for mute_key in list(muted_chats.keys()):
        try:
            conn, chat_str = mute_key.rsplit("_", 1)
            peer_id = int(chat_str)
            if not await is_chat_muted(conn, peer_id):
                continue
            has_mutes = True
            peer_info = await get_peer_info(conn, peer_id)
            name = peer_info.get("first_name") or chat_str
            uname = f" @{peer_info['username']}" if peer_info.get("username") else ""
            rem_str = format_remaining_mute(muted_chats.get(mute_key))
            btn_text = f"🔇 {name}{uname} ({rem_str})"
            builder.button(text=btn_text[:60], callback_data=f"forceunmute|{mute_key}")
        except Exception:
            continue

    builder.button(text="🔙 Назад", callback_data="admin_main")
    builder.adjust(1)
    if not has_mutes:
        await show_menu(call, "Активных мутов нет.", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "🔇 <b>Активные муты (нажми, чтобы снять):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("forceunmute|"))
async def force_unmute(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    mute_key = parts[1]
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К мутам", callback_data="admin_mutes")

    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})

    if mute_key in muted_chats:
        muted_chats.pop(mute_key, None)
        await show_menu(call, "✅ Мут снят.", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "Мут уже снят.", builder.as_markup(), with_photo=True)


# ==========================================
# АВТООТВЕТЧИК
# ==========================================
@dp.callback_query(F.data == "afk_menu")
async def afk_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    is_afk = user_data.get("is_afk", False)

    if is_afk:
        toggle_btn = InlineKeyboardButton(text="🟢 Выключить автоответчик", callback_data="toggle_afk")
    else:
        toggle_btn = InlineKeyboardButton(text="🔴 Включить автоответчик", callback_data="toggle_afk")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [toggle_btn],
        [InlineKeyboardButton(text="⚙️ Настройки автоответчика", callback_data="afk_settings")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main")],
    ])

    state = "🟢 ВКЛЮЧЕН" if is_afk else "🔴 ВЫКЛЮЧЕН"
    await show_menu(
        call,
        f"💤 <b>Автоответчик</b>\n\nТекущий статус: <b>{state}</b>\n"
        f"<i>💡 Автоответчик выключается сам, если вы напишете в чат вручную.</i>",
        kb, with_photo=True
    )


async def get_afk_settings_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    auto_afk = user_data.get("auto_afk", False)
    start_h = user_data.get("afk_start", 23)
    end_h = user_data.get("afk_end", 7)
    tz_offset = user_data.get("tz_offset", 3)
    auto_status = "ВКЛ" if auto_afk else "ВЫКЛ"
    tz_sign = "+" if tz_offset >= 0 else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="afk_set_text")],
        [InlineKeyboardButton(text=f"🕒 Авто-включение: {auto_status}", callback_data="toggle_auto_afk")],
        [InlineKeyboardButton(text=f"⏰ Время: {start_h}:00 - {end_h}:00", callback_data="afk_set_time")],
        [InlineKeyboardButton(text=f"🌍 Часовой пояс: UTC{tz_sign}{tz_offset}", callback_data="afk_set_tz")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="afk_menu")],
    ])


@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return
    kb = await get_afk_settings_kb(call.from_user.id)
    await show_menu(call, "⚙️ <b>Настройки автоответчика:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("is_afk", False)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"is_afk": new_status}},
        upsert=True
    )
    await afk_menu_handler(call)


@dp.callback_query(F.data == "toggle_auto_afk")
async def toggle_auto_afk(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("auto_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": new_status}}, upsert=True)
    kb = await get_afk_settings_kb(call.from_user.id)
    await show_menu(call, "⚙️ <b>Настройки автоответчика:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    if current is None:
        current = "Владелец занят. 💤"
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await show_menu(
        call,
        f"Текущий текст:\n<i>{html.escape(current)}</i>\n\nОтправь новый:",
        builder.as_markup(), with_photo=True
    )
    await state.set_state(UserStates.waiting_for_afk_text)


@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Отправь <b>текстовое</b> сообщение.", parse_mode="HTML")
        return
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Сохранено!", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await show_menu(
        call,
        "Формат: <code>23 7</code> (с 23:00 до 07:00)",
        builder.as_markup(), with_photo=True
    )
    await state.set_state(UserStates.waiting_for_afk_time)


@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Отправь время в формате: <code>23 7</code>", parse_mode="HTML")
        return
    try:
        parts = message.text.replace("-", " ").split()
        start_h, end_h = int(parts[0]), int(parts[1])
        if 0 <= start_h <= 23 and 0 <= end_h <= 23:
            await users_collection.update_one(
                {"user_id": message.from_user.id},
                {"$set": {"afk_start": start_h, "afk_end": end_h}}
            )
            kb = await get_afk_settings_kb(message.from_user.id)
            await message.answer("✅ Сохранено!", reply_markup=kb)
            await state.clear()
        else:
            await message.answer("⚠ Часы от 0 до 23")
    except Exception:
        await message.answer("⚠ Формат: <code>23 7</code>", parse_mode="HTML")


@dp.callback_query(F.data == "afk_set_tz")
async def afk_set_tz(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("tz_offset", 3)

    builder = InlineKeyboardBuilder()
    for offset in range(-12, 15):
        sign = "+" if offset >= 0 else ""
        prefix = "✅ " if offset == current else ""
        builder.button(text=f"{prefix}UTC{sign}{offset}", callback_data=f"afk_tz_set|{offset}")
    builder.button(text="🔙 Назад", callback_data="afk_settings")
    builder.adjust(3, 3, 3, 3, 3, 3, 3, 3, 3, 1)
    await show_menu(call, "🌍 <b>Выбери свой часовой пояс:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("afk_tz_set|"))
async def afk_tz_set_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    try:
        offset = int(call.data.split("|")[1])
    except (ValueError, IndexError):
        return
    if -12 <= offset <= 14:
        await users_collection.update_one(
            {"user_id": call.from_user.id},
            {"$set": {"tz_offset": offset}},
            upsert=True
        )
    await afk_settings_handler(call)


# ==========================================
# ВАЛЮТЫ (настройки)
# ==========================================
@dp.callback_query(F.data == "currency_settings")
async def currency_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])

    builder = InlineKeyboardBuilder()
    for cur in ALL_CURRENCIES:
        if cur in display:
            builder.button(text=f"✅ {cur}", callback_data=f"cur_toggle|{cur}")
        else:
            builder.button(text=cur, callback_data=f"cur_toggle|{cur}")

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(2)
    await show_menu(call, "💱 <b>Выбери валюты:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("cur_toggle|"))
async def currency_toggle_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    cur = parts[1]
    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    if cur in display:
        display.remove(cur)
    else:
        display.append(cur)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"display_currencies": display}},
        upsert=True
    )
    await currency_settings_handler(call)


# ==========================================
# МУТЫ В МЕНЮ
# ==========================================
@dp.callback_query(F.data == "user_mutes")
async def user_mutes_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    user_conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in user_conns]
    builder = InlineKeyboardBuilder()
    has_mutes = False

    for mute_key in list(muted_chats.keys()):
        try:
            conn, chat_str = mute_key.rsplit("_", 1)
            if conn not in conn_ids:
                continue
            peer_id = int(chat_str)
            if not await is_chat_muted(conn, peer_id):
                continue
            has_mutes = True
            peer_info = await get_peer_info(conn, peer_id)
            name = peer_info.get("first_name") or chat_str
            uname = f" @{peer_info['username']}" if peer_info.get("username") else ""
            rem_str = format_remaining_mute(muted_chats.get(mute_key))
            btn_text = f"🔇 {name}{uname} · {rem_str}"
            builder.button(text=btn_text[:60], callback_data=f"u_unmute|{mute_key}")
        except Exception:
            continue

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    if not has_mutes:
        await show_menu(call, "🔇 <b>Активных мутов нет.</b>", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "🔇 <b>Активные муты (нажми на собеседника, чтобы размутить):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("u_unmute|"))
async def user_unmute_callback(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    mute_key = parts[1]
    try:
        conn, chat = mute_key.rsplit("_", 1)
        owner_conn = await connections_collection.find_one({
            "business_connection_id": conn,
            "user_id": call.from_user.id
        })
        if not owner_conn and call.from_user.id != SUPERADMIN_ID:
            await call.answer("Нет доступа", show_alert=True)
            return
    except Exception:
        await call.answer("Ошибка", show_alert=True)
        return

    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})

    if mute_key in muted_chats:
        muted_chats.pop(mute_key, None)
        await call.answer("✅ Мут снят!", show_alert=True)
    else:
        await call.answer("Уже снят.", show_alert=True)
    await user_mutes_handler(call)


# ==========================================
# СТАТУС
# ==========================================
@dp.callback_query(F.data == "user_status")
async def user_status_handler(call: CallbackQuery):
    owner_id = call.from_user.id

    user_data = await users_collection.find_one({"user_id": owner_id}) or {}
    is_afk = user_data.get("is_afk", False)
    afk_text = user_data.get("afk_text") or "—"
    display_cur = user_data.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    format_mode = user_data.get("format_mode", [])
    if not isinstance(format_mode, list):
        format_mode = []

    conns = await connections_collection.find({"user_id": owner_id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in conns]

    total_archive = 0
    total_deleted = 0
    total_edited = 0
    if conn_ids:
        total_archive = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}})
        total_deleted = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}, "is_deleted": True})
        total_edited = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}, "is_edited": True})

    connected = len(conns) > 0

    lines = ["👤 <b>Мой статус</b>", ""]
    lines.append(f"🆔 <b>ID:</b> <code>{owner_id}</code>")
    lines.append(f"🔗 <b>Бот подключён:</b> {'✅ Да' if connected else '❌ Нет'}")
    lines.append(f"💼 <b>Аккаунтов:</b> {len(conns)}")
    lines.append("")
    lines.append("<b>📦 Архив сообщений</b>")
    lines.append(f"• Всего: <b>{total_archive}</b>")
    lines.append(f"• Удалено: <b>{total_deleted}</b>")
    lines.append(f"• Изменено: <b>{total_edited}</b>")
    lines.append("")
    lines.append(f"💤 <b>Автоответчик:</b> {'🟢 ВКЛ' if is_afk else '🔴 ВЫКЛ'}")
    if is_afk and afk_text and afk_text != "—":
        lines.append(f"💬 <i>{html.escape(afk_text[:100])}</i>")
    lines.append("")
    lines.append(f"💱 <b>Валюты:</b> <code>{', '.join(display_cur)}</code>")
    lines.append(f"✍️ <b>Форматирование:</b> {format_mode_label(format_mode)}")

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main")
    await show_menu(call, "\n".join(lines), builder.as_markup(), with_photo=True)


# ==========================================
# КАТАЛОГ КОМАНД
# ==========================================
def get_cmds_catalog_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡 Модерация и Утилиты", callback_data="cmds_cat|mod")],
        [InlineKeyboardButton(text="🎬 Анимации и Эффекты", callback_data="cmds_cat|anim")],
        [InlineKeyboardButton(text="🧮 Валюты, Математика и NFT", callback_data="cmds_cat|auto")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="user_main")],
    ])


def get_cmds_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К разделам команд", callback_data="user_cmds")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="user_main")],
    ])


@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    text = (
        "📖 <b>Справочник команд</b>\n\n"
        "Все команды вводятся в бизнес-чатах с точкой в начале.\n"
        "Выбери нужный раздел ниже:"
    )
    await show_menu(call, text, get_cmds_catalog_kb(), with_photo=True)


@dp.callback_query(F.data.startswith("cmds_cat|"))
async def show_cmds_category(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return

    cat = call.data.split("|")[1]

    if cat == "mod":
        admin_block = ""
        if call.from_user.id == SUPERADMIN_ID:
            admin_block = (
                "<b>📋 .коп / .вст</b> (Супер-Админ)\n"
                "Ответь <code>.коп</code> на любое сообщение (текст, стикер, ГС, кружок, видео), затем напиши <code>.вст</code> для вставки.\n\n"
            )
        text = (
            "🛡 <b>Модерация и Утилиты</b>\n\n"
            "<b>🔇 .мут [время]</b>\n"
            "Удаляет все сообщения собеседника (навсегда или по таймеру).\n"
            "Примеры: <code>.мут</code> · <code>.мут 10м</code> · <code>.мут 2ч</code> · <code>.мут 1д</code>\n\n"
            "<b>🔁 .амут [текст]</b>\n"
            "Антимут. Отправляет текст от лица бота в обход чужих мутов и защищает его 15 сек.\n"
            "Пример: <code>.амут привет</code>\n\n"
            "<b>🎭 .фейк [тип] [секунды]</b>\n"
            "Имитирует активность в шапке чата (до 120 сек).\n"
            "• <code>.фейк печать 30</code> — <i>печатает...</i>\n"
            "• <code>.фейк гс 40</code> — <i>записывает ГС...</i>\n"
            "• <code>.фейк кружок 20</code> — <i>записывает кружок...</i>\n"
            "• <code>.фейк видео 20</code> / <code>.фейк фото 15</code>\n"
            "• <code>.фейк стоп</code> — остановить имитацию\n\n"
            f"{admin_block}"
            "<b>💣 .[N] [текст]</b>\n"
            "Отправляет сообщение N раз подряд (до 50).\n"
            "Пример: <code>.10 привет</code>"
        )
    elif cat == "anim":
        text = (
            "🎬 <b>Анимации и Эффекты</b>\n\n"
            "<b>🔥 Алгоритмические спецэффекты:</b>\n"
            "• <code>.танос [текст]</code> — текст рассыпается в пыль и исчезает 💨\n"
            "• <code>.пак [текст]</code> — Пакман ᗧ съедает блоки и открывает текст\n"
            "• <code>.брут [текст]</code> — скоростной подбор пароля по буквам 🔐\n"
            "• <code>.глитч [текст]</code> — Zalgo-помехи и стабилизация сигнала ⚡\n"
            "• <code>.табло [текст]</code> — бегущая неоновая LED-строка 📟\n\n"
            "<b>⌨️ Классическая печать:</b>\n"
            "• <code>.п1 [текст]</code> — плавная печать по буквам\n"
            "• <code>.п2 [текст]</code> — печать с мигающим курсором ▌\n"
            "• <code>.п3 [текст]</code> — сборка из случайных символов"
        )
    else:
        text = (
            "🧮 <b>Валюты, Математика и NFT</b>\n\n"
            "<b>💱 Конвертер валют (на лету)</b>\n"
            "Пиши сумму или пример с валютой — бот мгновенно переведёт её:\n"
            "• <code>6 тон</code>\n"
            "• <code>100 баксов</code>\n"
            "• <code>500 звезд</code>\n"
            "• <code>10 тон + 5 тон</code>\n"
            "• <code>5 тон + 7 баксов</code> <i>(смешанные валюты)</i>\n\n"
            "<b>🧮 Умный калькулятор</b>\n"
            "Считает примеры цифрами и словами:\n"
            "• <code>25 * 4</code> → <code>25 * 4 = 100</code>\n"
            "• <code>пять плюс пять</code> → <code>5 + 5 = 10</code>\n\n"
            "<b>🎁 Парсер NFT-подарков</b>\n"
            "Отправь ссылку вида <code>t.me/nft/PlushPepe-274</code> — бот выведет карточку с редкостью, Floor-ценой и дешёвыми офферами."
        )

    await show_menu(call, text, get_cmds_back_kb(), with_photo=True)


# ==========================================
# БИЗНЕС-СОБЫТИЯ
# ==========================================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        rights_dict = {}
        if hasattr(connection, "rights") and connection.rights:
            r = connection.rights
            rights_dict = {
                "can_reply": getattr(r, "can_reply", False),
                "can_read_messages": getattr(r, "can_read_messages", False),
                "can_delete_sent_messages": getattr(r, "can_delete_sent_messages", False),
                "can_delete_all_messages": getattr(r, "can_delete_all_messages", False),
                "can_edit_name": getattr(r, "can_edit_name", False),
                "can_edit_bio": getattr(r, "can_edit_bio", False),
                "can_edit_profile_photo": getattr(r, "can_edit_profile_photo", False),
                "can_edit_username": getattr(r, "can_edit_username", False),
            }

        await connections_collection.update_one(
            {"business_connection_id": connection.id},
            {"$set": {
                "business_connection_id": connection.id,
                "user_id": connection.user.id,
                "first_name": connection.user.first_name or "Без имени",
                "username": connection.user.username or "",
                "rights": rights_dict,
                "updated_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

        with suppress(Exception):
            await bot.send_message(
                chat_id=connection.user.id,
                text=(
                    "✅ <b>Бот успешно подключён!</b>\n\n"
                    "Открой /start, чтобы пользоваться меню."
                ),
                parse_mode="HTML"
            )
    else:
        with suppress(Exception):
            await connections_collection.delete_one({"business_connection_id": connection.id})


# ==========================================
# КОПИРОВАНИЕ И ВСТАВКА (.коп / .вст)
# ==========================================
@dp.business_message(F.text.lower().startswith(".коп"))
@dp.message(F.text.lower().startswith(".коп"))
async def copy_message_superadmin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    conn_id = getattr(message, "business_connection_id", None)
    if conn_id:
        await check_and_save_protected_media(message, SUPERADMIN_ID)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    else:
        with suppress(Exception):
            await message.delete()

    reply = message.reply_to_message
    if not reply:
        return

    media_type, file_id, _, _, _ = _extract_media_info(reply)
    html_text = getattr(reply, "html_text", None) or reply.text or reply.caption or ""

    clip_data = {
        "media_type": media_type,
        "file_id": file_id,
        "html_text": html_text,
    }
    copied_messages_cache[SUPERADMIN_ID] = clip_data
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": SUPERADMIN_ID},
            {"$set": {"copied_msg": clip_data}},
            upsert=True
        )


@dp.business_message(F.text.lower().startswith(".вст"))
@dp.message(F.text.lower().startswith(".вст"))
async def paste_message_superadmin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    conn_id = getattr(message, "business_connection_id", None)
    if conn_id:
        await handle_owner_activity(SUPERADMIN_ID, message.chat.id)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    else:
        with suppress(Exception):
            await message.delete()

    clip_data = copied_messages_cache.get(SUPERADMIN_ID)
    if not clip_data:
        user_doc = await users_collection.find_one({"user_id": SUPERADMIN_ID}) or {}
        clip_data = user_doc.get("copied_msg")
        if clip_data:
            copied_messages_cache[SUPERADMIN_ID] = clip_data

    if not clip_data:
        return

    media_type = clip_data.get("media_type")
    file_id = clip_data.get("file_id")
    html_text = clip_data.get("html_text") or ""
    chat_id = message.chat.id
    reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None

    with suppress(Exception):
        if media_type == "photo" and file_id:
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "video" and file_id:
            await bot.send_video(chat_id=chat_id, video=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "video_note" and file_id:
            await bot.send_video_note(chat_id=chat_id, video_note=file_id, reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "animation" and file_id:
            await bot.send_animation(chat_id=chat_id, animation=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "voice" and file_id:
            await bot.send_voice(chat_id=chat_id, voice=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "audio" and file_id:
            await bot.send_audio(chat_id=chat_id, audio=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "document" and file_id:
            await bot.send_document(chat_id=chat_id, document=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "sticker" and file_id:
            await bot.send_sticker(chat_id=chat_id, sticker=file_id, reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif html_text:
            await bot.send_message(chat_id=chat_id, text=html_text, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)


# ==========================================
# МУТ (.мут [время])
# ==========================================
@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return

    peer_id = message.chat.id
    mute_key = f"{conn_id}_{peer_id}"

    duration_sec, duration_label = parse_mute_duration(message.text)
    until_ts = (datetime.now(timezone.utc).timestamp() + duration_sec) if duration_sec else None

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    with suppress(Exception):
        await mutes_collection.update_one(
            {"mute_key": mute_key},
            {"$set": {
                "mute_key": mute_key,
                "conn_id": conn_id,
                "peer_id": peer_id,
                "owner_id": owner_id,
                "until_ts": until_ts,
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

    muted_chats[mute_key] = until_ts
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute|{conn_id}|{peer_id}")]])
    with suppress(Exception):
        await bot.send_message(
            chat_id=peer_id,
            text=f"🔇 Собеседник замьючен ({duration_label}).",
            reply_markup=markup,
            business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".амут"))
async def amute_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return

    raw = message.text[5:].strip()
    peer_id = message.chat.id
    key = f"{conn_id}_{peer_id}"

    await handle_owner_activity(owner_id, peer_id)

    # .амут стоп — выключить защиту
    if raw.lower() in ("стоп", "stop", "off", "выкл"):
        active_amute.pop(key, None)
        _amute_lock.pop(key, None)  # чистим Lock чтобы не утекал
        with suppress(Exception):
            await bot.delete_business_messages(
                business_connection_id=conn_id,
                message_ids=[message.message_id]
            )
        with suppress(Exception):
            await bot.send_message(
                chat_id=peer_id,
                text="🔓 Защита сообщения снята.",
                business_connection_id=conn_id
            )
        return

    if not raw:
        return

    # Синтаксис: .амут [N] текст  — N = секунды защиты (необязательно)
    # Например: .амут 30 привет — защита 30 секунд
    #           .амут привет     — защита 3 минуты (дефолт)
    AMUTE_DEFAULT_SECS = 180  # 3 минуты по умолчанию
    until_ts: float
    m = re.match(r"^(\d+)\s+(.+)$", raw, re.DOTALL)
    if m:
        secs = min(int(m.group(1)), 3600)  # максимум 1 час
        text = m.group(2)
        until_ts = datetime.now(timezone.utc).timestamp() + (secs if secs > 0 else AMUTE_DEFAULT_SECS)
    else:
        text = raw
        until_ts = datetime.now(timezone.utc).timestamp() + AMUTE_DEFAULT_SECS

    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=conn_id,
            message_ids=[message.message_id]
        )

    sent = None
    with suppress(Exception):
        sent = await bot.send_message(
            chat_id=peer_id,
            text=text,
            business_connection_id=conn_id
        )

    if not sent:
        return

    active_amute[key] = {
        "text": text,
        "message_id": sent.message_id,
        "until_ts": until_ts,
        "resends": 0,          # счётчик переотправок
    }


# ==========================================
# ФЕЙК-АКТИВНОСТИ (.фейк) 
# ==========================================
FAKE_ACTION_MAP = {
    "печать": "typing", "текст": "typing", "т": "typing",
    "гс": "record_voice", "войс": "record_voice", "голос": "record_voice",
    "кружок": "record_video_note", "к": "record_video_note",
    "видео": "upload_video", "в": "upload_video",
    "фото": "upload_photo", "ф": "upload_photo",
    "док": "upload_document", "файл": "upload_document",
    "стикер": "choose_sticker", "игра": "choose_sticker",
}


@dp.business_message(F.text.lower().startswith(".фейк"))
async def fake_action_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    peer_id = message.chat.id
    owner_id = owner_data["user_id"]
    await handle_owner_activity(owner_id, peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    key = f"{conn_id}_{peer_id}"
    old_task = active_fakes.pop(key, None)
    if old_task and not old_task.done():
        old_task.cancel()

    args = message.text[5:].strip().lower().split()
    if args and args[0] in ("стоп", "stop", "выкл", "off"):
        return

    action = "typing"
    duration = 25

    for arg in args:
        if arg.isdigit():
            duration = min(max(int(arg), 5), 120)
        elif arg in FAKE_ACTION_MAP:
            action = FAKE_ACTION_MAP[arg]

    async def _run_fake():
        end_time = datetime.now(timezone.utc).timestamp() + duration
        while datetime.now(timezone.utc).timestamp() < end_time:
            try:
                await bot.send_chat_action(
                    chat_id=peer_id,
                    action=action,
                    business_connection_id=conn_id
                )
            except Exception as e:
                print(f"❌ Ошибка .фейк (send_chat_action): {e}")
                break
            await asyncio.sleep(4.3)

    task = asyncio.create_task(_run_fake())
    # Когда задача завершится — удаляем из словаря чтобы не копились мёртвые записи
    task.add_done_callback(lambda t: active_fakes.pop(key, None))
    active_fakes[key] = task


@dp.business_message(F.text.regexp(r"^\.(\d+)\s+"))
async def spam_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return
    match = re.match(r"^\.(\d+)\s+(.*)", message.text, re.DOTALL)
    if not match:
        return
    count = min(int(match.group(1)), 20)
    peer_id = message.chat.id
    await handle_owner_activity(owner_id, peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    for _ in range(count):
        with suppress(Exception):
            await bot.send_message(chat_id=peer_id, text=match.group(2), business_connection_id=conn_id)
        await asyncio.sleep(0.3)


# ==========================================
# АНИМАЦИИ ПЕЧАТИ
# ==========================================
@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    full_text = message.text[3:].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=peer_id, text=full_text[0], business_connection_id=conn_id)
    if not sent_msg:
        return
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        current = full_text[:i]
        await asyncio.sleep(0.25)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current, business_connection_id=conn_id)


@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    full_text = message.text[3:].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=peer_id, text=full_text[0] + "▌", business_connection_id=conn_id)
    if not sent_msg:
        return
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        current = full_text[:i]
        await asyncio.sleep(0.25)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current + "▌", business_connection_id=conn_id)
    await asyncio.sleep(0.25)
    with suppress(Exception):
        await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=full_text, business_connection_id=conn_id)


@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    full_text = message.text[3:].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"
    sent_msg = await bot.send_message(chat_id=peer_id, text="...", business_connection_id=conn_id)
    if not sent_msg:
        return
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        idx = min(i, len(full_text))
        await asyncio.sleep(0.22)
        correct = full_text[:idx]
        rnd = "".join(random.choice(alphabet) for _ in range(len(full_text) - idx))
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=correct + rnd, business_connection_id=conn_id)


# ==========================================
# СПЕЦЭФФЕКТЫ
# ==========================================
@dp.business_message(F.text.lower().startswith(".танос"))
async def anim_thanos(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    sent = await bot.send_message(
        chat_id=peer_id,
        text=f"<code>{html.escape(raw_text)}</code>",
        parse_mode="HTML",
        business_connection_id=conn_id
    )
    if not sent:
        return

    self_destruct_ids[f"{conn_id}_{sent.message_id}"] = True
    await asyncio.sleep(1.5)

    chars = list(raw_text)
    indices = [i for i, c in enumerate(chars) if c != " "]
    random.shuffle(indices)

    dust_particles = ["░", "▒", "·", "˙", "˚"]
    steps = 7
    chunk_size = max(1, (len(indices) + steps - 1) // steps)
    turning_to_space = []

    for s in range(steps):
        for idx in turning_to_space:
            chars[idx] = " "
        batch = indices[s * chunk_size:(s + 1) * chunk_size]
        for idx in batch:
            chars[idx] = random.choice(dust_particles)
        turning_to_space = batch

        current_str = "".join(chars)
        if not current_str.strip():
            break

        await asyncio.sleep(0.32)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id,
                message_id=sent.message_id,
                text=f"<code>{html.escape(current_str)}</code>",
                parse_mode="HTML",
                business_connection_id=conn_id
            )

    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id,
            message_id=sent.message_id,
            text="💨 <i>*испарилось*</i>",
            parse_mode="HTML",
            business_connection_id=conn_id
        )
    await asyncio.sleep(0.7)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[sent.message_id])


@dp.business_message(F.text.lower().startswith(".пак"))
async def anim_pacman(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    n = len(raw_text)
    step = max(1, n // 9)
    init_dots = "•" * min(n, 24)
    sent = await bot.send_message(
        chat_id=peer_id,
        text=f"<code>ᗧ{init_dots}</code>",
        parse_mode="HTML",
        business_connection_id=conn_id
    )
    if not sent:
        return

    frame_num = 0
    for pos in range(step, n, step):
        frame_num += 1
        pac = "ᗤ" if frame_num % 2 == 1 else "ᗧ"
        revealed = html.escape(raw_text[:pos])
        dots_left = "•" * min(n - pos, 20)
        frame = f"<code>{revealed}</code> {pac}<code>{dots_left}</code>"
        await asyncio.sleep(0.28)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id,
                message_id=sent.message_id,
                text=frame,
                parse_mode="HTML",
                business_connection_id=conn_id
            )

    await asyncio.sleep(0.28)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id,
            message_id=sent.message_id,
            text=html.escape(raw_text),
            parse_mode="HTML",
            business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".брут"))
async def anim_brute(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    hex_chars = "0123456789ABCDEF#$%&*@!?"
    n = len(raw_text)
    step = max(1, n // 8)

    sent = await bot.send_message(
        chat_id=peer_id,
        text=f"🔐 <code>[{'█' * min(n, 20)}] 0%</code>",
        parse_mode="HTML",
        business_connection_id=conn_id
    )
    if not sent:
        return

    for pos in range(0, n, step):
        locked = html.escape(raw_text[:pos])
        rem_len = min(n - pos, 16)
        spinning = "".join(random.choice(hex_chars) for _ in range(rem_len))
        pct = int((pos / n) * 100)
        frame = f"🔐 <code>{locked}{spinning}</code> <b>[{pct}%]</b>"
        await asyncio.sleep(0.26)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id,
                message_id=sent.message_id,
                text=frame,
                parse_mode="HTML",
                business_connection_id=conn_id
            )

    await asyncio.sleep(0.26)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id,
            message_id=sent.message_id,
            text=f"🔓 <code>{html.escape(raw_text)}</code>",
            parse_mode="HTML",
            business_connection_id=conn_id
        )


ZALGO_MARKS = [chr(code) for code in range(0x0300, 0x0338)]


def _zalgoify(text: str, intensity: int) -> str:
    out = []
    for ch in text:
        out.append(ch)
        if ch != " " and intensity > 0:
            for _ in range(random.randint(1, intensity)):
                out.append(random.choice(ZALGO_MARKS))
    return "".join(out)


@dp.business_message(F.text.lower().startswith(".глитч"))
async def anim_glitch(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    intensities = [5, 3, 6, 2, 4, 1]
    sent = await bot.send_message(
        chat_id=peer_id,
        text=f"⚡ {html.escape(_zalgoify(raw_text, 6))}",
        parse_mode="HTML",
        business_connection_id=conn_id
    )
    if not sent:
        return

    for lvl in intensities:
        await asyncio.sleep(0.28)
        glitched = html.escape(_zalgoify(raw_text, lvl))
        prefix = random.choice(["⚡", "⚠️", "📡", "🔌"])
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id,
                message_id=sent.message_id,
                text=f"{prefix} {glitched}",
                parse_mode="HTML",
                business_connection_id=conn_id
            )

    await asyncio.sleep(0.28)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id,
            message_id=sent.message_id,
            text=html.escape(raw_text),
            parse_mode="HTML",
            business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".табло"))
async def anim_ticker(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    width = 18
    padded = (" " * width) + raw_text + (" " * width)
    total_shifts = len(padded) - width
    step = max(1, total_shifts // 9)

    def _box(content: str) -> str:
        return (
            f"<code>┏{'━' * (width + 2)}┓\n"
            f"┃ {html.escape(content)} ┃\n"
            f"┗{'━' * (width + 2)}┛</code>"
        )

    sent = await bot.send_message(
        chat_id=peer_id,
        text=_box(padded[:width]),
        parse_mode="HTML",
        business_connection_id=conn_id
    )
    if not sent:
        return

    for pos in range(step, total_shifts + 1, step):
        window = padded[pos:pos + width].ljust(width)
        await asyncio.sleep(0.28)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id,
                message_id=sent.message_id,
                text=_box(window),
                parse_mode="HTML",
                business_connection_id=conn_id
            )

    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id,
            message_id=sent.message_id,
            text=f"📟 <b>{html.escape(raw_text)}</b>",
            parse_mode="HTML",
            business_connection_id=conn_id
        )


# ==========================================
# NFT ПАРСЕР
# ==========================================
NFT_LINK_PATTERN = r"(t\.me/nft/[a-zA-Z0-9_-]+)"


@dp.business_message(F.text.regexp(NFT_LINK_PATTERN))
@dp.message(F.text.regexp(NFT_LINK_PATTERN))
async def process_gift_link_auto(message: Message):
    conn_id = getattr(message, "business_connection_id", None)
    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", message.text)
    if not tg_match:
        return
    slug = tg_match.group(1)

    if conn_id:
        owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
        peer_id = message.chat.id

        if not owner_data:
            if await _is_peer_muted_anywhere(peer_id):
                with suppress(Exception):
                    await bot.delete_business_messages(
                        business_connection_id=conn_id,
                        message_ids=[message.message_id]
                    )
            return
        owner_id = owner_data["user_id"]
        peer_id = message.chat.id
        is_owner = (message.from_user.id == owner_id)

        if not is_owner and await is_chat_muted(conn_id, peer_id):
            # Удаляем: и обычные сообщения собеседника, и сообщения чужих ботов
            if message.from_user.id != BOT_ID or is_foreign_bot(message):
                with suppress(Exception):
                    await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            return

        with suppress(Exception):
            await save_message_quick(message, conn_id, peer_id, owner_id, owner_data.get("first_name", "owner"), is_owner)
        # Помечаем: catch-all handle_messages не должен сохранять это сообщение повторно
        _handled_msg_ids[(conn_id, message.message_id)] = True

        if is_owner:
            await handle_owner_activity(owner_id, peer_id)
        else:
            if await is_bot_connected(message.from_user.id):
                return

        dedup_key = ("nft", frozenset({owner_id, peer_id}), slug.lower())
        if dedup_key in action_dedup:
            return
        action_dedup[dedup_key] = True

    loading_msg = None
    with suppress(Exception):
        loading_msg = await bot.send_message(chat_id=message.chat.id, text="🔍 Анализирую...", business_connection_id=conn_id)

    # Убедимся, что курсы загружены для конвертации цен
    if not EXCHANGE_CACHE:
        await force_update_all_rates()

    data = await get_gift_card_data(slug)
    result_text = format_gift_card(data)
    if loading_msg:
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                text=result_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                business_connection_id=conn_id
            )


# ==========================================
# МАТЕМАТИКА И ВАЛЮТЫ
# ==========================================
@dp.business_message(
    F.text.regexp(AUTO_MATH_CURRENCY_PATTERN)
    | F.text.regexp(AUTO_MATH_PATTERN)
    | F.text.regexp(MIXED_CURRENCY_FULL)
)
async def auto_math_and_currency(message: Message):
    if not message.text or len(message.text) > 200:
        return
    if message.text.lstrip().startswith("."):
        return

    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    peer_id = message.chat.id

    if not owner_data:
        if await _is_peer_muted_anywhere(peer_id):
            with suppress(Exception):
                await bot.delete_business_messages(
                    business_connection_id=conn_id,
                    message_ids=[message.message_id]
                )
        returnn
    owner_id = owner_data["user_id"]
    peer_id = message.chat.id
    is_owner = (message.from_user.id == owner_id)

    if not is_owner and await is_chat_muted(conn_id, peer_id):
        if message.from_user.id != BOT_ID or is_foreign_bot(message):
            with suppress(Exception):
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return

    owner_name = owner_data.get("first_name", "owner")
    with suppress(Exception):
        await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)
    # Помечаем: catch-all handle_messages не должен сохранять это сообщение повторно
    _handled_msg_ids[(conn_id, message.message_id)] = True

    if is_owner:
        await check_and_save_protected_media(message, owner_id)
        await handle_owner_activity(owner_id, peer_id)
    else:
        if await is_bot_connected(message.from_user.id):
            return

    dedup_key = ("calc", frozenset({owner_id, peer_id}), message.text.strip().lower())
    if dedup_key in action_dedup:
        return
    action_dedup[dedup_key] = True

    if MIXED_CURRENCY_FULL.match(message.text):
        parsed = parse_mixed_currency(message.text)
        if parsed:
            result_usdt = await eval_mixed_currency(parsed)
            if result_usdt is not None:
                await send_mixed_currency_conversion(message, parsed, result_usdt, owner_id)
                return

    match_curr = AUTO_MATH_CURRENCY_PATTERN.match(message.text)
    if match_curr:
        math_expr = match_curr.group(1).strip()
        tail = match_curr.group(2).lower()
        code = CURRENCY_ALIASES.get(tail)
        if not code or not math_expr:
            return
        raw = normalize_math_input(math_expr.lower())
        parsed = parse_math_expression(raw)
        if not parsed:
            return
        try:
            amount = float(simple_eval(parsed))
            formatted_expr = format_math_expression(parsed)
            await send_currency_conversion(
                message,
                amount,
                code,
                original_expr=formatted_expr if len(formatted_expr.split()) > 1 else None,
                owner_id=owner_id
            )
        except Exception as e:
            print(f"currency eval error: {e}")
            return
        return

    raw = normalize_math_input(message.text.lower())
    parsed = parse_math_expression(raw)
    if not parsed:
        return
    formatted = format_math_expression(parsed)
    if len(formatted.split()) < 3:
        return
    try:
        result = simple_eval(parsed)
        if isinstance(result, float):
            result = int(result) if round(result, 4).is_integer() else round(result, 4)
    except Exception as e:
        print(f"math eval error: {e}")
        return

    if is_owner:
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    with suppress(Exception):
        await bot.send_message(chat_id=peer_id, text=f"{formatted} = {result}", business_connection_id=conn_id)


# ==========================================
# ГЛАВНЫЙ ОБРАБОТЧИК — С ФОРМАТИРОВАНИЕМ
# ==========================================
@dp.business_message()
async def handle_messages(message: Message):
    conn_id = message.business_connection_id

    # Пропускаем сообщения, уже обработанные специализированным хендлером (NFT, калькулятор)
    if _handled_msg_ids.get((conn_id, message.message_id)):
        return

    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    peer_id = message.chat.id

    # Если сообщение пришло через чужого бота (conn_id не наш) — проверяем мут по пиру
    if not owner_data:
        if await _is_peer_muted_anywhere(peer_id):
            with suppress(Exception):
                await bot.delete_business_messages(
                    business_connection_id=conn_id,
                    message_ids=[message.message_id]
                )
        return

    owner_id = owner_data["user_id"]
    owner_name = owner_data.get("first_name", "owner")
    peer_id = message.chat.id
    is_owner = (message.from_user.id == owner_id)

    if is_owner:
        # Сохраняем защищенные медиа
        await check_and_save_protected_media(message, owner_id)

        # Получаем настройки форматирования
        owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
        mode = owner_settings.get("format_mode", [])
        
        # Сначала сохраняем сообщение в архив (в исходном виде)
        doc = await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)

        # Проверяем, нужно ли форматирование
        should_format = (
            isinstance(mode, list) and mode
            and message.text
            and not message.text.startswith(".")
        )

        if should_format:
            formatted = apply_format(message.text, mode)
            if formatted and formatted != message.text:
                try:
                    # ИСПОЛЬЗУЕМ EDIT_MESSAGE_TEXT ВМЕСТО УДАЛЕНИЯ
                    await bot.edit_message_text(
                        chat_id=peer_id,
                        message_id=message.message_id,
                        text=formatted,
                        parse_mode="HTML",
                        business_connection_id=conn_id
                    )
                    # Обновляем текст в базе данных, если редактирование прошло успешно
                    if doc:
                        await archive_collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"text": formatted}}
                        )
                except Exception as e:
                    print(f"❌ Ошибка форматирования (edit_message_text): {e}")
                    # Если не удалось отредактировать, сообщение остается в исходном виде

        await handle_owner_activity(owner_id, peer_id)
        return

    # --- Собеседник ---
    with suppress(Exception):
        await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)

    # ── Защита .амут от обходов ──────────────────────────────────────────────
    # Пока .амут активен — удаляем у собеседника:
    #   • форварды (forward_origin)
    #   • inline-ответы через другой бот (via_bot != наш бот)
    #   • медиа с подписью (caption) — нельзя отредактировать обычный текст через фото
    amute_key = f"{conn_id}_{peer_id}"
    amute_session = active_amute.get(amute_key)
    if amute_session:
        until_ts = amute_session.get("until_ts", 0)
        if datetime.now(timezone.utc).timestamp() < until_ts:
            is_forward = getattr(message, "forward_origin", None) is not None
            via = getattr(message, "via_bot", None)
            is_via_foreign = via is not None and via.id != BOT_ID
            if is_forward or is_via_foreign:
                with suppress(Exception):
                    await bot.delete_business_messages(
                        business_connection_id=conn_id,
                        message_ids=[message.message_id]
                    )
                return
        else:
            # Истекло — чистим
            active_amute.pop(amute_key, None)
            _amute_lock.pop(amute_key, None)
    # ─────────────────────────────────────────────────────────────────────────

    if await is_chat_muted(conn_id, peer_id):
        if message.from_user.id != owner_id and (message.from_user.id != BOT_ID or is_foreign_bot(message)):
            with suppress(Exception):
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return

    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    manual_afk = owner_settings.get("is_afk", False)
    in_schedule = (
        check_auto_afk(
            owner_settings.get("afk_start", 23),
            owner_settings.get("afk_end", 7),
            owner_settings.get("tz_offset", 3),
        )
        if owner_settings.get("auto_afk", False) else False
    )

    if manual_afk or in_schedule:
        if (owner_id, peer_id) not in afk_cooldowns:
            afk_text = owner_settings.get("afk_text") or "Владелец сейчас занят. 💤"
            with suppress(Exception):
                await bot.send_message(chat_id=peer_id, text=afk_text, business_connection_id=conn_id)
            afk_cooldowns[(owner_id, peer_id)] = True


# ==========================================
# РЕДАКТИРОВАНИЯ И УДАЛЕНИЯ
# ==========================================
@dp.edited_business_message()
async def catch_edits(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    peer_id = message.chat.id
    is_owner = (message.from_user.id == owner_id)

    new_text = message.text or message.caption or "[Без текста]"

    # Защита .амут: если собеседник отредактировал наше .амут-сообщение — откатываем
    if not is_owner:
        key = f"{conn_id}_{peer_id}"
        session = active_amute.get(key)
        if session and session.get("message_id") == message.message_id:
            until_ts = session.get("until_ts", 0)
            if datetime.now(timezone.utc).timestamp() < until_ts:
                with suppress(Exception):
                    await bot.edit_message_text(
                        chat_id=peer_id,
                        message_id=message.message_id,
                        text=session["text"],
                        business_connection_id=conn_id
                    )
                return

    old_doc = await archive_collection.find_one({
        "conn_id": conn_id,
        "chat_id": peer_id,
        "message_id": message.message_id
    })
    if not old_doc:
        return

    old_text = old_doc.get("text", "")
    edited_at = datetime.now(timezone.utc)

    with suppress(Exception):
        await archive_collection.update_one(
            {"_id": old_doc["_id"]},
            {"$set": {
                "text": new_text,
                "old_text": old_text,
                "is_edited": True,
                "is_viewed": False,
                "edited_at": edited_at
            }}
        )

    if not is_owner:
        sender_name = old_doc.get("first_name") or message.from_user.first_name or "Неизвестный"
        sender_username = old_doc.get("username") or message.from_user.username or ""
        await notify_owner_about_edit(owner_id, sender_name, sender_username, old_text, new_text, edited_at)


@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    peer_id = deleted.chat.id

    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]

    for msg_id in deleted.message_ids:
        if f"{conn_id}_{msg_id}" in self_destruct_ids:
            continue

        key = f"{conn_id}_{peer_id}"
        session = active_amute.get(key)
        if session and session.get("message_id") == msg_id:
            # Проверяем не истекло ли время защиты
            until_ts = session.get("until_ts", 0)
            if datetime.now(timezone.utc).timestamp() >= until_ts:
                # Время вышло — снимаем защиту и чистим Lock
                active_amute.pop(key, None)
                _amute_lock.pop(key, None)
                continue

            # Лимит переотправок — защита от бесконечного ping-pong
            resends = session.get("resends", 0)
            if resends >= 200:
                active_amute.pop(key, None)
                _amute_lock.pop(key, None)
                continue

            # Блокировка от race condition — два удаления одновременно → одно сообщение
            if key not in _amute_lock:
                _amute_lock[key] = asyncio.Lock()
            async with _amute_lock[key]:
                # Перепроверяем после блокировки
                session = active_amute.get(key)
                if not session or session.get("message_id") != msg_id:
                    continue
                new_sent = None
                with suppress(Exception):
                    new_sent = await bot.send_message(
                        chat_id=peer_id,
                        text=session["text"],
                        business_connection_id=conn_id
                    )
                if new_sent:
                    session["message_id"] = new_sent.message_id
                    session["resends"] = session.get("resends", 0) + 1
            continue

        old_msg = await archive_collection.find_one({
            "conn_id": conn_id,
            "chat_id": peer_id,
            "message_id": msg_id
        })

        deleted_at = datetime.now(timezone.utc)

        if old_msg:
            with suppress(Exception):
                await archive_collection.update_one(
                    {"_id": old_msg["_id"]},
                    {"$set": {
                        "is_deleted": True,
                        "is_viewed": False,
                        "deleted_at": deleted_at
                    }}
                )

            if not old_msg.get("is_owner"):
                await notify_owner_about_deletion(
                    owner_id,
                    old_msg.get("first_name") or "Неизвестный",
                    old_msg.get("username", ""),
                    old_msg.get("text") or "",
                    old_msg.get("media_type"),
                    old_msg.get("drive_link"),
                    deleted_at,
                    file_id=old_msg.get("media_file_id")
                )


# ==========================================
# СНЯТИЕ МУТА В ЧАТЕ
# ==========================================
@dp.callback_query(F.data.startswith("unmute|"))
async def unmute_user(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 3:
        return
    _, conn_id, peer_id_str = parts
    try:
        peer_id = int(peer_id_str)
    except ValueError:
        return
    mute_key = f"{conn_id}_{peer_id}"

    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]

    if call.from_user.id != owner_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest):
            await call.answer("Нельзя", show_alert=True)
        return

    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})

    muted_chats.pop(mute_key, None)
    with suppress(TelegramBadRequest):
        await call.message.edit_text("🔊 Мут снят.")
        await call.answer("Мут снят")


# ==========================================
# MAIN
# ==========================================
async def main():
    web_task = asyncio.create_task(start_web_server())

    def _web_done(t: asyncio.Task):
        try:
            t.result()
        except Exception as e:
            print(f"❌ Веб-сервер упал: {e}")

    web_task.add_done_callback(_web_done)

    try:
        await dp.start_polling(bot)
    finally:
        if _web_runner:
            with suppress(Exception):
                await _web_runner.cleanup()
        web_task.cancel()
        with suppress(asyncio.CancelledError):
            await web_task


if __name__ == "__main__":
    asyncio.run(main())
