import os
import io
import asyncio
import random
import html
import re
import json
import aiohttp
from datetime import datetime, timezone, timedelta
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

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

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
except Exception as e:
    print(f"Ошибка БД: {e}")

muted_chats = set()
afk_cooldowns = {}

EXCHANGE_CACHE = {}
STARS_USD_RATE = 0.015

# ==========================================
# GOOGLE DRIVE
# ==========================================
_drive_service = None
_folder_cache = {}  # "path/to/folder" -> folder_id
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
    """Ищет папку по имени внутри parent_id. None в parent_id = корень."""
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
    """Создаёт вложенную структуру папок. path = 'root/sub1/sub2'."""
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


def upload_to_drive(file_bytes: bytes, filename: str, folder_path: str, mime_type: str = "application/octet-stream") -> dict | None:
    """Загружает файл в указанную папку. Возвращает {'id', 'link', 'webViewLink'}."""
    if not _drive():
        return None
    try:
        folder_id = get_or_create_folder(folder_path)
        if not folder_id:
            return None
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=False)
        meta = {"name": filename, "parents": [folder_id]}
        file = _drive().files().create(
            body=meta, media_body=media,
            fields="id, webViewLink, webContentLink"
        ).execute()
        return {
            "id": file.get("id"),
            "link": file.get("webViewLink"),
            "download": file.get("webContentLink"),
        }
    except Exception as e:
        print(f"Drive upload error: {e}")
        return None


def _sanitize(name: str) -> str:
    """Убирает запрещённые символы для имени папки/файла."""
    return re.sub(r'[\\/:*?"<>|]', "_", (name or "unknown"))[:80]


# ==========================================
# АРХИВАЦИЯ
# ==========================================
def _extract_media_info(message: Message):
    """Возвращает (media_type, file_id, ext, mime) или (None, None, None, None)."""
    if message.photo:
        return "photo", message.photo[-1].file_id, "jpg", "image/jpeg"
    if message.video:
        return "video", message.video.file_id, "mp4", "video/mp4"
    if message.video_note:
        return "video_note", message.video_note.file_id, "mp4", "video/mp4"
    if message.animation:
        return "animation", message.animation.file_id, "mp4", "video/mp4"
    if message.document:
        fname = message.document.file_name or "file"
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "bin"
        return "document", message.document.file_id, ext, message.document.mime_type or "application/octet-stream"
    if message.audio:
        return "audio", message.audio.file_id, "mp3", message.audio.mime_type or "audio/mpeg"
    if message.voice:
        return "voice", message.voice.file_id, "ogg", "audio/ogg"
    if message.sticker:
        return "sticker", message.sticker.file_id, "webp", "image/webp"
    return None, None, None, None


async def archive_message(message: Message, conn_id: str, chat_id: int, owner_id: int):
    """Сохраняет сообщение в вечный архив + медиа на Drive."""
    is_owner = (message.from_user.id == chat_id)
    text = message.text or message.caption or ""

    media_type, file_id, ext, mime = _extract_media_info(message) if not is_owner else (None, None, None, None)

    # Для сообщений собеседника тоже сохраняем медиа
    if not is_owner:
        media_type, file_id, ext, mime = _extract_media_info(message)

    doc = {
        "conn_id": conn_id,
        "chat_id": chat_id,
        "owner_id": owner_id,
        "message_id": message.message_id,
        "user_id": message.from_user.id,
        "first_name": message.from_user.first_name or "Без имени",
        "username": message.from_user.username or "",
        "text": text,
        "is_owner": is_owner,
        "media_type": media_type,
        "drive_link": None,
        "drive_file_id": None,
        "is_deleted": False,
        "is_viewed": False,
        "created_at": datetime.now(timezone.utc),
    }

    # Скачиваем и заливаем медиа
    if media_type and file_id:
        try:
            folder_path = build_chat_folder(conn_id, chat_id, message.from_user)
            file = await bot.get_file(file_id)
            buffer = await bot.download_file(file.file_path)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"{ts}_{media_type}.{ext}"

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, upload_to_drive, buffer.read(), filename, folder_path + "/media", mime
            )
            if result:
                doc["drive_link"] = result["link"]
                doc["drive_file_id"] = result["id"]
        except Exception as e:
            print(f"Media upload error: {e}")

    with suppress(Exception):
        await archive_collection.insert_one(doc)

    # Если медиа не удалось залить и текст пустой — добавим метку
    if media_type and not doc["drive_link"]:
        with suppress(Exception):
            await archive_collection.update_one(
                {"_id": doc.get("_id")},
                {"$set": {"text": text or f"[{media_type} — не загружено]"}}
            )


