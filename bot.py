import os
import io
import asyncio
import random
import html
import re
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
from aiogram.filters import BaseFilter
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

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

MENU_PHOTO = "https://files.catbox.moe/bph5iz.webp"
CAPTION_LIMIT = 1024

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections']
    users_collection = db['users']
    history_collection = db['history']
    archive_collection = db['chat_archive']
    deleted_users_collection = db['deleted_users']
    mutes_collection = db['mutes']
except Exception as e:
    print(f"Ошибка БД: {e}")

muted_chats = set()
afk_cooldowns = TTLCache(maxsize=2000, ttl=300)
active_amute = TTLCache(maxsize=1000, ttl=15)

BOT_USERNAME = "your_bot_username"

# ==========================================
# ПОДКЛЮЧЕНИЕ БИЗНЕС-БОТА
# ==========================================
async def is_bot_connected(user_id: int) -> bool:
    conn = await connections_collection.find_one({"user_id": user_id})
    return bool(conn)


def get_connect_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть настройки Telegram", url="tg://settings/edit", style="primary")],
        [InlineKeyboardButton(text="✅ Я подключил — проверить", callback_data="check_connection", style="success")]
    ])


def get_connect_text() -> str:
    return (
        "1. Настройки профиля - <a href=\"tg://settings/edit\">tg://settings/edit</a>\n\n"
        "2. «Автоматизация чатов»\n\n"
        f"3. В поиск <code>{BOT_USERNAME}</code> - добавить\n\n"
        "4. Дайте все права на сообщения"
    )


# ==========================================
# ПОКАЗ МЕНЮ С ФОТО
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
# GOOGLE DRIVE
# ==========================================
_drive_service = None
_folder_cache = TTLCache(maxsize=1000, ttl=86400)
drive_lock = asyncio.Lock()
ROOT_FOLDER_NAME = "TelegramArchiveBot"


def _init_drive():
    global _drive_service
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        print("⚠️ Google Drive: ключи не заданы")
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


