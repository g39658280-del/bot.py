import os
import io
import asyncio
import random
import html
import re
import aiohttp
import tempfile
from datetime import datetime, timezone
from contextlib import suppress
from aiogram import Bot
from motor.motor_asyncio import AsyncIOMotorClient
from simpleeval import simple_eval
from cachetools import TTLCache
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==========================================
# КОНФИГ
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")
SUPERADMIN_ID = 6548121776

GIFT_SATELLITE_TOKEN = os.environ.get("GIFT_SATELLITE_TOKEN", "f5fca5357f0721d8f95fed1a3874b60ae097ee7500eb2a2e41cbfc74cc201683")
GIFT_SATELLITE_BASE = "https://gift-satellite.dev/api"
NFTMATCH_API_KEY = os.environ.get("NFTMATCH_API_KEY", "")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

MENU_PHOTO = "https://files.catbox.moe/bph5iz.webp"
CAPTION_LIMIT = 1024

bot = Bot(token=BOT_TOKEN)
BOT_ID: int | None = None
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
active_amute = TTLCache(maxsize=1000, ttl=15)
action_dedup = TTLCache(maxsize=2000, ttl=4)
self_destruct_ids = TTLCache(maxsize=1000, ttl=30)
active_fakes: dict[str, asyncio.Task] = {}
copied_messages_cache = {} 
bot_formatted_ids = TTLCache(maxsize=3000, ttl=60)

# ==========================================
# УТИЛИТЫ
# ==========================================
def parse_mute_duration(text: str) -> tuple[int | None, str]:
    rest = text[4:].strip().lower()
    if not rest: return None, "навсегда"
    match = re.match(r"^(\d+)\s*(с|сек|s|м|мин|m|ч|час|h|д|дн|d)?", rest)
    if not match: return None, "навсегда"
    val, unit = int(match.group(1)), (match.group(2) or "м")
    if val <= 0: return None, "навсегда"
    if unit.startswith(("с", "s")): return val, f"{val} сек."
    if unit.startswith(("ч", "h")): return val * 3600, f"{val} ч."
    if unit.startswith(("д", "d")): return val * 86400, f"{val} дн."
    return val * 60, f"{val} мин."

def format_remaining_mute(until_ts: float | None) -> str:
    if until_ts is None: return "навсегда"
    rem = int(until_ts - datetime.now(timezone.utc).timestamp())
    if rem <= 0: return "истёк"
    if rem < 60: return f"{rem} сек."
    if rem < 3600: return f"{rem // 60} мин."
    if rem < 86400: return f"{rem // 3600} ч. {(rem % 3600) // 60} мин."
    return f"{rem // 86400} дн."

async def is_chat_muted(conn_id: str, peer_id: int) -> bool:
    mute_key = f"{conn_id}_{peer_id}"
    if mute_key not in muted_chats: return False
    until_ts = muted_chats[mute_key]
    if until_ts is not None and datetime.now(timezone.utc).timestamp() >= until_ts:
        muted_chats.pop(mute_key, None)
        with suppress(Exception): await mutes_collection.delete_one({"mute_key": mute_key})
        return False
    return True

async def handle_owner_activity(owner_id: int, peer_id: int):
    afk_cooldowns[(owner_id, peer_id)] = True
    try:
        result = await users_collection.update_one({"user_id": owner_id, "is_afk": True}, {"$set": {"is_afk": False}})
        if result.modified_count > 0:
            with suppress(Exception):
                await bot.send_message(owner_id, "🌅 <b>Автоответчик автоматически выключен</b>, так как вы написали сообщение в чате.", parse_mode="HTML")
    except Exception as e: print(f"handle_owner_activity error: {e}")

async def is_bot_connected(user_id: int) -> bool:
    return bool(await connections_collection.find_one({"user_id": user_id}))

def get_connect_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть настройки Telegram", url="tg://settings/edit")],
        [InlineKeyboardButton(text="✅ Я подключил — проверить", callback_data="check_connection")]
    ])

def get_connect_text() -> str:
    return f"1. Настройки профиля - <a href=\"tg://settings/edit\">tg://settings/edit</a>\n\n2. «Автоматизация чатов»\n\n3. В поиск <code>{BOT_USERNAME}</code> - добавить\n\n4. Дайте все права на сообщения"