def build_chat_folder(conn_id: str, chat_id: int, sender=None) -> str:
    """Строит путь папки: TelegramArchiveBot/{username}/{Имя (id)}."""
    owner_data = None
    # берём из кэша или из имени соединения
    try:
        owner_name = _conn_owner_name.get(conn_id, "unknown")
    except Exception:
        owner_name = "unknown"
    sender_name = _sanitize(sender.first_name if sender else "chat")
    return f"{ROOT_FOLDER_NAME}/{_sanitize(owner_name)}/{sender_name} ({chat_id})"


_conn_owner_name = {}


async def is_archive_enabled(conn_id: str, chat_id: int) -> bool:
    doc = await users_collection.find_one({"user_id": SUPERADMIN_ID})
    # Архивация включена всегда для всех чатов — не требует команды
    return True


# ==========================================
# ОБНОВЛЕНИЕ ЮЗЕРНЕЙМОВ (раз в сутки)
# ==========================================
async def refresh_usernames_loop():
    """Раз в сутки обновляет username/first_name у всех известных user_id."""
    while True:
        try:
            await asyncio.sleep(86400)  # 24 часа
            unique_users = await archive_collection.distinct("user_id")
            for uid in unique_users:
                try:
                    chat = await bot.get_chat(uid)
                    await archive_collection.update_many(
                        {"user_id": uid},
                        {"$set": {
                            "username": chat.username or "",
                            "first_name": chat.first_name or "Без имени",
                        }}
                    )
                    await deleted_users_collection.update_many(
                        {"user_id": uid},
                        {"$set": {
                            "username": chat.username or "",
                            "first_name": chat.first_name or "Без имени",
                        }}
                    )
                    await asyncio.sleep(0.5)
                except Exception:
                    continue
            print("✅ Usernames refreshed")
        except Exception as e:
            print(f"refresh_usernames_loop error: {e}")
            await asyncio.sleep(3600)


# ==========================================
# GIFT SATELLITE (сокращённо)
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
# КУРСЫ
# ==========================================
CURRENCY_ALIASES = {
    "usd": "USD", "доллар": "USD", "доллары": "USD", "долларов": "USD", "бакс": "USD", "баксы": "USD", "баксов": "USD", "$": "USD",
    "rub": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "руб": "RUB", "₽": "RUB",
    "eur": "EUR", "евро": "EUR", "€": "EUR",
    "cny": "CNY", "юань": "CNY", "юани": "CNY", "юаней": "CNY", "¥": "CNY",
    "btc": "BTC", "биткоин": "BTC", "биток": "BTC", "₿": "BTC",
    "ton": "TON", "тон": "TON", "тонов": "TON", "грам": "TON",
    "stars": "STARS", "звезд": "STARS", "звёзд": "STARS", "звезды": "STARS", "⭐": "STARS",
    "eth": "ETH", "эфир": "ETH", "Ξ": "ETH",
    "usdt": "USDT", "тетер": "USDT", "юсдт": "USDT",
    "kzt": "KZT", "тенге": "KZT", "₸": "KZT",
    "uah": "UAH", "гривна": "UAH", "гривен": "UAH", "₴": "UAH",
    "gbp": "GBP", "фунт": "GBP", "£": "GBP",
    "jpy": "JPY", "иена": "JPY", "йена": "JPY",
}

