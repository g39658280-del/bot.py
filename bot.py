import os
import asyncio
import random
import html
import re
import json
import aiohttp
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
from TelegramGifts import TelegramGifts

# ==========================================
# КОНФИГ И БД
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")
SUPERADMIN_ID = 6548121776

# Gift Satellite API
GIFT_SATELLITE_TOKEN = os.environ.get(
    "GIFT_SATELLITE_TOKEN",
    "f5fca5357f0721d8f95fed1a3874b60ae097ee7500eb2a2e41cbfc74cc201683"
)
GIFT_SATELLITE_BASE = "https://gift-satellite.dev/api"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections']
    users_collection = db['users']
    history_collection = db['history']
except Exception as e:
    print(f"Ошибка БД: {e}")

muted_chats = set()
afk_cooldowns = {}

# ==========================================
# КУРСЫ ВАЛЮТ
# ==========================================
EXCHANGE_CACHE = {}
STARS_USD_RATE = 0.015
gift_parser: TelegramGifts | None = None

CURRENCY_ALIASES = {
    "usd": "USD", "доллар": "USD", "доллары": "USD", "долларов": "USD", "доллара": "USD",
    "бакс": "USD", "баксы": "USD", "баксов": "USD", "бакса": "USD", "бачей": "USD",
    "бач": "USD", "баксик": "USD", "баксиков": "USD", "зеленый": "USD", "зелёный": "USD",
    "зеленых": "USD", "зелёных": "USD", "юсд": "USD", "усд": "USD", "$": "USD",
    "rub": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "рубля": "RUB",
    "руб": "RUB", "рубас": "RUB", "рубасов": "RUB", "рубаса": "RUB",
    "деревянный": "RUB", "деревянных": "RUB", "₽": "RUB", "р": "RUB", "р.": "RUB",
    "eur": "EUR", "евро": "EUR", "еврик": "EUR", "евриков": "EUR", "евра": "EUR", "€": "EUR",
    "cny": "CNY", "юань": "CNY", "юани": "CNY", "юаней": "CNY", "юаня": "CNY",
    "yuan": "CNY", "женьминьби": "CNY", "жэньминьби": "CNY", "¥": "CNY",
    "btc": "BTC", "биткоин": "BTC", "биткоины": "BTC", "биткоинов": "BTC",
    "биткоина": "BTC", "биток": "BTC", "битки": "BTC", "битков": "BTC", "битка": "BTC",
    "биткойн": "BTC", "биткойнов": "BTC", "₿": "BTC",
    "ton": "TON", "gram": "TON", "gramm": "TON", "грам": "TON", "граммы": "TON",
    "граммов": "TON", "грамма": "TON", "тонов": "TON", "тона": "TON", "тон": "TON",
    "тоны": "TON", "тоник": "TON", "тоника": "TON", "тоников": "TON",
    "stars": "STARS", "star": "STARS", "звезд": "STARS", "звёзд": "STARS",
    "звезда": "STARS", "звёзда": "STARS", "звезды": "STARS", "звёзды": "STARS",
    "звёздочек": "STARS", "звездочек": "STARS", "звёздочки": "STARS", "⭐": "STARS", "🌟": "STARS",
    "eth": "ETH", "эфир": "ETH", "эфириум": "ETH", "эфира": "ETH", "эфиров": "ETH",
    "эфирка": "ETH", "эфирки": "ETH", "эфирок": "ETH", "Ξ": "ETH",
    "usdt": "USDT", "тетер": "USDT", "тетеры": "USDT", "тетеров": "USDT", "тетера": "USDT",
    "тезер": "USDT", "тезеры": "USDT", "тезеров": "USDT", "юста": "USDT",
    "юсдт": "USDT", "усдт": "USDT",
    "kzt": "KZT", "тенге": "KZT", "теньге": "KZT", "₸": "KZT", "тг": "KZT", "тг.": "KZT",
    "uah": "UAH", "гривна": "UAH", "гривны": "UAH", "гривен": "UAH", "гривне": "UAH",
    "гривня": "UAH", "₴": "UAH", "грн": "UAH", "грн.": "UAH",
    "gbp": "GBP", "фунт": "GBP", "фунты": "GBP", "фунтов": "GBP", "фунта": "GBP",
    "стерлинг": "GBP", "стерлингов": "GBP", "£": "GBP",
    "jpy": "JPY", "иена": "JPY", "иены": "JPY", "иен": "JPY", "йена": "JPY", "йены": "JPY",
}

