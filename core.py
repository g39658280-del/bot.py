# ==========================================
# core.py — конфиг, БД, кеши, сервисы, утилиты
# ==========================================
import os
import asyncio
import re
import html
import aiohttp
import tempfile
from datetime import datetime, timezone
from aiohttp import web
from contextlib import suppress
from aiogram import Bot
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient
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

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

MENU_PHOTO = "https://files.catbox.moe/bph5iz.webp"
CAPTION_LIMIT = 1024

bot = Bot(token=BOT_TOKEN)

BOT_ID: int | None = None
BOT_USERNAME = "your_bot_username"

# ==========================================
# БД
# ==========================================
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    connections_collection = db['connections']
    users_collection = db['users']
    archive_collection = db['chat_archive']
    mutes_collection = db['mutes']
except Exception as e:
    print(f"Ошибка БД: {e}")

# ==========================================
# КЕШИ
# ==========================================
muted_chats: dict[str, float | None] = {}
afk_cooldowns = TTLCache(maxsize=2000, ttl=300)
active_amute = TTLCache(maxsize=1000, ttl=15)
action_dedup = TTLCache(maxsize=2000, ttl=4)
self_destruct_ids = TTLCache(maxsize=1000, ttl=30)
active_fakes: dict[str, asyncio.Task] = {}
copied_messages_cache = {}
bot_formatted_ids = TTLCache(maxsize=3000, ttl=60)

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
async def show_menu(call, caption: str, kb, with_photo: bool = True):
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
# ФОРМАТИРОВАНИЕ ТЕКСТА
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
        asyncio.create_task(upload_media_bg(
            doc["_id"], file_id, media_type, ext, mime,
            peer_info["first_name"], peer_id, owner_name, file_size
        ))
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
                await bot.send_photo(chat_id=owner_id, photo=file_id, caption=caption); media_sent = True
            elif media_type == "video":
                await bot.send_video(chat_id=owner_id, video=file_id, caption=caption); media_sent = True
            elif media_type == "video_note":
                await bot.send_video_note(chat_id=owner_id, video_note=file_id); media_sent = True
            elif media_type == "animation":
                await bot.send_animation(chat_id=owner_id, animation=file_id, caption=caption); media_sent = True
            elif media_type == "voice":
                await bot.send_voice(chat_id=owner_id, voice=file_id); media_sent = True
            elif media_type == "audio":
                await bot.send_audio(chat_id=owner_id, audio=file_id, caption=caption); media_sent = True
            elif media_type == "document":
                await bot.send_document(chat_id=owner_id, document=file_id, caption=caption); media_sent = True
            elif media_type == "sticker":
                await bot.send_sticker(chat_id=owner_id, sticker=file_id); media_sent = True
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
        await bot.send_message(chat_id=owner_id, text="\n".join(parts),
                               parse_mode="HTML", disable_web_page_preview=True)


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


async def get_gift_card_data(slug: str) -> dict:
    result = {"slug": slug}
    gift_info = await fetch_gift_satellite(f"/gift/by-slug/{slug}")
    if not gift_info:
        return result
    result["collection_name"] = gift_info.get("collectionName", "")
    result["model"] = gift_info.get("modelName", "")
    result["backdrop"] = gift_info.get("backdropName", "")
    result["symbol"] = gift_info.get("symbolName", "")
    result["number"] = gift_info.get("number")
    collection = result["collection_name"]
    if not collection:
        return result
    await asyncio.sleep(0.3)
    offers = await fetch_gift_satellite(f"/search/tg/{collection}")
    if not offers or not isinstance(offers, list):
        return result
    result["all_offers"] = offers
    model = result["model"]
    same_model = [o for o in offers if o.get("modelName") == model]
    result["model_offers"] = same_model
    if same_model:
        prices = [o.get("normalizedPrice", 0) for o in same_model if o.get("normalizedPrice")]
        if prices:
            result["floor_ton"] = min(prices)
            result["avg_ton"] = sum(prices) / len(prices)
    all_prices = [o.get("normalizedPrice", 0) for o in offers if o.get("normalizedPrice")]
    if all_prices:
        result["collection_floor_ton"] = min(all_prices)
    return result


def format_gift_card(data: dict) -> str:
    if not data or "collection_name" not in data:
        return "❌ Подарок не найден в базе Gift Satellite."
    collection = data.get("collection_name", "Подарок")
    number = data.get("number", "?")
    model = data.get("model", "")
    backdrop = data.get("backdrop", "")
    symbol = data.get("symbol", "")
    lines = [f"🎁 <b>{collection} #{number}</b>"]
    attrs = []
    if model: attrs.append(f"Модель: <b>{model}</b>")
    if backdrop: attrs.append(f"Фон: <b>{backdrop}</b>")
    if symbol: attrs.append(f"Символ: <b>{symbol}</b>")
    if attrs: lines.append(" · ".join(attrs))
    lines.append("")
    floor_ton = data.get("floor_ton")
    avg_ton = data.get("avg_ton")
    if floor_ton is not None: lines.append(f"<b>Floor:</b> {floor_ton:.2f} TON")
    if avg_ton is not None: lines.append(f"<b>AVG:</b> {avg_ton:.2f} TON")
    coll_floor = data.get("collection_floor_ton")
    if coll_floor is not None and coll_floor != floor_ton:
        lines.append(f"<b>Floor (коллекция):</b> {coll_floor:.2f} TON")
    model_offers = data.get("model_offers", [])
    if model_offers:
        sorted_offers = sorted(model_offers, key=lambda x: x.get("normalizedPrice", 999999))
        lines.append(f"\n<b>Дешёвые офферы:</b>")
        lines.append("<blockquote>")
        seen, count = set(), 0
        for off in sorted_offers:
            price = off.get("normalizedPrice")
            slug = off.get("slug", "?")
            if not price or price in seen: continue
            seen.add(price)
            lines.append(f'🔘 <a href="https://t.me/nft/{slug}">#{slug}</a> — {price:.2f} TON')
            count += 1
            if count >= 5: break
        lines.append("</blockquote>")
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
                        price = float(data["price"]); break
                    except (ValueError, TypeError):
                        pass
            if price is None:
                cg_id = {"BTC": "bitcoin", "ETH": "ethereum"}.get(code)
                if cg_id:
                    cg_data = await fetch_json(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
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
                    ton_price = float(data["price"]); break
                except (ValueError, TypeError):
                    pass
        if ton_price is None:
            cg_data = await fetch_json("https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd")
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
        await force_update_all_rates()
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
# ВЕБ-СЕРВЕР
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

# ==========================================
# ХЕЛПЕРЫ МЕНЮ
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


def get_admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💼 Мои твинки", callback_data="admin_twinks")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes")],
    ])


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


async def _render_format_menu(call):
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