ALL_CURRENCIES = ["RUB", "USD", "EUR", "CNY", "UAH", "KZT", "GBP", "JPY", "BTC", "ETH", "USDT", "TON", "STARS"]
_CURRENCY_ALT = "|".join(sorted([re.escape(a) for a in CURRENCY_ALIASES.keys()], key=len, reverse=True))
_NUM = r"\d+[.,]?\d*"
_OPS = r"[+\-*/xх^]"
_WORDS = r"(?:плюс|минус|умножить на|разделить на|поделить на|умножить|разделить|поделить|сложить|вычесть|делить|степень|в степени|х)"
AUTO_MATH_PATTERN = rf"^\s*{_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})+\s*$"
AUTO_MATH_CURRENCY_PATTERN = rf"^\s*({_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})*)\s{{0,3}}({_CURRENCY_ALT})\s*$"
WORD_NUMBERS = {
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "сто": 100, "тысяча": 1000,
}
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
        for sym, code in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("TONUSDT", "TON")]:
            data = await fetch_json(f"https://api.mexc.com/api/v3/ticker/price?symbol={sym}")
            if data and "price" in data:
                EXCHANGE_CACHE[code] = float(data["price"])
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
    display_currencies = owner_settings.get("display_currencies", ["RUB", "STARS", "TON"])
    amount_str = f"{amount:.4f}".rstrip("0").rstrip(".") if isinstance(amount, float) else str(amount)
    lines = [f"💱 <b>{original_expr}</b> = <b>{amount_str} {from_cur}</b>:\n"] if original_expr else [f"💱 <b>{amount_str} {from_cur}</b>:\n"]
    for target in display_currencies:
        if target == from_cur:
            continue
        result = await convert_currency(amount, from_cur, target)
        if result is None:
            continue
        if target in ("BTC", "ETH", "TON"):
            formatted = f"{result:.6f}".rstrip("0").rstrip(".")
        elif target == "STARS":
            formatted = f"{result:.0f}"
        else:
            formatted = f"{result:.2f}"
        emoji = {"RUB": "🇷🇺", "USD": "🇺🇸", "EUR": "🇪🇺", "CNY": "🇨🇳", "UAH": "🇺🇦",
                 "KZT": "🇰🇿", "GBP": "🇬🇧", "JPY": "🇯🇵", "BTC": "₿", "ETH": "Ξ",
                 "USDT": "💵", "TON": "💎", "STARS": "⭐"}.get(target, "•")
        lines.append(f"{emoji} <b>{formatted}</b> {target}")
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


def is_protected_media(message: Message) -> bool:
    return bool(getattr(message, "has_protected_content", False))