ALL_CURRENCIES = ["RUB", "USD", "EUR", "CNY", "UAH", "KZT", "GBP", "JPY", "BTC", "ETH", "USDT", "TON", "STARS"]
_CURRENCY_ALT = "|".join(sorted([re.escape(a) for a in CURRENCY_ALIASES.keys()], key=len, reverse=True))

_NUM = r"\d+[.,]?\d*"
_OPS = r"[+\-*/xх^]"
_WORDS = r"(?:плюс|минус|умножить на|разделить на|поделить на|умножить|разделить|поделить|сложить|вычесть|делить|степень|в степени|х)"

AUTO_MATH_PATTERN = rf"^\s*{_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})+\s*$"
AUTO_MATH_CURRENCY_PATTERN = rf"^\s*({_NUM}(?:\s{{0,3}}(?:{_OPS}|{_WORDS})\s{{0,3}}{_NUM})*)\s{{0,3}}({_CURRENCY_ALT})\s*$"

WORD_NUMBERS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
    "девяносто": 90, "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900, "тысяча": 1000
}

OPERATORS = {
    "умножить на": "*", "разделить на": "/", "поделить на": "/", "в степени": "**",
    "плюс": "+", "сложить": "+", "минус": "-", "вычесть": "-",
    "умножить": "*", "х": "*", "разделить": "/", "делить": "/", "поделить": "/", "степень": "**",
}

# ==========================================
# GIFT SATELLITE API
# ==========================================

async def fetch_gift_satellite(endpoint: str, params: dict = None) -> dict | list | None:
    """Запрос к Gift Satellite API."""
    if not GIFT_SATELLITE_TOKEN:
        return None

    headers = {"Authorization": f"Token {GIFT_SATELLITE_TOKEN}"}
    url = f"{GIFT_SATELLITE_BASE}{endpoint}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    print(f"❌ GiftSatellite: неверный токен")
                elif resp.status == 429:
                    print(f"⚠️ GiftSatellite: rate limit")
                else:
                    print(f"GiftSatellite {endpoint} → {resp.status}")
    except Exception as e:
        print(f"GiftSatellite error: {e}")
    return None


async def get_gift_satellite_data(collection_name: str, slug: str = None) -> dict:
    """Собирает данные о коллекции через Gift Satellite."""
    result = {}

    # 1. Данные коллекции: атрибуты и редкость
    if collection_name:
        coll_data = await fetch_gift_satellite(f"/gift/collection/{collection_name}")
        if coll_data:
            result["collection_rarity"] = {
                "backdrops": coll_data.get("backdrops", []),
                "patterns": coll_data.get("patterns", []),
                "models": coll_data.get("models", []),
            }

    # 2. Данные конкретного подарка по slug
    if slug:
        slug_data = await fetch_gift_satellite(f"/gift/by-slug/{slug}")
        if slug_data:
            result["gift_details"] = slug_data

    # 3. Актуальные офферы на коллекцию (роут 2.7.1)
    if collection_name:
        offers = await fetch_gift_satellite(
            "/history/collection-offers",
            params={"collection": collection_name}
        )
        if offers:
            result["collection_offers"] = offers

    return result


# ==========================================
# TELEGRAM GIFTS
# ==========================================

async def get_gift_info(text: str) -> dict:
    """Извлекает данные о подарке через TelegramGifts."""
    if not gift_parser:
        return {}

    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", text)
    if not tg_match:
        return {}

    raw = tg_match.group(1)
    name = raw.replace("-", " ")
    name_clean = re.sub(r"\s+\d+$", "", name).strip()

    try:
        info = gift_parser.get_gift(name_clean)
        return info or {}
    except Exception as e:
        print(f"Gift lookup error: {e}")
        return {}