async def upload_media_bg(archive_id, file_id, media_type, ext, mime, peer_name_raw, peer_id, owner_name, file_size):
    try:
        if file_size and file_size > 20 * 1024 * 1024:
            print(f"⚠️ Файл слишком большой ({file_size} байт), пропускаем загрузку на Диск для {peer_id}")
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
            await bot.download_file(file_obj.file_path, destination=tmp.name)
            tmp_path = tmp.name

        def _sync_upload():
            media = MediaFileUpload(tmp_path, mimetype=mime, resumable=True)
            meta = {"name": filename, "parents": [folder_id]}
            return _drive().files().create(body=meta, media_body=media, fields="id, webViewLink, webContentLink").execute()

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _sync_upload)
            if result:
                await archive_collection.update_one(
                    {"_id": archive_id},
                    {"$set": {"drive_link": result.get("webViewLink"), "drive_file_id": result.get("id")}}
                )
                print(f"✅ Медиа загружено: {media_type} для {peer_id}")
        finally:
            os.remove(tmp_path)
    except Exception as e:
        print(f"upload_media_bg error: {e}")


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
            message.from_user.first_name, peer_id, owner_name, file_size
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
        await bot.send_message(
            chat_id=owner_id,
            text=body,
            parse_mode="HTML",
            disable_web_page_preview=True
        )


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
    "usd": "USDT", "usdt": "USDT",
    "доллар": "USDT", "доллары": "USDT", "долларов": "USDT", "доллара": "USDT",
    "долл": "USDT", "долл.": "USDT",
    "бакс": "USDT", "баксы": "USDT", "баксов": "USDT", "бакса": "USDT",
    "бачей": "USDT", "бач": "USDT", "бач.": "USDT",
    "баксик": "USDT", "баксиков": "USDT", "баксика": "USDT",
    "зеленый": "USDT", "зелёный": "USDT", "зеленых": "USDT", "зелёных": "USDT",
    "зелень": "USDT", "зелени": "USDT",
    "юсд": "USDT", "юсдт": "USDT", "усд": "USDT", "усдт": "USDT",
    "тетер": "USDT", "тетеры": "USDT", "тетеров": "USDT", "тетера": "USDT",
    "тезер": "USDT", "тезеры": "USDT", "тезеров": "USDT", "тезера": "USDT",
    "юста": "USDT", "юст": "USDT",
    "$": "USDT", "💵": "USDT", "💰": "USDT", "💲": "USDT",

    "rub": "RUB", "руб": "RUB", "руб.": "RUB",
    "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "рубля": "RUB", "рублю": "RUB",
    "рубас": "RUB", "рубасов": "RUB", "рубаса": "RUB", "рубасы": "RUB",
    "рубчик": "RUB", "рубчики": "RUB", "рубчиков": "RUB",
    "деревянный": "RUB", "деревянных": "RUB", "деревянные": "RUB",
    "р": "RUB", "р.": "RUB", "₽": "RUB",

    "eur": "EUR", "евро": "EUR", "еврик": "EUR", "еврики": "EUR",
    "евриков": "EUR", "еврика": "EUR", "евра": "EUR", "евров": "EUR",
    "€": "EUR",

    "cny": "CNY", "юань": "CNY", "юани": "CNY", "юаней": "CNY", "юаня": "CNY",
    "юан": "CNY", "yuan": "CNY", "юанчики": "CNY",
    "женьминьби": "CNY", "жэньминьби": "CNY", "¥": "CNY",

    "btc": "BTC", "биткоин": "BTC", "биткоины": "BTC", "биткоинов": "BTC",
    "биткоина": "BTC", "биткойн": "BTC", "биткойны": "BTC", "биткойнов": "BTC",
    "биток": "BTC", "битки": "BTC", "битков": "BTC", "битка": "BTC",
    "бит": "BTC", "биты": "BTC", "битов": "BTC",
    "₿": "BTC",

    "ton": "TON", "тон": "TON", "тона": "TON", "тонов": "TON", "тоны": "TON",
    "тоник": "TON", "тоника": "TON", "тоников": "TON", "тонику": "TON",
    "грам": "TON", "грама": "TON", "грамов": "TON", "граммы": "TON",
    "грамм": "TON", "gram": "TON", "grams": "TON",
    "павлуша": "TON", "дуров": "TON",
    "💎": "TON",

    "stars": "STARS", "star": "STARS",
    "звезда": "STARS", "звёзда": "STARS", "звезды": "STARS", "звёзды": "STARS",
    "звезд": "STARS", "звёзд": "STARS", "звезде": "STARS", "звёзде": "STARS",
    "звездочка": "STARS", "звёздочка": "STARS",
    "звездочки": "STARS", "звёздочки": "STARS",
    "звездочек": "STARS", "звёздочек": "STARS",
    "зв": "STARS", "зв.": "STARS",
    "звездулька": "STARS", "звездульки": "STARS",
    "starчик": "STARS", "стар": "STARS", "стары": "STARS", "старов": "STARS",
    "⭐": "STARS", "🌟": "STARS", "✨": "STARS",

    "eth": "ETH", "эфир": "ETH", "эфира": "ETH", "эфиров": "ETH", "эфиры": "ETH",
    "эфириум": "ETH", "эфирка": "ETH", "эфирки": "ETH", "эфирок": "ETH",
    "эфирчик": "ETH", "эфирчика": "ETH",
    "Ξ": "ETH",

    "kzt": "KZT", "тенге": "KZT", "теньге": "KZT", "теньга": "KZT",
    "тг": "KZT", "тг.": "KZT", "₸": "KZT",

    "uah": "UAH", "гривна": "UAH", "гривны": "UAH", "гривен": "UAH",
    "гривне": "UAH", "гривня": "UAH", "гривню": "UAH",
    "грн": "UAH", "грн.": "UAH", "₴": "UAH",

    "gbp": "GBP", "фунт": "GBP", "фунты": "GBP", "фунтов": "GBP", "фунта": "GBP",
    "стерлинг": "GBP", "стерлинги": "GBP", "стерлингов": "GBP",
    "£": "GBP",

    "jpy": "JPY", "иена": "JPY", "иены": "JPY", "иен": "JPY",
    "йена": "JPY", "йены": "JPY", "йен": "JPY",
}

ALL_CURRENCIES = ["RUB", "USDT", "EUR", "CNY", "KZT", "UAH", "GBP", "JPY", "BTC", "ETH", "TON", "STARS"]
_CURRENCY_ALT = "|".join(sorted([re.escape(a) for a in CURRENCY_ALIASES.keys()], key=len, reverse=True))

WORD_NUMBERS = {
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "сто": 100, "тысяча": 1000,
}

_NUM_WORDS = "|".join(WORD_NUMBERS.keys())
_NUM = rf"(?:\d+[.,]?\d*|{_NUM_WORDS})"
_OPS = r"[+\-*/xх^]"
_WORDS = r"(?:плюс|минус|умножить на|разделить на|поделить на|умножить|разделить|поделить|сложить|вычесть|делить|степень|в степени|х)"

# ИСПОЛЬЗУЕМ re.compile с флагом re.IGNORECASE для игнорирования регистра букв
AUTO_MATH_PATTERN = re.compile(rf"^\s*{_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})+\s*$", re.IGNORECASE)
AUTO_MATH_CURRENCY_PATTERN = re.compile(rf"^\s*({_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})*)\s{{0,3}}({_CURRENCY_ALT})\s*$", re.IGNORECASE)

OPERATORS = {
    "умножить на": "*", "разделить на": "/", "поделить на": "/", "в степени": "**",
    "плюс": "+", "сложить": "+", "минус": "-", "вычесть": "-",
    "умножить": "*", "разделить": "/", "делить": "/", "поделить": "/", "х": "*",
}