async def ensure_connection(conn_id: str, user_id: int, first_name: str):
    with suppress(Exception):
        await connections_collection.update_one(
            {"business_connection_id": conn_id},
            {"$set": {"business_connection_id": conn_id, "user_id": user_id,
                      "first_name": first_name or "Без имени",
                      "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )


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
    # TTL 48ч для рабочих сообщений
    with suppress(Exception):
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
    # Индексы для архива
    with suppress(Exception):
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("created_at", -1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("is_viewed", 1)])
        await archive_collection.create_index([("user_id", 1)])
        await deleted_users_collection.create_index([("conn_id", 1), ("chat_id", 1)], unique=True)

    asyncio.create_task(update_rates_loop())
    asyncio.create_task(refresh_usernames_loop())

    # Google Drive
    _init_drive()

    # Проверка Gift Satellite
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
# МЕНЮ ПОЛЬЗОВАТЕЛЯ
# ==========================================
async def count_unviewed_deleted(owner_id: int) -> int:
    return await archive_collection.count_documents({
        "owner_id": owner_id,
        "is_deleted": True,
        "is_viewed": False
    })


async def get_user_main_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    is_afk = user_data.get("is_afk", False)
    status_text = "🔴 ВЫКЛ" if not is_afk else "🟢 ВКЛ"
    unviewed = await count_unviewed_deleted(user_id)
    deleted_btn = f"🗑 Удалённые ({unviewed})" if unviewed else "🗑 Удалённые"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💤 Автоответчик: {status_text}", callback_data="toggle_afk")],
        [InlineKeyboardButton(text="⚙️ Настройки автоответчика", callback_data="afk_settings")],
        [InlineKeyboardButton(text="💱 Валюты для конвертации", callback_data="currency_settings")],
        [InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes")],
        [InlineKeyboardButton(text=deleted_btn, callback_data="deleted_menu")],
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds")]
    ])


@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    with suppress(Exception):
        await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"user_id": message.from_user.id}}, upsert=True)
    kb = await get_user_main_kb(message.from_user.id)
    await message.answer("👋 **Твой личный бот-секретарь.**\nУправляй статусом и настройками ниже:", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = await get_user_main_kb(call.from_user.id)
    await call.message.edit_text("🏠 **Главное меню:**", reply_markup=kb, parse_mode="Markdown")


# ==========================================
# МЕНЮ УДАЛЁНОК
# ==========================================
@dp.callback_query(F.data == "deleted_menu")
async def deleted_menu_handler(call: CallbackQuery):
    owner_id = call.from_user.id

    # уникальные собеседники, чьи сообщения удалялись
    pipeline = [
        {"$match": {"owner_id": owner_id, "is_deleted": True}},
        {"$group": {
            "_id": {"conn_id": "$conn_id", "chat_id": "$chat_id"},
            "count": {"$sum": 1},
            "unviewed": {"$sum": {"$cond": [{"$eq": ["$is_viewed", False]}, 1, 0]}},
            "first_name": {"$last": "$first_name"},
            "username": {"$last": "$username"},
        }},
        {"$sort": {"unviewed": -1, "count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)

    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="user_main")
        await call.message.edit_text(
            "🗑 **Удалённые сообщения**\n\nПока пусто. Как только собеседники начнут удалять — они появятся здесь.",
            reply_markup=builder.as_markup(), parse_mode="Markdown"
        )
        return

    for ch in chats:
        conn_id = ch["_id"]["conn_id"]
        chat_id = ch["_id"]["chat_id"]
        name = ch.get("first_name") or "Без имени"
        uname = f"@{ch['username']}" if ch.get("username") else ""
        unviewed = ch.get("unviewed", 0)
        label = f"{name} {uname} · {ch['count']}"
        if unviewed:
            label += f" · 🆕 {unviewed}"
        builder.button(text=label[:60], callback_data=f"delchat_{conn_id}_{chat_id}")

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    await call.message.edit_text("🗑 **Собеседники с удалёнными сообщениями:**", reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("delchat_"))
async def deleted_chat_handler(call: CallbackQuery):
    parts = call.data.replace("delchat_", "").rsplit("_", 1)
    if len(parts) != 2:
        await call.answer("Ошибка навигации", show_alert=True)
        return
    conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id

    total = await archive_collection.count_documents({"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id, "is_deleted": True})
    unviewed = await archive_collection.count_documents({"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id, "is_deleted": True, "is_viewed": False})

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Все удалённые ({total})", callback_data=f"delall_{conn_id}_{chat_id}")
    if unviewed:
        builder.button(text=f"🆕 Непросмотренные ({unviewed})", callback_data=f"delnew_{conn_id}_{chat_id}")
    builder.button(text="✅ Отметить всё просмотренным", callback_data=f"delview_{conn_id}_{chat_id}")
    builder.button(text="🔙 Назад", callback_data="deleted_menu")
    builder.adjust(1)

    await call.message.edit_text(f"🗑 **Удалённые от этого собеседника**\n\nВсего: {total}\nНепросмотрено: {unviewed}", reply_markup=builder.as_markup(), parse_mode="Markdown")


def _render_messages(msgs: list) -> list[str]:
    """Разбивает длинный список сообщений на куски по 4000 символов."""
    chunks = []
    current = ""
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m %H:%M") if isinstance(ts, datetime) else "?"
        author = m.get("first_name") or "Без имени"
        text = html.escape(m.get("text") or "[медиа]")
        media = m.get("media_type")
        if media and m.get("drive_link"):
            text += f'\n📎 <a href="{m["drive_link"]}">[{media}]</a>'
        elif media:
            text += f"\n📎 [{media}]"
        line = f"<b>{ts_str} · {html.escape(author)}</b>\n{text}\n{'─' * 20}\n"
        if len(current) + len(line) > 3500:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


@dp.callback_query(F.data.startswith("delall_") | F.data.startswith("delnew_"))
async def show_deleted_messages(call: CallbackQuery):
    is_new = call.data.startswith("delnew_")
    prefix = "delnew_" if is_new else "delall_"
    parts = call.data.replace(prefix, "").rsplit("_", 1)
    if len(parts) != 2:
        await call.answer("Ошибка", show_alert=True)
        return
    conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id

    query = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id, "is_deleted": True}
    if is_new:
        query["is_viewed"] = False

    limit = 10 if is_new else 200
    msgs = await archive_collection.find(query).sort("created_at", -1).limit(limit).to_list(length=limit)
    msgs.reverse()

    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return

    chunks = _render_messages(msgs)
    for i, chunk in enumerate(chunks[:3]):
        with suppress(Exception):
            await call.message.answer(f"<b>📋 Сообщений: {len(msgs)}</b>\n\n{chunk}", parse_mode="HTML", disable_web_page_preview=True)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)

    # Помечаем как просмотренные
    if is_new:
        await archive_collection.update_many(query, {"$set": {"is_viewed": True}})

    with suppress(TelegramBadRequest):
        await call.answer("Готово")


@dp.callback_query(F.data.startswith("delview_"))
async def mark_viewed(call: CallbackQuery):
    parts = call.data.replace("delview_", "").rsplit("_", 1)
    conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id
    await archive_collection.update_many(
        {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id, "is_viewed": False},
        {"$set": {"is_viewed": True}}
    )
    with suppress(TelegramBadRequest):
        await call.answer("✅ Все помечены просмотренными", show_alert=True)


# ==========================================
# АДМИНКА — МОИ ТВИНКИ
# ==========================================
def get_admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💼 Мои твинки", callback_data="admin_twinks")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes")],
    ])


@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return
    await message.answer("👑 **Панель управления:**", reply_markup=get_admin_main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "admin_main")
async def back_to_main_admin(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID:
        return
    await state.clear()
    await call.message.edit_text("👑 **Панель управления:**", reply_markup=get_admin_main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "admin_twinks")
async def admin_twinks(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        return
    conns = await connections_collection.find({}).to_list(length=100)

    builder = InlineKeyboardBuilder()
    if not conns:
        builder.button(text="🔙 Назад", callback_data="admin_main")
        await call.message.edit_text("Нет подключённых аккаунтов.", reply_markup=builder.as_markup())
        return

    for c in conns:
        name = c.get("first_name", "Без имени")
        conn_id = c["business_connection_id"]
        count = await archive_collection.count_documents({"conn_id": conn_id})
        builder.button(text=f"💼 {name} ({count})", callback_data=f"twink_{conn_id}")
    builder.button(text="🔙 Назад", callback_data="admin_main")
    builder.adjust(1)
    await call.message.edit_text("💼 **Выбери аккаунт (твинк):**", reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("twink_"))
async def twink_chats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        return
    conn_id = call.data.replace("twink_", "")

    pipeline = [
        {"$match": {"conn_id": conn_id}},
        {"$group": {
            "_id": {"chat_id": "$chat_id"},
            "count": {"$sum": 1},
            "deleted": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}},
            "first_name": {"$last": "$first_name"},
            "username": {"$last": "$username"},
        }},
        {"$sort": {"count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)

    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="admin_twinks")
        await call.message.edit_text("У этого аккаунта пока нет переписок.", reply_markup=builder.as_markup())
        return

    for ch in chats:
        chat_id = ch["_id"]["chat_id"]
        name = ch.get("first_name") or "Без имени"
        uname = f"@{ch['username']}" if ch.get("username") else ""
        label = f"{name} {uname} · {ch['count']} msg"
        if ch.get("deleted"):
            label += f" · 🗑 {ch['deleted']}"
        builder.button(text=label[:60], callback_data=f"twinkchat_{conn_id}_{chat_id}")
    builder.button(text="🔙 Назад", callback_data="admin_twinks")
    builder.adjust(1)
    await call.message.edit_text("💬 **Переписки аккаунта:**", reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("twinkchat_"))
async def twink_full_chat(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        return
    parts = call.data.replace("twinkchat_", "").rsplit("_", 1)
    if len(parts) != 2:
        await call.answer("Ошибка", show_alert=True)
        return
    conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)

    total = await archive_collection.count_documents({"conn_id": conn_id, "chat_id": chat_id})
    deleted = await archive_collection.count_documents({"conn_id": conn_id, "chat_id": chat_id, "is_deleted": True})

    msgs = await archive_collection.find({"conn_id": conn_id, "chat_id": chat_id}).sort("created_at", -1).limit(50).to_list(length=50)
    msgs.reverse()

    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return

    chunks = _render_messages(msgs)
    header = f"<b>💬 Полная переписка</b>\nВсего: {total} · Удалено: {deleted}\nПоказано: последние {len(msgs)}\n{'━' * 20}\n\n"

    for i, chunk in enumerate(chunks[:3]):
        text = header + chunk if i == 0 else chunk
        with suppress(Exception):
            await call.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)

    with suppress(TelegramBadRequest):
        await call.answer("Готово")


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_main")
    total_archive = await archive_collection.count_documents({})
    total_deleted = await archive_collection.count_documents({"is_deleted": True})
    total_conns = await connections_collection.count_documents({})
    await call.message.edit_text(
        f"📊 <b>Статистика:</b>\n\nБизнесов: {total_conns}\nСообщений в архиве: {total_archive}\nУдалённых: {total_deleted}",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )


# ==========================================
# ОСТАЛЬНЫЕ МЕНЮ (сокращены)
# ==========================================
async def get_afk_settings_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    auto_afk = user_data.get("auto_afk", False)
    start_h = user_data.get("afk_start", 23)
    end_h = user_data.get("afk_end", 7)
    auto_status = "ВКЛ" if auto_afk else "ВЫКЛ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="afk_set_text")],
        [InlineKeyboardButton(text=f"🕒 Авто-включение: {auto_status}", callback_data="toggle_auto_afk")],
        [InlineKeyboardButton(text=f"⏰ Время: {start_h}:00 - {end_h}:00", callback_data="afk_set_time")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main")]
    ])


@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("is_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"is_afk": new_status}}, upsert=True)
    kb = await get_user_main_kb(call.from_user.id)
    await call.message.edit_text("🏠 **Главное меню:**", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "toggle_auto_afk")
async def toggle_auto_afk(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("auto_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": new_status}}, upsert=True)
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text(f"Текущий текст:\n_{current}_\n\nОтправь новый:", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_afk_text)


@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Сохранено!", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text("Формат: `23 7`", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_afk_time)


@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
    try:
        parts = message.text.replace("-", " ").split()
        start_h, end_h = int(parts[0]), int(parts[1])
        if 0 <= start_h <= 23 and 0 <= end_h <= 23:
            await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_start": start_h, "afk_end": end_h}})
            kb = await get_afk_settings_kb(message.from_user.id)
            await message.answer("✅ Сохранено!", reply_markup=kb)
            await state.clear()
        else:
            await message.answer("⚠ Часы от 0 до 23")
    except Exception:
        await message.answer("⚠ Формат: `23 7`")


@dp.callback_query(F.data == "currency_settings")
async def currency_settings_handler(call: CallbackQuery):
    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON"])
    builder = InlineKeyboardBuilder()
    for cur in ALL_CURRENCIES:
        check = "✅" if cur in display else "⬜"
        builder.button(text=f"{check} {cur}", callback_data=f"cur_toggle_{cur}")
    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(2)
    await call.message.edit_text("💱 <b>Выбери валюты:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("cur_toggle_"))
async def currency_toggle_handler(call: CallbackQuery):
    cur = call.data.replace("cur_toggle_", "")
    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON"])
    if cur in display:
        display.remove(cur)
    else:
        display.append(cur)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"display_currencies": display}}, upsert=True)
    await currency_settings_handler(call)