def format_gift_card(info: dict) -> str:
    """Формирует полную карточку подарка со всеми данными."""
    if not info:
        return "❌ Не удалось получить данные по этому подарку."

    name = info.get("full_name") or info.get("name") or "Подарок"
    prices = info.get("prices", {})

    floor_ton = prices.get("tgmrkt_price_ton") or prices.get("floor_price_ton")
    floor_usd = prices.get("tgmrkt_price_usd") or prices.get("floor_price_usd")
    avg_ton = prices.get("average_price_ton") or prices.get("avg_price_ton")
    last_ton = prices.get("last_sale_ton") or prices.get("last_price_ton")

    lines = [f"🎁 <b>{name}</b>"]

    # Атрибуты
    model = info.get("model")
    backdrop = info.get("backdrop")
    pattern = info.get("pattern")
    attrs = []
    if model:
        attrs.append(f"Модель: <b>{model}</b>")
    if backdrop:
        attrs.append(f"Фон: <b>{backdrop}</b>")
    if pattern:
        attrs.append(f"Узор: <b>{pattern}</b>")
    if attrs:
        lines.append(" · ".join(attrs))

    lines.append("")

    # Цены
    if floor_ton is not None:
        usd_part = f" ≈ {floor_usd:.2f} $" if floor_usd else ""
        lines.append(f"<b>Floor:</b> {floor_ton:.2f} TON{usd_part}")
    if avg_ton is not None:
        lines.append(f"<b>AVG (30д):</b> {avg_ton:.2f} TON")
    if last_ton is not None:
        lines.append(f"<b>Последняя продажа:</b> {last_ton:.2f} TON")

    # Редкость из Gift Satellite
    rarity = info.get("collection_rarity", {})
    model_r = backdrop_r = pattern_r = None

    if rarity:
        for m in rarity.get("models", []):
            if m.get("name") == model:
                model_r = m.get("rarityPermille", 0) / 10.0
                break
        for b in rarity.get("backdrops", []):
            if b.get("name") == backdrop:
                backdrop_r = b.get("rarityPermille", 0) / 10.0
                break
        for p in rarity.get("patterns", []):
            if p.get("name") == pattern:
                pattern_r = p.get("rarityPermille", 0) / 10.0
                break

    if any(x is not None for x in [model_r, backdrop_r, pattern_r]):
        lines.append("\n<b>Редкость:</b>")
        if model_r is not None:
            lines.append(f"▸ Модель: {model_r:.1f}%")
        if backdrop_r is not None:
            lines.append(f"▸ Фон: {backdrop_r:.1f}%")
        if pattern_r is not None:
            lines.append(f"▸ Узор: {pattern_r:.1f}%")

    # Офферы из Gift Satellite
    offers = info.get("collection_offers", [])
    if offers:
        lines.append("\n<b>Актуальные офферы (Portals):</b>")
        lines.append("<blockquote>")
        for offer in offers[:5]:
            price = offer.get("price_ton") or offer.get("price", 0)
            seller = offer.get("seller", "?")
            lines.append(f"🔘 {price:.2f} TON — {seller}")
        lines.append("</blockquote>")

    # История продаж из Gift Satellite (покупки)
    purchases = info.get("user_purchases", [])
    if purchases:
        lines.append("\n<b>История продаж модели:</b>")
        lines.append("<blockquote>")
        for p in purchases[:10]:
            item_slug = p.get("slug", "?")
            price = p.get("price", 0)
            date = p.get("timestamp", "")[:10] if p.get("timestamp") else "?"
            lines.append(f"🔘 {item_slug}: {price:.2f} TON — {date}")
        lines.append("</blockquote>")
    else:
        lines.append("\n<i>История продаж недоступна</i>")

    supply = info.get("supply")
    if supply:
        lines.append(f"\n<b>Выпуск:</b> {supply}")

    emoji_id = info.get("custom_emoji_id")
    if emoji_id:
        lines.append(f"<i>Custom Emoji ID:</i> <code>{emoji_id}</code>")

    lines.append("\n<i>Источник: Fragment / GetGems / TGMrkt / Gift Satellite</i>")
    return "\n".join(lines)


# ==========================================
# ОСНОВНАЯ ЛОГИКА
# ==========================================
def parse_math_expression(text: str) -> str:
    text = text.lower().strip()
    for word, op in sorted(OPERATORS.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", f" {op} ", text)

    words = text.split()
    result = []
    for word in words:
        clean = word.strip(".,!?;:")
        if clean in WORD_NUMBERS:
            result.append(str(WORD_NUMBERS[clean]))
        else:
            result.append(word)

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
            else:
                cg_id = {"BTC": "bitcoin", "ETH": "ethereum", "TON": "the-open-network"}.get(code)
                if cg_id:
                    cg_data = await fetch_json(
                        f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
                    )
                    if cg_data and cg_id in cg_data:
                        EXCHANGE_CACHE[code] = float(cg_data[cg_id]["usd"])

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
        return bool(r.photo or r.video or r.video_note or r.animation or
                    r.document or r.audio or r.voice)


class AdminStates(StatesGroup):
    waiting_for_broadcast = State()


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
                      "first_name": first_name or "Без имени"}},
            upsert=True
        )