def parse_math_expression(text: str) -> str:
    text = text.lower().strip()
    for word, op in sorted(OPERATORS.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", f" {op} ", text)
    words = text.split()
    result = []
    for word in words:
        clean = word.strip(".,!?;:")
        result.append(str(WORD_NUMBERS[clean]) if clean in WORD_NUMBERS else word)
    text = " ".join(result)
    text = re.sub(r"[^\d+\-*/().\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


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
            data = await fetch_json(f"https://api.mexc.com/api/v3/ticker/price?symbol={sym}")
            if data and "price" in data:
                EXCHANGE_CACHE[code] = float(data["price"])
            else:
                cg_id = {"BTC": "bitcoin", "ETH": "ethereum"}.get(code)
                if cg_id:
                    cg_data = await fetch_json(
                        f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
                    )
                    if cg_data and cg_id in cg_data:
                        EXCHANGE_CACHE[code] = float(cg_data[cg_id]["usd"])

        ton_price = None
        for ton_sym in ["TONUSDT", "GRAMUSDT"]:
            data = await fetch_json(f"https://api.mexc.com/api/v3/ticker/price?symbol={ton_sym}")
            if data and "price" in data:
                try:
                    ton_price = float(data["price"])
                    print(f"✅ TON курс с MEXC {ton_sym}: {ton_price}")
                    break
                except (ValueError, TypeError):
                    pass

        if ton_price is None:
            cg_data = await fetch_json(
                "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"
            )
            if cg_data and "the-open-network" in cg_data:
                ton_price = float(cg_data["the-open-network"]["usd"])
                print(f"✅ TON курс с CoinGecko: {ton_price}")

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
    r_from = EXCHANGE_CACHE.get(from_cur)
    r_to = EXCHANGE_CACHE.get(to_cur)
    if r_from is None or r_to is None:
        return None
    return (amount * r_from) / r_to


async def send_currency_conversion(message: Message, amount: float, from_cur: str, original_expr: str = None):
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

    emoji_map = {
        "RUB": "🇷🇺", "USDT": "💵", "EUR": "🇪🇺", "CNY": "🇨🇳", "KZT": "🇰🇿",
        "UAH": "🇺🇦", "GBP": "🇬🇧", "JPY": "🇯🇵", "BTC": "₿", "ETH": "Ξ",
        "TON": "💎", "STARS": "⭐",
    }

    shown = set()
    results_found = 0

    for target in display_currencies:
        if target == "USD":
            target = "USDT"
        if target in shown:
            continue
        if target == from_cur:
            continue
        shown.add(target)

        result = await convert_currency(amount, from_cur, target)
        if result is None:
            continue

        if target in ("BTC", "ETH", "TON"):
            formatted = f"{result:.6f}".rstrip("0").rstrip(".")
        elif target == "STARS":
            formatted = f"{result:.2f}".rstrip("0").rstrip(".")
        elif target in ("RUB", "KZT", "UAH", "JPY"):
            formatted = f"{result:.2f}"
        else:
            formatted = f"{result:.3f}".rstrip("0").rstrip(".")

        if not formatted:
            formatted = "0"

        emoji = emoji_map.get(target, "•")
        lines.append(f"{target} {emoji}: <code>{formatted}</code>")
        results_found += 1

    if results_found == 0:
        lines.append("<i>Нет доступных валют для конвертации</i>")

    with suppress(Exception):
        await message.reply("\n".join(lines), parse_mode="HTML")


# ==========================================
# ФИЛЬТРЫ И СОСТОЯНИЯ
# ==========================================
class ReplyHasMedia(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        r = message.reply_to_message
        if not r:
            return False
        return bool(r.photo or r.video or r.video_note or r.animation or r.document or r.audio or r.voice)


class UserStates(StatesGroup):
    waiting_for_afk_text = State()
    waiting_for_afk_time = State()


def check_auto_afk(start_h: int, end_h: int) -> bool:
    now = datetime.now(timezone.utc)
    local_hour = (now.hour + 3) % 24
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
    global BOT_USERNAME

    with suppress(Exception):
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
    with suppress(Exception):
        await archive_collection.create_index([("owner_id", 1), ("conn_id", 1), ("chat_id", 1), ("created_at", -1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("message_id", 1)])
        await archive_collection.create_index([("user_id", 1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("is_owner", 1), ("created_at", -1)])
    with suppress(Exception):
        await deleted_users_collection.create_index([("conn_id", 1), ("chat_id", 1)])
    with suppress(Exception):
        await mutes_collection.create_index([("mute_key", 1)], unique=True)

    try:
        stored_mutes = await mutes_collection.find({}).to_list(length=None)
        for m in stored_mutes:
            muted_chats.add(m["mute_key"])
        print(f"✅ Загружено активных мутов из базы: {len(muted_chats)}")
    except Exception as e:
        print(f"Ошибка загрузки мутов: {e}")

    asyncio.create_task(update_rates_loop())
    _init_drive()

    with suppress(Exception):
        me = await bot.get_me()
        BOT_USERNAME = me.username or "your_bot_username"
        print(f"✅ Бот: @{BOT_USERNAME}")

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

    if is_afk:
        afk_btn = InlineKeyboardButton(
            text="🟢 Автоответчик — ВКЛЮЧЕН",
            callback_data="afk_menu",
            style="success"
        )
    else:
        afk_btn = InlineKeyboardButton(
            text="🔴 Автоответчик — ВЫКЛЮЧЕН",
            callback_data="afk_menu",
            style="danger"
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds", style="primary")],
        [
            InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes", style="primary"),
            InlineKeyboardButton(text="🗑 Удалённые и изменения", callback_data="deleted_menu", style="primary"),
        ],
        [
            InlineKeyboardButton(text="👤 Мой статус", callback_data="user_status", style="primary"),
            InlineKeyboardButton(text="💱 Валюты", callback_data="currency_settings", style="primary"),
        ],
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

    if message.from_user.id == SUPERADMIN_ID:
        kb = await get_user_main_kb(message.from_user.id)
        text = "👋 <b>Твой личный бот-секретарь.</b>\nУправляй настройками ниже:"
        try:
            await message.answer_photo(photo=MENU_PHOTO, caption=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    if not await is_bot_connected(message.from_user.id):
        try:
            await message.answer_photo(photo=MENU_PHOTO, caption=get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        except Exception:
            await message.answer(get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        return

    if not await has_profile_access(message.from_user.id):
        try:
            await message.answer_photo(photo=MENU_PHOTO, caption=get_profile_access_text(), reply_markup=get_profile_kb(), parse_mode="HTML")
        except Exception:
            await message.answer(get_profile_access_text(), reply_markup=get_profile_kb(), parse_mode="HTML")
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

    if not await has_profile_access(call.from_user.id):
        await call.answer("⚠️ Нужен доступ к профилю", show_alert=True)
        with suppress(Exception):
            await call.message.answer(
                get_profile_access_text(),
                reply_markup=get_profile_kb(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        return

    await call.answer("✅ Подключено!", show_alert=True)
    kb = await get_user_main_kb(call.from_user.id)
    await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "check_profile")
async def check_profile_handler(call: CallbackQuery):
    if await has_profile_access(call.from_user.id):
        await call.answer("✅ Доступ к профилю получен!", show_alert=True)
        kb = await get_user_main_kb(call.from_user.id)
        await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)
    else:
        await call.answer(
            "❌ Доступ ещё не выдан.\n\n"
            "Включи «Управление профилем» в настройках Автоматизации чатов",
            show_alert=True
        )


@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()

    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            kb = get_connect_kb()
            await show_menu(call, get_connect_text(), kb, with_photo=True)
            return
        if not await has_profile_access(call.from_user.id):
            kb = get_profile_kb()
            await show_menu(call, get_profile_access_text(), kb, with_photo=True)
            return

    kb = await get_user_main_kb(call.from_user.id)
    await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)


# ==========================================
# МЕНЮ УДАЛЁНОК
# ==========================================
@dp.callback_query(F.data == "deleted_menu")
async def deleted_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
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
        builder.button(text="🔙 Назад", callback_data="user_main", style="primary")
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

    builder.button(text="🔙 Назад", callback_data="user_main", style="primary")
    builder.adjust(1)
    await show_menu(call, "🗑 <b>Собеседники (удалённые / изменения):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("delchat|"))
async def deleted_chat_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
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
        builder.button(text=f"📋 Все удалённые ({del_total})", callback_data=f"delshow|{conn_id}|{chat_id}|del|0", style="primary")
    if edit_total:
        builder.button(text=f"✏️ Все изменения ({edit_total})", callback_data=f"delshow|{conn_id}|{chat_id}|edit|0", style="primary")
    if del_total + edit_total > 30:
        builder.button(text="📥 Скачать архивом", callback_data=f"deldl|{conn_id}|{chat_id}", style="primary")
    builder.button(text="🔙 Назад", callback_data="deleted_menu", style="primary")
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
            callback_data=f"delshow|{conn_id}|{chat_id}|{mode}|{offset + shown}",
            style="primary"
        )
    builder.button(text="🔙 К собеседнику", callback_data=f"delchat|{conn_id}|{chat_id}", style="primary")
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
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats", style="primary")],
        [InlineKeyboardButton(text="💼 Мои твинки", callback_data="admin_twinks", style="primary")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes", style="primary")],
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
        builder.button(text="🔙 Назад", callback_data="admin_main", style="primary")
        await show_menu(call, "Нет подключённых аккаунтов.", builder.as_markup(), with_photo=True)
        return

    for c in conns:
        name = c.get("first_name", "Без имени")
        uname = f"@{c.get('username', '')}" if c.get("username") else ""
        conn_id = c["business_connection_id"]
        count = await archive_collection.count_documents({"conn_id": conn_id})
        label = f"💼 {name} {uname} ({count})"
        builder.button(text=label[:60], callback_data=f"twink|{conn_id}")
    builder.button(text="🔙 Назад", callback_data="admin_main", style="primary")
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
        builder.button(text="🔙 Назад", callback_data="admin_twinks", style="primary")
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
    builder.button(text="🔙 Назад", callback_data="admin_twinks", style="primary")
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
    builder.button(text="🔙 Назад", callback_data="admin_main", style="primary")

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
    if not muted_chats:
        builder.button(text="🔙 Назад", callback_data="admin_main", style="primary")
        await show_menu(call, "Активных мутов нет.", builder.as_markup(), with_photo=True)
        return
    for mute in list(muted_chats):
        try:
            conn, chat = mute.rsplit("_", 1)
            builder.button(text=f"Снять: {chat}", callback_data=f"forceunmute|{mute}", style="primary")
        except Exception:
            continue
    builder.button(text="🔙 Назад", callback_data="admin_main", style="primary")
    builder.adjust(1)
    await show_menu(call, "🔇 <b>Активные муты:</b>", builder.as_markup(), with_photo=True)


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
    builder.button(text="🔙 К мутам", callback_data="admin_mutes", style="primary")

    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})

    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await show_menu(call, "✅ Мут снят.", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "Мут уже снят.", builder.as_markup(), with_photo=True)


# ==========================================
# АВТООТВЕТЧИК
# ==========================================
@dp.callback_query(F.data == "afk_menu")
async def afk_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            kb = get_connect_kb()
            await show_menu(call, get_connect_text(), kb, with_photo=True)
            return

    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    is_afk = user_data.get("is_afk", False)

    if is_afk:
        toggle_btn = InlineKeyboardButton(
            text="🟢 Выключить автоответчик",
            callback_data="toggle_afk",
            style="success"
        )
    else:
        toggle_btn = InlineKeyboardButton(
            text="🔴 Включить автоответчик",
            callback_data="toggle_afk",
            style="danger"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [toggle_btn],
        [InlineKeyboardButton(text="⚙️ Настройки автоответчика", callback_data="afk_settings", style="primary")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main", style="primary")],
    ])

    state = "🟢 ВКЛЮЧЕН" if is_afk else "🔴 ВЫКЛЮЧЕН"
    await show_menu(
        call,
        f"💤 <b>Автоответчик</b>\n\nТекущий статус: <b>{state}</b>",
        kb, with_photo=True
    )


async def get_afk_settings_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    auto_afk = user_data.get("auto_afk", False)
    start_h = user_data.get("afk_start", 23)
    end_h = user_data.get("afk_end", 7)
    auto_status = "ВКЛ" if auto_afk else "ВЫКЛ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="afk_set_text", style="primary")],
        [InlineKeyboardButton(text=f"🕒 Авто-включение: {auto_status}", callback_data="toggle_auto_afk", style="primary")],
        [InlineKeyboardButton(text=f"⏰ Время: {start_h}:00 - {end_h}:00", callback_data="afk_set_time", style="primary")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="afk_menu", style="primary")],
    ])


@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            kb = get_connect_kb()
            await show_menu(call, get_connect_text(), kb, with_photo=True)
            return
    kb = await get_afk_settings_kb(call.from_user.id)
    await show_menu(call, "⚙️ <b>Настройки автоответчика:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
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
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("auto_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": new_status}}, upsert=True)
    kb = await get_afk_settings_kb(call.from_user.id)
    await show_menu(call, "⚙️ <b>Настройки автоответчика:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings", style="primary")
    await show_menu(
        call,
        f"Текущий текст:\n<i>{html.escape(current)}</i>\n\nОтправь новый:",
        builder.as_markup(), with_photo=True
    )
    await state.set_state(UserStates.waiting_for_afk_text)


@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Сохранено!", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            return
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings", style="primary")
    await show_menu(
        call,
        "Формат: <code>23 7</code> (с 23:00 до 07:00)",
        builder.as_markup(), with_photo=True
    )
    await state.set_state(UserStates.waiting_for_afk_time)


@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
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


# ==========================================
# ВАЛЮТЫ
# ==========================================
@dp.callback_query(F.data == "currency_settings")
async def currency_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            kb = get_connect_kb()
            await show_menu(call, get_connect_text(), kb, with_photo=True)
            return

    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])

    builder = InlineKeyboardBuilder()
    for cur in ALL_CURRENCIES:
        if cur in display:
            builder.button(
                text=f"✅ {cur}",
                callback_data=f"cur_toggle|{cur}",
                style="success"
            )
        else:
            builder.button(
                text=cur,
                callback_data=f"cur_toggle|{cur}"
            )

    builder.button(text="🔙 Назад", callback_data="user_main", style="primary")
    builder.adjust(2)
    await show_menu(call, "💱 <b>Выбери валюты:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("cur_toggle|"))
async def currency_toggle_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
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
# МУТЫ
# ==========================================
@dp.callback_query(F.data == "user_mutes")
async def user_mutes_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            kb = get_connect_kb()
            await show_menu(call, get_connect_text(), kb, with_photo=True)
            return
    user_conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in user_conns]
    builder = InlineKeyboardBuilder()
    has_mutes = False
    for mute in list(muted_chats):
        try:
            conn, chat = mute.rsplit("_", 1)
            if conn in conn_ids:
                has_mutes = True
                builder.button(text=f"Снять мут: {chat}", callback_data=f"u_unmute|{mute}", style="primary")
        except Exception:
            continue
    builder.button(text="🔙 Назад", callback_data="user_main", style="primary")
    builder.adjust(1)
    if not has_mutes:
        await show_menu(call, "Нет мутов.", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "🔇 <b>Муты:</b>", builder.as_markup(), with_photo=True)


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
        muted_chats.remove(mute_key)
        await call.answer("✅ Снят!", show_alert=True)
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
    afk_text = user_data.get("afk_text", "—")
    display_cur = user_data.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])

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

    text = "\n".join(lines)

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main", style="primary")

    await show_menu(call, text, builder.as_markup(), with_photo=True)