@dp.callback_query(F.data == "user_mutes")
async def user_mutes_handler(call: CallbackQuery):
    user_conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in user_conns]
    builder = InlineKeyboardBuilder()
    has_mutes = False
    for mute in list(muted_chats):
        try:
            conn, chat = mute.rsplit("_", 1)
            if conn in conn_ids:
                has_mutes = True
                builder.button(text=f"Снять мут: {chat}", callback_data=f"u_unmute_{mute}")
        except Exception:
            continue
    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    if not has_mutes:
        await call.message.edit_text("Нет мутов.", reply_markup=builder.as_markup())
    else:
        await call.message.edit_text("🔇 **Муты:**", reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("u_unmute_"))
async def user_unmute_callback(call: CallbackQuery):
    mute_key = call.data.replace("u_unmute_", "")
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.answer("✅ Снят!", show_alert=True)
    else:
        await call.answer("Уже снят.", show_alert=True)
    await user_mutes_handler(call)


@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    text = (
        "📖 **Команды:**\n\n"
        "🚫 `.мут` — мут собеседника\n"
        "💣 `.[N] [текст]` — спам\n"
        "🎭 `.п1`, `.п2`, `.п3` — анимации\n"
        "🧮 `5+3`, `5 баксов` — математика/валюты\n"
        "🎁 `t.me/nft/...` — карточка подарка\n"
        "🗑 **Удалённые** — в главном меню"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main")
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())