def check_auto_afk(start_h: int, end_h: int) -> bool:
    now = datetime.now(timezone.utc)
    local_hour = (now.hour + 3) % 24
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
# STARTUP
# ==========================================
async def on_startup():
    global gift_parser

    with suppress(Exception):
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)

    asyncio.create_task(update_rates_loop())

    with suppress(Exception):
        gift_parser = TelegramGifts(ttl_seconds=300)
        print("✅ TelegramGifts инициализирован (авто-обновление каждые 5 минут)")

    # Проверка токена Gift Satellite
    with suppress(Exception):
        me = await fetch_gift_satellite("/user/me")
        if me:
            print(f"✅ Gift Satellite: {me.get('username')} (уровень {me.get('level')})")
        else:
            print("⚠️ Gift Satellite: токен не работает или сервис недоступен")

    with suppress(Exception):
        await bot.set_my_commands(
            [BotCommand(command="start", description="🏠 Главное меню")],
            scope=BotCommandScopeDefault()
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню"),
                BotCommand(command="admin", description="👑 Админ-панель")
            ],
            scope=BotCommandScopeChat(chat_id=SUPERADMIN_ID)
        )
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


dp.startup.register(on_startup)


# ==========================================
# МЕНЮ & АДМИНКА
# ==========================================
async def get_user_main_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    is_afk = user_data.get("is_afk", False)
    status_text = "🔴 ВЫКЛ" if not is_afk else "🟢 ВКЛ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💤 Автоответчик: {status_text}", callback_data="toggle_afk")],
        [InlineKeyboardButton(text="⚙️ Настройки автоответчика", callback_data="afk_settings")],
        [InlineKeyboardButton(text="💱 Валюты для конвертации", callback_data="currency_settings")],
        [InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes")],
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds")]
    ])


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


@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": message.from_user.id},
            {"$set": {"user_id": message.from_user.id}},
            upsert=True
        )
    kb = await get_user_main_kb(message.from_user.id)
    await message.answer(
        "👋 **Твой личный бот-секретарь.**\nУправляй статусом и настройками ниже:",
        reply_markup=kb, parse_mode="Markdown"
    )


@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = await get_user_main_kb(call.from_user.id)
    await call.message.edit_text("🏠 **Главное меню:**", reply_markup=kb, parse_mode="Markdown")


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
        await call.message.edit_text(
            "У тебя сейчас нет активных мутов.",
            reply_markup=builder.as_markup(), parse_mode="Markdown"
        )
    else:
        await call.message.edit_text(
            "🔇 **Твои активные муты:**\nНажми на кнопку, чтобы снять мут с собеседника.",
            reply_markup=builder.as_markup(), parse_mode="Markdown"
        )


@dp.callback_query(F.data.startswith("u_unmute_"))
async def user_unmute_callback(call: CallbackQuery):
    mute_key = call.data.replace("u_unmute_", "")
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.answer("✅ Мут успешно снят!", show_alert=True)
    else:
        await call.answer("Мут уже был снят.", show_alert=True)
    await user_mutes_handler(call)


@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("is_afk", False)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"is_afk": new_status}},
        upsert=True
    )
    kb = await get_user_main_kb(call.from_user.id)
    await call.message.edit_text("🏠 **Главное меню:**", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "toggle_auto_afk")
async def toggle_auto_afk(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("auto_afk", False)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"auto_afk": new_status}},
        upsert=True
    )
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text(
        f"Текущий текст:\n_{current}_\n\nОтправь новый текст автоответчика:",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )
    await state.set_state(UserStates.waiting_for_afk_text)


@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    await users_collection.update_one(
        {"user_id": message.from_user.id},
        {"$set": {"afk_text": message.text}}
    )
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Текст сохранен!", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text(
        "Отправь время включения и выключения в часах через пробел или дефис.\nПример: `23 7`",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
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
            await message.answer("✅ Время сохранено!", reply_markup=kb)
            await state.clear()
        else:
            await message.answer("⚠ Ошибка: часы должны быть от 0 до 23.")
    except Exception:
        await message.answer("⚠️ Неверный формат. Напиши просто две цифры: `23 7`")


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
    await call.message.edit_text(
        "💱 <b>Выбери валюты для отображения</b>:",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("cur_toggle_"))