# ==========================================
# ПОМОЩЬ
# ==========================================
@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        if not await is_bot_connected(call.from_user.id):
            return

    text = (
        "📖 <b>Команды бота</b>\n"
        "<i>Все команды пишутся в бизнес-чатах с префиксом точки</i>\n\n"

        "<b>🚫 .мут</b>\n"
        "Мутит собеседника — все его следующие сообщения будут удаляться\n"
        "Пример: <code>.мут</code>\n\n"

        "<b>🔁 .амут [текст]</b>\n"
        "Антимут. Отправляет сообщение и переотправляет его 10 секунд при удалении мут-ботом\n"
        "Пример: <code>.амут привет</code>\n\n"

        "<b>💣 .[N] [текст]</b>\n"
        "Спам. Отправляет текст N раз (максимум 50)\n"
        "Пример: <code>.10 привет</code>\n\n"

        "<b>🎭 .п1 [текст]</b>\n"
        "Анимация печати — текст появляется посимвольно\n"
        "Пример: <code>.п1 привет мир</code>\n\n"

        "<b>⌨️ .п2 [текст]</b>\n"
        "Анимация печати с курсором в конце\n"
        "Пример: <code>.п2 привет</code>\n\n"

        "<b>🌀 .п3 [текст]</b>\n"
        "Анимация с рандомными буквами — текст «собирается» постепенно\n"
        "Пример: <code>.п3 секретное сообщение</code>\n\n"

        "<b>🧮 Математика</b>\n"
        "Бот автоматически считает простые выражения\n"
        "Пример: <code>5+3</code> → <code>8</code>\n"
        "Пример: <code>10 умножить на 5</code> → <code>50</code>\n\n"

        "<b>💱 Валюты</b>\n"
        "Конвертация валют по актуальному курсу\n"
        "Пример: <code>100 баксов</code>\n"
        "Пример: <code>5 баксов + 10 евро</code>\n\n"

        "<b>🎁 Подарки Telegram</b>\n"
        "Кидай ссылку — бот покажет модель, фон, floor и офферы\n"
        "Пример: <code>t.me/nft/PlushPepe-274</code>\n\n"

        "<i>💡 Совет: зажми сообщение с командой и скопируй её себе</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main", style="primary")
    await show_menu(call, text, builder.as_markup(), with_photo=True)


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