# ==========================================
# БИЗНЕС-ЛОГИКА
# ==========================================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        await ensure_connection(connection.id, connection.user.id, connection.user.first_name)
        _conn_owner_name[connection.id] = connection.user.username or connection.user.first_name or "unknown"
    else:
        with suppress(Exception):
            await connections_collection.delete_one({"business_connection_id": connection.id})


@dp.business_message(ReplyHasMedia())
async def auto_save_replied_media(message: Message):
    if message.from_user.id == message.chat.id:
        return
    reply = message.reply_to_message
    if not reply or not is_protected_media(reply):
        return
    # тут сохраняем медиа в личку владельцу (как было)
    file_id, media_kind, filename = None, None, "saved"
    if reply.photo: file_id, media_kind, filename = reply.photo[-1].file_id, "photo", "saved.jpg"
    elif reply.video: file_id, media_kind, filename = reply.video.file_id, "video", "saved.mp4"
    elif reply.video_note: file_id, media_kind, filename = reply.video_note.file_id, "video_note", "saved_note.mp4"
    elif reply.animation: file_id, media_kind, filename = reply.animation.file_id, "animation", "saved.mp4"
    elif reply.document: file_id, media_kind, filename = reply.document.file_id, "document", reply.document.file_name or "saved.bin"
    if not file_id:
        return
    owner_id = message.from_user.id
    try:
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        input_file = BufferedInputFile(buffer.read(), filename=filename)
        if media_kind == "photo":
            await bot.send_photo(chat_id=owner_id, photo=input_file, caption="🕵️ Сохранено")
    except Exception as e:
        print(f"save_media error: {e}")