async def show_menu(call, caption: str, kb, with_photo: bool = True):
    msg = call.message
    if len(caption) > CAPTION_LIMIT: with_photo = False
    try:
        if with_photo:
            if msg.photo: await msg.edit_caption(caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                with suppress(Exception): await msg.delete()
                await bot.send_photo(call.from_user.id, photo=MENU_PHOTO, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            if msg.photo:
                with suppress(Exception): await msg.delete()
                await bot.send_message(call.from_user.id, text=caption, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
            else:
                with suppress(Exception): await msg.edit_text(caption, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e: print(f"show_menu error: {e}")

def apply_format(text: str, mode: list) -> str:
    if not mode or not isinstance(mode, list): return text
    def _do(t: str) -> str:
        r = t.upper() if "caps" in mode else t
        r = html.escape(r)
        if "bold" in mode: r = f"<b>{r}</b>"
        if "italic" in mode: r = f"<i>{r}</i>"
        return r
    result = _do(text)
    if len(result) <= 4096: return result
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(_do(text[:mid])) <= 4096: low = mid
        else: high = mid - 1
    return _do(text[:low])

def format_mode_label(mode: list) -> str:
    if not mode: return "ВЫКЛ"
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
        print("⚠️ Google Drive: ключи не заданы")
        return None
    try:
        creds = Credentials(token=None, refresh_token=GOOGLE_REFRESH_TOKEN, token_uri="https://oauth2.googleapis.com/token", client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET, scopes=["https://www.googleapis.com/auth/drive.file"])
        creds.refresh(Request())
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        print("✅ Google Drive подключён")
        return _drive_service
    except Exception as e:
        print(f"❌ Google Drive init error: {e}")
        return None

def _find_folder(name: str, parent_id: str | None) -> str | None:
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id: q += f" and '{parent_id}' in parents"
    try:
        result = _drive_service.files().list(q=q, fields="files(id, name)", pageSize=1).execute()
        files = result.get("files", [])
        return files[0]["id"] if files else None
    except: return None

def _create_folder(name: str, parent_id: str | None) -> str:
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id: meta["parents"] = [parent_id]
    return _drive_service.files().create(body=meta, fields="id").execute()["id"]

def get_or_create_folder(path: str) -> str | None:
    if not _drive_service: return None
    if path in _folder_cache: return _folder_cache[path]
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
        return await asyncio.get_running_loop().run_in_executor(None, get_or_create_folder, path)

def _sanitize(name: str) -> str: return re.sub(r'[\\/:*?"<>|]', "_", (name or "unknown"))[:80]

# ==========================================
# АРХИВАЦИЯ
# ==========================================
_peer_cache = TTLCache(maxsize=5000, ttl=86400)

async def get_peer_info(conn_id: str, peer_id: int) -> dict:
    key = (conn_id, peer_id)
    if key in _peer_cache: return _peer_cache[key]
    doc = await archive_collection.find_one({"conn_id": conn_id, "chat_id": peer_id, "is_owner": False}, sort=[("created_at", -1)])
    if doc:
        info = {"first_name": doc.get("first_name") or "Без имени", "username": doc.get("username") or ""}
        _peer_cache[key] = info
        return info
    try:
        chat = await bot.get_chat(peer_id)
        info = {"first_name": chat.first_name or "Без имени", "username": chat.username or ""}
    except: info = {"first_name": "Без имени", "username": ""}
    _peer_cache[key] = info
    return info

def _extract_media_info(message):
    if message.photo: return "photo", message.photo[-1].file_id, "jpg", "image/jpeg", message.photo[-1].file_size
    if message.video: return "video", message.video.file_id, "mp4", "video/mp4", message.video.file_size
    if message.video_note: return "video_note", message.video_note.file_id, "mp4", "video/mp4", message.video_note.file_size
    if message.animation: return "animation", message.animation.file_id, "mp4", "video/mp4", message.animation.file_size
    if message.document:
        fname = message.document.file_name or "file"
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "bin"
        return "document", message.document.file_id, ext, message.document.mime_type or "application/octet-stream", message.document.file_size
    if message.audio: return "audio", message.audio.file_id, "mp3", message.audio.mime_type or "audio/mpeg", message.audio.file_size
    if message.voice: return "voice", message.voice.file_id, "ogg", "audio/ogg", message.voice.file_size
    if message.sticker: return "sticker", message.sticker.file_id, "webp", "image/webp", message.sticker.file_size
    return None, None, None, None, None

async def check_and_save_protected_media(message, owner_id: int):
    reply = message.reply_to_message
    if not reply or not getattr(reply, "has_protected_content", False): return
    file_id, filename = None, "saved"
    if reply.photo: file_id, filename = reply.photo[-1].file_id, "saved.jpg"
    elif reply.video: file_id, filename = reply.video.file_id, "saved.mp4"
    elif reply.video_note: file_id, filename = reply.video_note.file_id, "saved_note.mp4"
    elif reply.animation: file_id, filename = reply.animation.file_id, "saved.mp4"
    elif reply.voice: file_id, filename = reply.voice.file_id, "saved_voice.ogg"
    elif reply.audio: file_id, filename = reply.audio.file_id, reply.audio.file_name or "saved_audio.mp3"
    elif reply.document: file_id, filename = reply.document.file_id, reply.document.file_name or "saved.bin"
    if not file_id: return
    try:
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        from aiogram.types import BufferedInputFile
        await bot.send_document(owner_id, BufferedInputFile(buffer.read(), filename=filename), caption="🕵️ Сохранено из исчезающего сообщения")
    except Exception as e: print(f"save_media error: {e}")

async def upload_media_bg(archive_id, file_id, media_type, ext, mime, peer_name_raw, peer_id, owner_name, file_size):
    tmp_path = None
    try:
        if file_size and file_size > 20 * 1024 * 1024: return
        folder_path = f"{ROOT_FOLDER_NAME}/{_sanitize(owner_name)}/{_sanitize(peer_name_raw or f'peer_{peer_id}')} ({peer_id})/media"
        folder_id = await async_get_or_create_folder(folder_path)
        if not folder_id: return
        file_obj = await bot.get_file(file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp: tmp_path = tmp.name
        await bot.download_file(file_obj.file_path, destination=tmp_path)
        def _sync_upload():
            media = MediaFileUpload(tmp_path, mimetype=mime, resumable=True)
            return _drive_service.files().create(body={"name": f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{media_type}.{ext}", "parents": [folder_id]}, media_body=media, fields="id, webViewLink").execute()
        result = await asyncio.get_running_loop().run_in_executor(None, _sync_upload)
        if result: await archive_collection.update_one({"_id": archive_id}, {"$set": {"drive_link": result.get("webViewLink"), "drive_file_id": result.get("id")}})
    except Exception as e: print(f"❌ upload_media_bg error: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)

async def save_message_quick(message, conn_id: str, peer_id: int, owner_id: int, owner_name: str, is_owner: bool):
    text = message.text or message.caption or ""
    media_type, file_id, ext, mime, file_size = _extract_media_info(message)
    peer_info = await get_peer_info(conn_id, peer_id) if is_owner else {"first_name": message.from_user.first_name or "Без имени", "username": message.from_user.username or ""}
    if not is_owner: _peer_cache[(conn_id, peer_id)] = peer_info
    doc = {
        "conn_id": conn_id, "chat_id": peer_id, "owner_id": owner_id, "message_id": message.message_id,
        "user_id": message.from_user.id, "first_name": message.from_user.first_name or "Без имени",
        "username": message.from_user.username or "", "peer_first_name": peer_info["first_name"],
        "peer_username": peer_info["username"], "text": text, "is_owner": is_owner,
        "media_type": media_type, "media_file_id": file_id, "drive_link": None, "drive_file_id": None,
        "is_deleted": False, "is_edited": False, "old_text": None, "is_viewed": False, "created_at": datetime.now(timezone.utc),
    }
    try:
        result = await archive_collection.insert_one(doc)
        doc["_id"] = result.inserted_id
    except Exception as e:
        print(f"insert error: {e}")
        return None
    if media_type and file_id:
        asyncio.create_task(upload_media_bg(doc["_id"], file_id, media_type, ext, mime, peer_info["first_name"], peer_id, owner_name, file_size))
    return doc

async def notify_owner_about_deletion(owner_id, sender_name, sender_username, text, media_type=None, drive_link=None, deleted_at=None, file_id=None):
    time_str = deleted_at.strftime("%d.%m %H:%M") if deleted_at else datetime.now().strftime("%d.%m %H:%M")
    uname = f" @{sender_username}" if sender_username else ""
    parts = [f"🗑 <b>Удалено сообщение</b>\n👤 От: <b>{html.escape(sender_name)}</b>{html.escape(uname)}\n🕐 {time_str}"]
    if text: parts.append(f"\n💬 {html.escape(text[:500])}")
    if media_type: parts.append(f"\n📎 [{media_type}] — файл удалён")
    with suppress(Exception): await bot.send_message(owner_id, "\n".join(parts), parse_mode="HTML")

async def notify_owner_about_edit(owner_id, sender_name, sender_username, old_text, new_text, edited_at=None):
    time_str = edited_at.strftime("%d.%m %H:%M") if edited_at else datetime.now().strftime("%d.%m %H:%M")
    body = f"✏️ <b>Изменено сообщение</b>\n👤 От: <b>{html.escape(sender_name)}</b>\n🕐 {time_str}\n\n📝 <b>Было:</b>\n<i>{html.escape(old_text[:400])}</i>\n\n📝 <b>Стало:</b>\n{html.escape(new_text[:400])}"
    with suppress(Exception): await bot.send_message(owner_id, text=body, parse_mode="HTML")

# ==========================================
# NFT API
# ==========================================
async def fetch_gift_satellite(endpoint: str, retry: int = 2):
    if not GIFT_SATELLITE_TOKEN: return None
    headers = {"Authorization": f"Token {GIFT_SATELLITE_TOKEN}"}
    url = f"{GIFT_SATELLITE_BASE}{endpoint}"
    for attempt in range(retry + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as resp:
                    if resp.status == 200: return await resp.json()
                    elif resp.status == 429 and attempt < retry:
                        await asyncio.sleep(1.5)
                        continue
                    return None
        except: pass
    return None

async def get_nftmatch_floor(collection_name: str) -> dict:
    if not NFTMATCH_API_KEY:
        print("⚠️ NFTMATCH_API_KEY не задан в Render! Используем резервный Gift Satellite.")
        return {}
    
    headers = {"Authorization": NFTMATCH_API_KEY}
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', collection_name)
    result = {}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://nftmatch.pro/api/ListGifts/AllGiftNamesWithFloor", headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        if item.get("name") == collection_name:
                            result["collection_floor_ton"] = item.get("floorPrice") or item.get("floor")
                            break
                else:
                    print(f"❌ NFTMatch вернул статус {resp.status}")
            
            async with session.get(f"https://nftmatch.pro/api/ListGifts/{clean_name}/AllModelNames", headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prices = []
                    for item in data:
                        p = item.get("price") or item.get("floorPrice")
                        if p:
                            try: prices.append(float(p))
                            except: pass
                    if prices: result["floor_ton"] = min(prices)
    except Exception as e: 
        print(f"❌ Ошибка NFTMatch API: {e}")
    return result

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

    # 1. Пытаемся получить цены с NFTMatch (если задан ключ)
    result.update(await get_nftmatch_floor(collection))
    
    # 2. Если NFTMatch не дал цен (или нет ключа), берем данные из Gift Satellite
    if not result.get("floor_ton"):
        await asyncio.sleep(0.3)
        offers = await fetch_gift_satellite(f"/search/tg/{collection}")
        if offers and isinstance(offers, list):
            result["all_offers"] = offers
            model = result["model"]
            same_model = [o for o in offers if o.get("modelName") == model]
            if same_model:
                prices = [o.get("normalizedPrice", 0) for o in same_model if o.get("normalizedPrice")]
                if prices:
                    result["floor_ton"] = min(prices)
            all_prices = [o.get("normalizedPrice", 0) for o in offers if o.get("normalizedPrice")]
            if all_prices:
                result["collection_floor_ton"] = min(all_prices)
                
    return result

async def format_gift_card(data: dict, owner_id: int) -> str:
    if not data or "collection_name" not in data: return "❌ Подарок не найден."
    collection = data.get("collection_name", "Подарок")
    number = data.get("number", "?")
    slug = data.get("slug", "")
    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    currencies = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    title = f'<a href="https://t.me/nft/{slug}">{collection} #{number}</a>' if slug else f"{collection} #{number}"
    lines = [f"🎁 <b>{title}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    mono = data.get("monochrome_percent") or data.get("monochromePercentage")
    if mono:
        lines.append(f"⚫️ <b>Монохромность:</b> {mono}%")
        lines.append("")
    def fmt_price(ton_val):
        if not ton_val: return "—"
        ton_rate = EXCHANGE_CACHE.get("TON", 0)
        parts = [f"<b>{ton_val:.2f} TON</b>"]
        for cur in currencies:
            if cur == "TON": continue
            rate = EXCHANGE_CACHE.get(cur)
            if not rate or not ton_rate: continue
            val = (ton_val * ton_rate) / rate
            parts.append(f"{val:.6f} {cur}" if cur in ("BTC", "ETH") else (f"{val:.0f} {cur}" if cur == "STARS" else f"{val:.2f} {cur}"))
        return " | ".join(parts)
    floor_ton = data.get("floor_ton")
    coll_floor = data.get("collection_floor_ton")
    if floor_ton or coll_floor:
        lines.append("💎 <b>Рыночные данные:</b>")
        if floor_ton: lines.append(f"├ Floor (модель): {fmt_price(floor_ton)}")
        if coll_floor and coll_floor != floor_ton: lines.append(f"└ Floor (коллекция): {fmt_price(coll_floor)}")
    return "\n".join(lines)

# ==========================================
# ВАЛЮТЫ И МАТЕМАТИКА
# ==========================================
EXCHANGE_CACHE = {}
STARS_USD_RATE = 0.015

CURRENCY_ALIASES = {"usd": "USDT", "usdt": "USDT", "доллар": "USDT", "доллары": "USDT", "долларов": "USDT", "доллара": "USDT", "долл": "USDT", "долл.": "USDT", "бакс": "USDT", "баксы": "USDT", "баксов": "USDT", "бакса": "USDT", "бачей": "USDT", "бач": "USDT", "бач.": "USDT", "баксик": "USDT", "баксиков": "USDT", "баксика": "USDT", "зеленый": "USDT", "зелёный": "USDT", "зеленых": "USDT", "зелёных": "USDT", "зелень": "USDT", "зелени": "USDT", "юсд": "USDT", "юсдт": "USDT", "усд": "USDT", "усдт": "USDT", "тетер": "USDT", "тетеры": "USDT", "тетеров": "USDT", "тетера": "USDT", "тезер": "USDT", "тезеры": "USDT", "тезеров": "USDT", "тезера": "USDT", "юста": "USDT", "юст": "USDT", "$": "USDT", "💵": "USDT", "💰": "USDT", "💲": "USDT", "rub": "RUB", "руб": "RUB", "руб.": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "рубля": "RUB", "рублю": "RUB", "рубас": "RUB", "рубасов": "RUB", "рубаса": "RUB", "рубасы": "RUB", "рубчик": "RUB", "рубчики": "RUB", "рубчиков": "RUB", "деревянный": "RUB", "деревянных": "RUB", "деревянные": "RUB", "р": "RUB", "р.": "RUB", "₽": "RUB", "eur": "EUR", "евро": "EUR", "еврик": "EUR", "еврики": "EUR", "евриков": "EUR", "еврика": "EUR", "евра": "EUR", "евров": "EUR", "€": "EUR", "cny": "CNY", "юань": "CNY", "юани": "CNY", "юаней": "CNY", "юаня": "CNY", "юан": "CNY", "yuan": "CNY", "юанчики": "CNY", "женьминьби": "CNY", "жэньминьби": "CNY", "¥": "CNY", "btc": "BTC", "биткоин": "BTC", "биткоины": "BTC", "биткоинов": "BTC", "биткоина": "BTC", "биткойн": "BTC", "биткойны": "BTC", "биткойнов": "BTC", "биток": "BTC", "битки": "BTC", "битков": "BTC", "битка": "BTC", "бит": "BTC", "биты": "BTC", "битов": "BTC", "₿": "BTC", "ton": "TON", "тон": "TON", "тона": "TON", "тонов": "TON", "тоны": "TON", "тоник": "TON", "тоника": "TON", "тоников": "TON", "тонику": "TON", "грам": "TON", "грама": "TON", "грамов": "TON", "граммы": "TON", "грамм": "TON", "gram": "TON", "grams": "TON", "павлуша": "TON", "дуров": "TON", "💎": "TON", "stars": "STARS", "star": "STARS", "звезда": "STARS", "звёзда": "STARS", "звезды": "STARS", "звёзды": "STARS", "звезд": "STARS", "звёзд": "STARS", "звезде": "STARS", "звёзде": "STARS", "звездочка": "STARS", "звёздочка": "STARS", "звездочки": "STARS", "звёздочки": "STARS", "звездочек": "STARS", "звёздочек": "STARS", "зв": "STARS", "зв.": "STARS", "звездулька": "STARS", "звездульки": "STARS", "starчик": "STARS", "стар": "STARS", "стары": "STARS", "старов": "STARS", "⭐": "STARS", "🌟": "STARS", "✨": "STARS", "eth": "ETH", "эфир": "ETH", "эфира": "ETH", "эфиров": "ETH", "эфиры": "ETH", "эфириум": "ETH", "эфирка": "ETH", "эфирки": "ETH", "эфирок": "ETH", "эфирчик": "ETH", "эфирчика": "ETH", "Ξ": "ETH", "kzt": "KZT", "тенге": "KZT", "теньге": "KZT", "теньга": "KZT", "тг": "KZT", "тг.": "KZT", "₸": "KZT", "uah": "UAH", "гривна": "UAH", "гривны": "UAH", "гривен": "UAH", "гривне": "UAH", "гривня": "UAH", "гривню": "UAH", "грн": "UAH", "грн.": "UAH", "₴": "UAH", "gbp": "GBP", "фунт": "GBP", "фунты": "GBP", "фунтов": "GBP", "фунта": "GBP", "стерлинг": "GBP", "стерлинги": "GBP", "стерлингов": "GBP", "£": "GBP", "jpy": "JPY", "иена": "JPY", "иены": "JPY", "иен": "JPY", "йена": "JPY", "йены": "JPY", "йен": "JPY"}

ALL_CURRENCIES = ["RUB", "USDT", "EUR", "CNY", "KZT", "UAH", "GBP", "JPY", "BTC", "ETH", "TON", "STARS"]
_CURRENCY_ALT = "|".join(sorted([re.escape(a) for a in CURRENCY_ALIASES.keys()], key=len, reverse=True))
WORD_NUMBERS = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900, "тысяча": 1000}
_NUM_WORDS = "|".join(sorted(WORD_NUMBERS.keys(), key=len, reverse=True))
_SINGLE_NUM = rf"(?:\d+[.,]?\d*|{_NUM_WORDS})"
_NUM = rf"{_SINGLE_NUM}(?:\s+{_SINGLE_NUM})*"
_OPS = r"[+\-*/xх^]"
_WORDS = r"(?:плюс|минус|умножить на|разделить на|поделить на|умножить|разделить|поделить|сложить|вычесть|делить|степень|в степени|х)"

AUTO_MATH_PATTERN = re.compile(rf"^\s*{_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})+\s*$", re.IGNORECASE)
AUTO_MATH_CURRENCY_PATTERN = re.compile(rf"^\s*({_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})*)\s{{0,3}}({_CURRENCY_ALT})\s*$", re.IGNORECASE)
MIXED_CURRENCY_FULL = re.compile(rf"^\s*{_SINGLE_NUM}\s*(?:{_CURRENCY_ALT})(?:\s*[+\-]\s*{_SINGLE_NUM}\s*(?:{_CURRENCY_ALT}))+\s*$", re.IGNORECASE)
MIXED_CURRENCY_TERM = re.compile(rf"({_SINGLE_NUM})\s*({_CURRENCY_ALT})", re.IGNORECASE)
OPERATORS = {"умножить на": "*", "разделить на": "/", "поделить на": "/", "в степени": "**", "степень": "**", "плюс": "+", "сложить": "+", "минус": "-", "вычесть": "-", "умножить": "*", "разделить": "/", "делить": "/", "поделить": "/", "х": "*", "x": "*", "^": "**"}

def parse_math_expression(text: str) -> str:
    text = text.lower().strip()
    for word, op in sorted(OPERATORS.items(), key=lambda x: -len(x[0])):
        if word in ("^",): text = text.replace("^", " ** ")
        else: text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", f" {op} ", text)
    tokens = re.findall(r"\*\*|[+\-*/()]|\d+(?:\.\d+)?|[а-яёa-z]+", text)
    result, current_num = [], None
    for tok in tokens:
        if tok in WORD_NUMBERS: current_num = (current_num + WORD_NUMBERS[tok]) if current_num is not None else WORD_NUMBERS[tok]
        else:
            if current_num is not None:
                result.append(str(current_num))
                current_num = None
            if re.fullmatch(r"\*\*|[+\-*/()]|\d+(?:\.\d+)?", tok): result.append(tok)
    if current_num is not None: result.append(str(current_num))
    return " ".join(result).strip()

def normalize_math_input(text: str) -> str: return re.sub(r"\s{1,}", " ", re.sub(r"(?<=\d),(?=\d)", ".", text.strip()))
def format_math_expression(expr: str) -> str: return re.sub(r"\s+", " ", re.sub(r"\s*(\*\*|[+\-*/])\s*", r" \1 ", re.sub(r"\s+", " ", expr).strip())).strip()

async def fetch_json(url: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200: return await resp.json()
    except: pass
    return None

async def force_update_all_rates():
    try:
        fiat_data = await fetch_json("https://open.er-api.com/v6/latest/USD")
        if fiat_data and "rates" in fiat_data:
            for cur in ["RUB", "EUR", "CNY", "UAH", "KZT", "GBP", "JPY"]:
                if fiat_data["rates"].get(cur): EXCHANGE_CACHE[cur] = 1.0 / fiat_data["rates"][cur]
        for sym, code in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("TONUSDT", "TON")]:
            data = await fetch_json(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}")
            if data and "price" in data:
                try: EXCHANGE_CACHE[code] = float(data["price"])
                except: pass
        EXCHANGE_CACHE["USD"], EXCHANGE_CACHE["USDT"], EXCHANGE_CACHE["STARS"] = 1.0, 1.0, STARS_USD_RATE
    except Exception as e: print(f"Ошибка курсов: {e}")

async def update_rates_loop():
    while True:
        await force_update_all_rates()
        await asyncio.sleep(300)

async def convert_currency(amount: float, from_cur: str, to_cur: str):
    if from_cur == to_cur: return amount
    if from_cur not in EXCHANGE_CACHE or to_cur not in EXCHANGE_CACHE: await force_update_all_rates()
    r_from, r_to = EXCHANGE_CACHE.get(from_cur), EXCHANGE_CACHE.get(to_cur)
    return (amount * r_from) / r_to if r_from is not None and r_to is not None else None

EMOJI_MAP = {"RUB": "🇷🇺", "USDT": "💵", "EUR": "🇪🇺", "CNY": "🇨🇳", "KZT": "🇰🇿", "UAH": "🇺🇦", "GBP": "🇬🇧", "JPY": "🇯🇵", "BTC": "₿", "ETH": "Ξ", "TON": "💎", "STARS": "⭐"}

def _format_converted(target: str, result: float) -> str:
    if target in ("BTC", "ETH", "TON"): formatted = f"{result:.6f}".rstrip("0").rstrip(".")
    elif target == "STARS": formatted = f"{result:.2f}".rstrip("0").rstrip(".")
    elif target in ("RUB", "KZT", "UAH", "JPY"): formatted = f"{result:.2f}"
    else: formatted = f"{result:.3f}".rstrip("0").rstrip(".")
    return formatted or "0"

async def send_currency_conversion(message, amount: float, from_cur: str, original_expr: str = None, owner_id: int = None):
    conn_id = getattr(message, "business_connection_id", None)
    owner_id = owner_id or message.from_user.id
    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    currencies = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    if from_cur == "USD": from_cur = "USDT"
    header = f"🔄 Конвертация {original_expr} {from_cur}" if original_expr else f"🔄 Конвертация {f'{amount:.6f}'.rstrip('0').rstrip('.')} {from_cur}"
    lines, shown = [f"<b>{header}</b>", ""], set()
    for target in currencies:
        if target == "USD": target = "USDT"
        if target in shown or target == from_cur: continue
        shown.add(target)
        result = await convert_currency(amount, from_cur, target)
        if result is not None: lines.append(f"{target} {EMOJI_MAP.get(target, '•')}: <code>{_format_converted(target, result)}</code>")
    with suppress(Exception):
        await bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML", reply_to_message_id=message.message_id, business_connection_id=conn_id)

def parse_mixed_currency(text: str):
    parsed, pos = [], 0
    for m in MIXED_CURRENCY_TERM.finditer(text.strip()):
        between = text[pos:m.start()].strip()
        if parsed:
            if between not in ("+", "-"): return None
            parsed.append(between)
        elif between: return None
        num_str, cur_str = m.group(1), m.group(2).lower()
        code = CURRENCY_ALIASES.get(cur_str)
        if not code: return None
        try:
            num = float(num_str.replace(",", ".")) if re.fullmatch(r"\d+[.,]?\d*", num_str) else float(simple_eval(parse_math_expression(num_str)))
        except: return None
        parsed.append({"num": num, "cur": code})
        pos = m.end()
    return parsed if not text[pos:].strip() and len(parsed) >= 3 else None

async def eval_mixed_currency(parsed):
    if not EXCHANGE_CACHE: await force_update_all_rates()
    usdt_values = []
    for item in parsed:
        if isinstance(item, dict):
            rate = EXCHANGE_CACHE.get(item["cur"])
            if rate is None:
                await force_update_all_rates()
                rate = EXCHANGE_CACHE.get(item["cur"])
                if rate is None: return None
            usdt_values.append(item["num"] * rate)
        else: usdt_values.append(item)
    result, i = usdt_values[0], 1
    while i < len(usdt_values) - 1:
        if usdt_values[i] == "+": result += usdt_values[i + 1]
        elif usdt_values[i] == "-": result -= usdt_values[i + 1]
        i += 2
    return result

async def send_mixed_currency_conversion(message, parsed, result_usdt, owner_id):
    conn_id = getattr(message, "business_connection_id", None)
    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    currencies = list(owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"]))
    expr = " ".join([f"{i['num']:g} {i['cur']}" if isinstance(i, dict) else i for i in parsed])
    lines = [f"<b>🔄 {expr}</b>", ""]
    if "USDT" not in currencies: currencies.append("USDT")
    shown = set()
    for target in currencies:
        if target == "USD": target = "USDT"
        if target in shown: continue
        shown.add(target)
        rate = EXCHANGE_CACHE.get(target)
        if rate: lines.append(f"{target} {EMOJI_MAP.get(target, '•')}: <code>{_format_converted(target, result_usdt / rate)}</code>")
    with suppress(Exception):
        await bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML", reply_to_message_id=message.message_id, business_connection_id=conn_id)

def check_auto_afk(start_h: int, end_h: int, tz_offset: int = 3) -> bool:
    local_hour = (datetime.now(timezone.utc).hour + tz_offset) % 24
    return (start_h <= local_hour < end_h) if start_h < end_h else (local_hour >= start_h or local_hour < end_h)

async def get_user_main_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    is_afk = user_data.get("is_afk", False)
    fmt_label = format_mode_label(user_data.get("format_mode", []))
    afk_btn = InlineKeyboardButton(text="🟢 Автоответчик — ВКЛЮЧЕН" if is_afk else "🔴 Автоответчик — ВЫКЛЮЧЕН", callback_data="afk_menu")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds")],
        [InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes"), InlineKeyboardButton(text="🗑 Удалённые", callback_data="deleted_menu")],
        [InlineKeyboardButton(text="👤 Мой статус", callback_data="user_status"), InlineKeyboardButton(text="💱 Валюты", callback_data="currency_settings")],
        [InlineKeyboardButton(text=f"✍️ Форматирование: {fmt_label}", callback_data="format_menu")],
        [afk_btn],
    ])

async def get_afk_settings_kb(user_id: int):
    u = await users_collection.find_one({"user_id": user_id}) or {}
    tz = u.get("tz_offset", 3)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="afk_set_text")],
        [InlineKeyboardButton(text=f"🕒 Авто-включение: {'ВКЛ' if u.get('auto_afk') else 'ВЫКЛ'}", callback_data="toggle_auto_afk")],
        [InlineKeyboardButton(text=f"⏰ Время: {u.get('afk_start', 23)}:00 - {u.get('afk_end', 7)}:00", callback_data="afk_set_time")],
        [InlineKeyboardButton(text=f"🌍 Пояс: UTC{'+' if tz >= 0 else ''}{tz}", callback_data="afk_set_tz")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="afk_menu")],
    ])