@dp.business_message(ReplyHasMedia())
async def auto_save_replied_media(message: Message):
    if message.from_user.id == message.chat.id:
        return
    reply = message.reply_to_message
    if not reply:
        return
    if not getattr(reply, "has_protected_content", False):
        return

    file_id, filename = None, "saved"
    if reply.photo: file_id, filename = reply.photo[-1].file_id, "saved.jpg"
    elif reply.video: file_id, filename = reply.video.file_id, "saved.mp4"
    elif reply.video_note: file_id, filename = reply.video_note.file_id, "saved_note.mp4"
    elif reply.animation: file_id, filename = reply.animation.file_id, "saved.mp4"
    elif reply.document: file_id, filename = reply.document.file_id, reply.document.file_name or "saved.bin"
    if not file_id:
        return

    owner_id = message.from_user.id
    try:
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        input_file = BufferedInputFile(buffer.read(), filename=filename)
        await bot.send_document(chat_id=owner_id, document=input_file, caption="🕵️ Сохранено")
    except Exception as e:
        print(f"save_replied_media error: {e}")


# ==========================================
# ВЕЧНЫЙ МУТ
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

    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=conn_id,
            message_ids=[message.message_id]
        )

    with suppress(Exception):
        await mutes_collection.update_one(
            {"mute_key": mute_key},
            {"$set": {
                "mute_key": mute_key,
                "conn_id": conn_id,
                "peer_id": peer_id,
                "owner_id": owner_id,
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

    if mute_key in muted_chats:
        return

    muted_chats.add(mute_key)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute|{peer_id}", style="danger")]])
    with suppress(Exception):
        await bot.send_message(
            chat_id=peer_id,
            text="🔇 Собеседник замьючен.",
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

    text = message.text[5:].strip()
    if not text:
        return

    peer_id = message.chat.id

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

    key = f"{conn_id}_{peer_id}"
    active_amute[key] = {
        "text": text,
        "message_id": sent.message_id,
    }
    print(f"🔁 Амут-сессия на 15с: {key} → {text[:50]}")


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
    count = min(int(match.group(1)), 50)
    peer_id = message.chat.id
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    for _ in range(count):
        with suppress(Exception):
            await bot.send_message(chat_id=peer_id, text=match.group(2), business_connection_id=conn_id)
        await asyncio.sleep(0.5)


@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    peer_id = message.chat.id
    sent_msg = await bot.send_message(chat_id=peer_id, text=full_text[0], business_connection_id=conn_id)
    if not sent_msg:
        return
    current = full_text[0]
    for char in full_text[1:]:
        current += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current, business_connection_id=conn_id)


@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    peer_id = message.chat.id
    sent_msg = await bot.send_message(chat_id=peer_id, text=full_text[0] + "▌", business_connection_id=conn_id)
    if not sent_msg:
        return
    current = full_text[0]
    for char in full_text[1:]:
        current += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current + "▌", business_connection_id=conn_id)
    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current, business_connection_id=conn_id)


@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    peer_id = message.chat.id
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"
    sent_msg = await bot.send_message(chat_id=peer_id, text="...", business_connection_id=conn_id)
    if not sent_msg:
        return
    for i in range(len(full_text) + 1):
        await asyncio.sleep(0.2)
        correct = full_text[:i]
        rnd = "".join(random.choice(alphabet) for _ in range(len(full_text) - i))
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=correct + rnd, business_connection_id=conn_id)