@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        mute_key = f"{conn_id}_{chat_id}"
        if mute_key in muted_chats:
            return
        muted_chats.add(mute_key)
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]])
        await bot.send_message(chat_id=message.chat.id, text="мут выдан", reply_markup=markup, business_connection_id=conn_id)


@dp.business_message(F.text.regexp(r"^\.(\d+)\s+"))
async def spam_command(message: Message):
    if message.from_user.id == message.chat.id:
        return
    match = re.match(r"^\.(\d+)\s+(.*)", message.text, re.DOTALL)
    if not match:
        return
    count = min(int(match.group(1)), 50)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    for _ in range(count):
        with suppress(Exception):
            await bot.send_message(chat_id=message.chat.id, text=match.group(2), business_connection_id=message.business_connection_id)
        await asyncio.sleep(0.5)


@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    if message.from_user.id == message.chat.id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0], business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    current = full_text[0]
    for char in full_text[1:]:
        current += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current, business_connection_id=message.business_connection_id)


@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    if message.from_user.id == message.chat.id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0] + "▌", business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    current = full_text[0]
    for char in full_text[1:]:
        current += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current + "▌", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current, business_connection_id=message.business_connection_id)


@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    if message.from_user.id == message.chat.id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text:
        return
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"
    sent_msg = await bot.send_message(chat_id=message.chat.id, text="...", business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    for i in range(len(full_text) + 1):
        await asyncio.sleep(0.2)
        correct = full_text[:i]
        rnd = "".join(random.choice(alphabet) for _ in range(len(full_text) - i))
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=correct + rnd, business_connection_id=message.business_connection_id)


# ==========================================
# ПАРСЕР ПОДАРКОВ
# ==========================================
NFT_LINK_PATTERN = r"(t\.me/nft/[a-zA-Z0-9_-]+)"


@dp.business_message(F.text.regexp(NFT_LINK_PATTERN))
@dp.message(F.text.regexp(NFT_LINK_PATTERN))
async def process_gift_link_auto(message: Message):
    conn_id = getattr(message, "business_connection_id", None)
    if conn_id and message.from_user.id == message.chat.id:
        return
    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", message.text)
    if not tg_match:
        return
    slug = tg_match.group(1)
    loading_msg = None
    with suppress(Exception):
        loading_msg = await bot.send_message(chat_id=message.chat.id, text="🔍 Анализирую...", business_connection_id=conn_id)
    data = await get_gift_card_data(slug)
    result_text = format_gift_card(data)
    if loading_msg:
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=loading_msg.message_id, text=result_text, parse_mode="HTML", business_connection_id=conn_id)


# ==========================================
# МАТЕМАТИКА
# ==========================================
@dp.business_message(F.text.regexp(AUTO_MATH_CURRENCY_PATTERN) | F.text.regexp(AUTO_MATH_PATTERN))
async def auto_math_and_currency(message: Message):
    if message.from_user.id == message.chat.id:
        return
    if message.text.lstrip().startswith("."):
        return
    match_curr = re.match(AUTO_MATH_CURRENCY_PATTERN, message.text)
    if match_curr:
        math_expr = match_curr.group(1).strip()
        tail = match_curr.group(2).lower()
        code = CURRENCY_ALIASES.get(tail)
        if not code:
            return
        if math_expr:
            raw = normalize_math_input(math_expr)
            parsed = parse_math_expression(raw)
            try:
                amount = float(simple_eval(parsed))
                formatted_expr = format_math_expression(parsed)
                await send_currency_conversion(message, amount, code, original_expr=formatted_expr if len(parsed.split()) > 1 else None)
            except Exception:
                return
    else:
        raw = normalize_math_input(message.text)
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
            await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
            await bot.send_message(chat_id=message.chat.id, text=f"{formatted} = {result}", business_connection_id=message.business_connection_id)