async def currency_toggle_handler(call: CallbackQuery):
    cur = call.data.replace("cur_toggle_", "")
    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON"])

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


@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    text = (
        "📖 **Список команд (писать в чатах):**\n\n"
        "🚫 `.мут` — удаляет сообщения собеседника\n"
        "💣 `.[число] [текст]` — спам сообщением\n"
        "🎭 `.п1`, `.п2`, `.п3` — анимации печати\n"
        "🧮 **Математика/Валюта:** `5+3`, `5 баксов`\n"
        "🎁 **Подарки:** бот автоматически ловит ссылки t.me/nft/...\n"
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
    else:
        with suppress(Exception):
            await connections_collection.delete_one({"business_connection_id": connection.id})


@dp.business_message(ReplyHasMedia())
async def auto_save_replied_media(message: Message):
    """Сохраняет ТОЛЬКО защищённые (предположительно одноразовые) медиа при reply."""
    if message.from_user.id == message.chat.id:
        return

    reply = message.reply_to_message
    if not reply:
        return

    if not is_protected_media(reply):
        return

    file_id, media_kind, filename = None, None, "saved"
    if reply.photo:
        file_id, media_kind, filename = reply.photo[-1].file_id, "photo", "saved.jpg"
    elif reply.video:
        file_id, media_kind, filename = reply.video.file_id, "video", "saved.mp4"
    elif reply.video_note:
        file_id, media_kind, filename = reply.video_note.file_id, "video_note", "saved_note.mp4"
    elif reply.animation:
        file_id, media_kind, filename = reply.animation.file_id, "animation", "saved.mp4"
    elif reply.document:
        file_id, media_kind, filename = reply.document.file_id, "document", reply.document.file_name or "saved.bin"
    elif reply.audio:
        file_id, media_kind, filename = reply.audio.file_id, "audio", "saved.mp3"
    elif reply.voice:
        file_id, media_kind, filename = reply.voice.file_id, "voice", "saved.ogg"

    if not file_id:
        return

    owner_id = message.from_user.id
    sender_name = (
        reply.from_user.first_name or reply.from_user.username or "Без имени"
        if reply.from_user else "Неизвестно"
    )
    caption = reply.caption or ""
    header = f"🕵️ <b>Сохранено от {sender_name}</b>"

    try:
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        input_file = BufferedInputFile(buffer.read(), filename=filename)

        if media_kind == "photo":
            await bot.send_photo(chat_id=owner_id, photo=input_file,
                                 caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "video":
            await bot.send_video(chat_id=owner_id, video=input_file,
                                 caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "video_note":
            await bot.send_video_note(chat_id=owner_id, video_note=input_file)
            await bot.send_message(chat_id=owner_id, text=header, parse_mode="HTML")
        elif media_kind == "animation":
            await bot.send_animation(chat_id=owner_id, animation=input_file,
                                     caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "document":
            await bot.send_document(chat_id=owner_id, document=input_file,
                                    caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "audio":
            await bot.send_audio(chat_id=owner_id, audio=input_file,
                                 caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "voice":
            await bot.send_voice(chat_id=owner_id, voice=input_file)
            await bot.send_message(chat_id=owner_id, text=header, parse_mode="HTML")

    except Exception as e:
        with suppress(Exception):
            await bot.send_message(chat_id=owner_id, text=f"❌ Не удалось сохранить: <code>{e}</code>", parse_mode="HTML")
        return

    with suppress(Exception):
        await history_collection.insert_one({
            "owner_id": owner_id,
            "text": f"🕵️ Авто-сейв медиа от {sender_name} ({media_kind})",
            "ts": datetime.now(timezone.utc)
        })


@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id

    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        with suppress(Exception):
            await bot.delete_business_messages(
                business_connection_id=conn_id,
                message_ids=[message.message_id]
            )

        mute_key = f"{conn_id}_{chat_id}"
        if mute_key in muted_chats:
            return

        muted_chats.add(mute_key)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]
        ])
        await bot.send_message(
            chat_id=message.chat.id,
            text="мут выдан",
            reply_markup=markup,
            business_connection_id=conn_id
        )


@dp.business_message(F.text.regexp(r"^\.(\d+)\s+"))
async def spam_command(message: Message):
    if message.from_user.id == message.chat.id:
        return

    match = re.match(r"^\.(\d+)\s+(.*)", message.text, re.DOTALL)
    if not match:
        return

    count = min(int(match.group(1)), 50)
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    for _ in range(count):
        with suppress(Exception):
            await bot.send_message(
                chat_id=message.chat.id,
                text=match.group(2),
                business_connection_id=message.business_connection_id
            )
        await asyncio.sleep(0.5)


# ==========================================
# АВТО-ПАРСЕР ПОДАРКОВ С GIFT SATELLITE
# ==========================================
NFT_LINK_PATTERN = r"(t\.me/nft/[a-zA-Z0-9_-]+|getgems\.io/collection/[\w-]+/[\w-]+|[EU]Q[a-zA-Z0-9_-]{46})"


@dp.business_message(F.text.regexp(NFT_LINK_PATTERN))
@dp.message(F.text.regexp(NFT_LINK_PATTERN))
async def process_gift_link_auto(message: Message):
    conn_id = getattr(message, "business_connection_id", None)

    if conn_id and message.from_user.id == message.chat.id:
        return

    loading_msg = None
    with suppress(Exception):
        loading_msg = await bot.send_message(
            chat_id=message.chat.id,
            text="🔍 Ищу данные по подарку...",
            business_connection_id=conn_id
        )

    # 1. Базовые данные из TelegramGifts
    tg_info = await get_gift_info(message.text) or {}

    # 2. Извлекаем collection name и slug из ссылки
    collection_name = None
    slug = None

    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", message.text)
    if tg_match:
        raw = tg_match.group(1)
        name = raw.replace("-", " ")
        collection_name = re.sub(r"\s+\d+$", "", name).strip()
        # Формируем slug: "LootBag-10251"
        parts = raw.rsplit("-", 1)
        if len(parts) == 2:
            slug = f"{parts[0]}-{parts[1]}"

    # 3. Данные из Gift Satellite
    gs_data = await get_gift_satellite_data(collection_name, slug) if collection_name else {}

    # 4. Мержим всё вместе
    combined = {**tg_info, **gs_data}
    result_text = format_gift_card(combined)

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
# ОСТАЛЬНЫЕ ХЭНДЛЕРЫ
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
                await send_currency_conversion(
                    message, amount, code,
                    original_expr=formatted_expr if len(parsed.split()) > 1 else None
                )
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
            await bot.delete_business_messages(
                business_connection_id=message.business_connection_id,
                message_ids=[message.message_id]
            )
            await bot.send_message(
                chat_id=message.chat.id,
                text=f"{formatted} = {result}",
                business_connection_id=message.business_connection_id
            )


@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id

    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        return

    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if owner_data:
        owner_id = owner_data["user_id"]
        owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
        manual_afk = owner_settings.get("is_afk", False)
        in_schedule = (
            check_auto_afk(owner_settings.get("afk_start", 23), owner_settings.get("afk_end", 7))
            if owner_settings.get("auto_afk", False) else False
        )

        if manual_afk or in_schedule:
            now = datetime.now().timestamp()
            last_sent = afk_cooldowns.get((owner_id, chat_id), 0)
            if now - last_sent > 300:
                afk_text = owner_settings.get("afk_text", "Владелец сейчас занят и ответит позже. 💤")
                with suppress(Exception):
                    await bot.send_message(
                        chat_id=chat_id,
                        text=afk_text,
                        business_connection_id=conn_id
                    )
                afk_cooldowns[(owner_id, chat_id)] = now

    mute_key = f"{conn_id}_{chat_id}"
    if mute_key in muted_chats:
        with suppress(Exception):
            await bot.delete_business_messages(
                business_connection_id=conn_id,
                message_ids=[message.message_id]
            )
        return

    with suppress(Exception):
        await messages_collection.insert_one({
            "business_connection_id": conn_id,
            "message_id": message.message_id,
            "chat_id": chat_id,
            "user_id": message.from_user.id,
            "username": message.from_user.username or "нет_юзернейма",
            "first_name": message.from_user.first_name or "Без имени",
            "text": message.text or message.caption or "[Без текста]",
            "created_at": datetime.now(timezone.utc)
        })


@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"

    if call.from_user.id == chat_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest):
            await call.answer("вы не можете снять мут", show_alert=True)
        return

    if mute_key in muted_chats or call.from_user.id == SUPERADMIN_ID:
        if mute_key in muted_chats:
            muted_chats.remove(mute_key)
        with suppress(TelegramBadRequest):
            await call.message.edit_text("мут снят")
            await call.answer("снял")
    else:
        with suppress(TelegramBadRequest):
            await call.message.edit_text("уже снял")


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