# ==========================================
# ПАРСЕР ПОДАРКОВ
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
        if owner_data and message.from_user.id == owner_data["user_id"]:
            return

    loading_msg = None
    with suppress(Exception):
        loading_msg = await bot.send_message(chat_id=message.chat.id, text="🔍 Анализирую...", business_connection_id=conn_id)
    data = await get_gift_card_data(slug)
    result_text = format_gift_card(data)
    if loading_msg:
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                text=result_text,
                parse_mode="HTML",
                business_connection_id=conn_id
            )


# ==========================================
# МАТЕМАТИКА
# ==========================================
@dp.business_message(F.text.regexp(AUTO_MATH_CURRENCY_PATTERN) | F.text.regexp(AUTO_MATH_PATTERN))
async def auto_math_and_currency(message: Message):
    if len(message.text) > 150:
        return

    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id == owner_id:
        return
    if message.text.lstrip().startswith("."):
        return

    peer_id = message.chat.id
    match_curr = AUTO_MATH_CURRENCY_PATTERN.match(message.text)
    if match_curr:
        math_expr = match_curr.group(1).strip()
        tail = match_curr.group(2).lower()
        code = CURRENCY_ALIASES.get(tail)
        if not code:
            return
        if math_expr:
            # Переводим текст в нижний регистр для расчетов (чтобы искать "Два")
            raw = normalize_math_input(math_expr.lower())
            parsed = parse_math_expression(raw)
            try:
                amount = float(simple_eval(parsed))
                formatted_expr = format_math_expression(parsed)
                await send_currency_conversion(message, amount, code, original_expr=formatted_expr if len(parsed.split()) > 1 else None)
            except Exception:
                return
    else:
        # Аналогично переводим в нижний регистр для обычной математики
        raw = normalize_math_input(message.text.lower())
        parsed = parse_math_expression(raw)
        if not parsed or len(parsed.split()) < 3:
            return
        try:
            result = simple_eval(parsed)
            if isinstance(result, float):
                result = int(result) if round(result, 4).is_integer() else round(result, 4)
        except Exception:
            return
        formatted = format_math_expression(parsed)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            await bot.send_message(chat_id=peer_id, text=f"{formatted} = {result}", business_connection_id=conn_id)