# ==========================================
# ГЛАВНЫЙ HANDLE
# ==========================================
@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id

    # Запоминаем владельца соединения
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if owner_data:
        _conn_owner_name[conn_id] = owner_data.get("first_name", "unknown")

    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        # Сохраняем в обычную коллекцию
        with suppress(Exception):
            await messages_collection.insert_one({
                "business_connection_id": conn_id, "message_id": message.message_id,
                "chat_id": chat_id, "user_id": message.from_user.id,
                "username": message.from_user.username or "",
                "first_name": message.from_user.first_name or "Без имени",
                "text": message.text or message.caption or "[Без текста]",
                "created_at": datetime.now(timezone.utc)
            })
        # В архив
        if owner_data:
            with suppress(Exception):
                await archive_message(message, conn_id, chat_id, owner_data["user_id"])
        return

    # Это сообщение от владельца
    owner_id = owner_data["user_id"] if owner_data else None
    if owner_id:
        owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
        manual_afk = owner_settings.get("is_afk", False)
        in_schedule = check_auto_afk(owner_settings.get("afk_start", 23), owner_settings.get("afk_end", 7)) if owner_settings.get("auto_afk", False) else False
        if manual_afk or in_schedule:
            now = datetime.now().timestamp()
            last_sent = afk_cooldowns.get((owner_id, chat_id), 0)
            if now - last_sent > 300:
                afk_text = owner_settings.get("afk_text", "Владелец сейчас занят. 💤")
                with suppress(Exception):
                    await bot.send_message(chat_id=chat_id, text=afk_text, business_connection_id=conn_id)
                afk_cooldowns[(owner_id, chat_id)] = now

    # Архив сообщения владельца
    if owner_id:
        with suppress(Exception):
            await archive_message(message, conn_id, chat_id, owner_id)

    # Мут
    mute_key = f"{conn_id}_{chat_id}"
    if mute_key in muted_chats:
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return

    with suppress(Exception):
        await messages_collection.insert_one({
            "business_connection_id": conn_id, "message_id": message.message_id,
            "chat_id": chat_id, "user_id": message.from_user.id,
            "username": message.from_user.username or "",
            "first_name": message.from_user.first_name or "Без имени",
            "text": message.text or message.caption or "[Без текста]",
            "created_at": datetime.now(timezone.utc)
        })


# ==========================================
# УДАЛЕНИЯ И РЕДАКТИРОВАНИЯ
# ==========================================
@dp.edited_business_message()
async def catch_edits(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id:
        return
    new_text = message.text or message.caption or "[Без текста]"
    with suppress(Exception):
        await archive_collection.update_many(
            {"conn_id": conn_id, "chat_id": chat_id, "message_id": message.message_id},
            {"$set": {"text": new_text, "edited_at": datetime.now(timezone.utc)}}
        )


@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    chat_id = deleted.chat.id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]

    for msg_id in deleted.message_ids:
        # Помечаем как удалённое в архиве
        with suppress(Exception):
            await archive_collection.update_many(
                {"conn_id": conn_id, "chat_id": chat_id, "message_id": msg_id},
                {"$set": {"is_deleted": True, "is_viewed": False, "deleted_at": datetime.now(timezone.utc)}}
            )

        # Запоминаем, что этот юзер удалял
        old_msg = await archive_collection.find_one({"conn_id": conn_id, "chat_id": chat_id, "message_id": msg_id})
        if old_msg and not old_msg.get("is_owner"):
            with suppress(Exception):
                await deleted_users_collection.update_one(
                    {"conn_id": conn_id, "chat_id": chat_id},
                    {"$set": {
                        "conn_id": conn_id, "chat_id": chat_id, "owner_id": owner_id,
                        "user_id": old_msg["user_id"],
                        "first_name": old_msg.get("first_name", "Без имени"),
                        "username": old_msg.get("username", ""),
                        "last_deleted_at": datetime.now(timezone.utc)
                    }},
                    upsert=True
                )

    # Дополнительно запоминаем sender'ов удалённых, если есть
    for msg_id in deleted.message_ids:
        old = await archive_collection.find_one({"conn_id": conn_id, "chat_id": chat_id, "message_id": msg_id})
        if old and not old.get("is_owner"):
            with suppress(Exception):
                await deleted_users_collection.update_one(
                    {"conn_id": conn_id, "chat_id": chat_id},
                    {"$set": {
                        "conn_id": conn_id, "chat_id": chat_id, "owner_id": owner_id,
                        "user_id": old["user_id"],
                        "first_name": old.get("first_name", "Без имени"),
                        "username": old.get("username", ""),
                    }},
                    upsert=True
                )


@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"
    if call.from_user.id == chat_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest):
            await call.answer("Нельзя", show_alert=True)
        return
    if mute_key in muted_chats or call.from_user.id == SUPERADMIN_ID:
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