# ==========================================
# ГЛАВНЫЙ ОБРАБОТЧИК
# ==========================================
@dp.business_message()
async def handle_messages(message: Message):
    conn_id = message.business_connection_id

    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return

    owner_id = owner_data["user_id"]
    owner_name = owner_data.get("first_name", "owner")
    peer_id = message.chat.id

    is_owner = (message.from_user.id == owner_id)

    if not is_owner:
        with suppress(Exception):
            await messages_collection.insert_one({
                "business_connection_id": conn_id,
                "message_id": message.message_id,
                "chat_id": peer_id,
                "user_id": message.from_user.id,
                "username": message.from_user.username or "",
                "first_name": message.from_user.first_name or "Без имени",
                "text": message.text or message.caption or "[Без текста]",
                "created_at": datetime.now(timezone.utc)
            })

    with suppress(Exception):
        await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)

    if is_owner:
        owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
        manual_afk = owner_settings.get("is_afk", False)
        in_schedule = check_auto_afk(owner_settings.get("afk_start", 23), owner_settings.get("afk_end", 7)) if owner_settings.get("auto_afk", False) else False

        if manual_afk or in_schedule:
            if (owner_id, peer_id) not in afk_cooldowns:
                afk_text = owner_settings.get("afk_text", "Владелец сейчас занят. 💤")
                with suppress(Exception):
                    await bot.send_message(chat_id=peer_id, text=afk_text, business_connection_id=conn_id)
                afk_cooldowns[(owner_id, peer_id)] = True
        return

    mute_key = f"{conn_id}_{peer_id}"
    if mute_key in muted_chats:
        # УДАЛЕНИЕ ДЛЯ МУТА: Игнорируем владельца и САМОГО БОТА (чтобы не удалять ответы бота)
        # но удаляем любые другие сообщения, включая сторонних юзерботов собеседника!
        if message.from_user.id != owner_id and message.from_user.id != bot.id:
            with suppress(Exception):
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])


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

    with suppress(Exception):
        await messages_collection.update_one(
            {"business_connection_id": conn_id, "message_id": message.message_id, "chat_id": peer_id},
            {"$set": {"text": new_text}}
        )

    if not is_owner:
        sender_name = old_doc.get("first_name") or message.from_user.first_name or "Неизвестный"
        sender_username = old_doc.get("username") or message.from_user.username or ""
        await notify_owner_about_edit(owner_id, sender_name, sender_username, old_text, new_text, edited_at)

        with suppress(Exception):
            await deleted_users_collection.update_one(
                {"conn_id": conn_id, "chat_id": peer_id},
                {
                    "$set": {
                        "conn_id": conn_id,
                        "chat_id": peer_id,
                        "owner_id": owner_id,
                        "user_id": message.from_user.id,
                        "first_name": sender_name,
                        "username": sender_username,
                        "last_edited_at": edited_at
                    },
                    "$inc": {"edit_count": 1}
                },
                upsert=True
            )


@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    peer_id = deleted.chat.id

    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]

    for msg_id in deleted.message_ids:
        key = f"{conn_id}_{peer_id}"
        session = active_amute.get(key)
        if session and session.get("message_id") == msg_id:
            new_sent = None
            with suppress(Exception):
                new_sent = await bot.send_message(
                    chat_id=peer_id,
                    text=session["text"],
                    business_connection_id=conn_id
                )
            if new_sent:
                session["message_id"] = new_sent.message_id
                print(f"🔁 Амут: переотправлено в {key}")
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
                sender_name = old_msg.get("first_name") or "Неизвестный"
                sender_username = old_msg.get("username", "")
                text = old_msg.get("text") or ""
                media_type = old_msg.get("media_type")
                drive_link = old_msg.get("drive_link")
                file_id = old_msg.get("media_file_id")

                await notify_owner_about_deletion(
                    owner_id, sender_name, sender_username,
                    text, media_type, drive_link, deleted_at,
                    file_id=file_id
                )

                with suppress(Exception):
                    await deleted_users_collection.update_one(
                        {"conn_id": conn_id, "chat_id": peer_id},
                        {
                            "$set": {
                                "conn_id": conn_id,
                                "chat_id": peer_id,
                                "owner_id": owner_id,
                                "user_id": old_msg.get("user_id"),
                                "first_name": old_msg.get("first_name", "Без имени"),
                                "username": old_msg.get("username", ""),
                                "last_deleted_at": deleted_at
                            },
                            "$inc": {"delete_count": 1}
                        },
                        upsert=True
                    )


# ==========================================
# СНЯТИЕ МУТА
# ==========================================
@dp.callback_query(F.data.startswith("unmute|"))
async def unmute_user(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    try:
        peer_id = int(parts[1])
    except ValueError:
        return
    conn_id = call.message.business_connection_id
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

    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
    with suppress(TelegramBadRequest):
        await call.message.edit_text("мут снят")
        await call.answer("ok")


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
