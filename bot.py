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
    BufferedInputFile, BotCommand, MenuButtonCommands, BotCommandScopeDefault, BotCommandScopeChat
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from simpleeval import simple_eval

# ==========================================
# КОНФИГ И БД
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")
SUPERADMIN_ID = 6548121776

# ВСТАВЬ СЮДА КЛЮЧ TONAPI (tonconsole.com)
TONAPI_KEY = os.environ.get("TONAPI_KEY", "СЮДА_ВСТАВЬ_КЛЮЧ_TONAPI")

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

CURRENCY_ALIASES = {
    "usd": "USD", "доллар": "USD", "доллары": "USD", "долларов": "USD", "доллара": "USD", "бакс": "USD", "баксы": "USD", "баксов": "USD", "бакса": "USD", "бачей": "USD", "бач": "USD", "баксик": "USD", "баксиков": "USD", "зеленый": "USD", "зелёный": "USD", "зеленых": "USD", "зелёных": "USD", "юсд": "USD", "усд": "USD", "$": "USD",
    "rub": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "рубля": "RUB", "руб": "RUB", "рубас": "RUB", "рубасов": "RUB", "рубаса": "RUB", "деревянный": "RUB", "деревянных": "RUB", "₽": "RUB", "р": "RUB", "р.": "RUB",
    "eur": "EUR", "евро": "EUR", "еврик": "EUR", "евриков": "EUR", "евра": "EUR", "€": "EUR",
    "cny": "CNY", "юань": "CNY", "юани": "CNY", "юаней": "CNY", "юаня": "CNY", "yuan": "CNY", "женьминьби": "CNY", "жэньминьби": "CNY", "¥": "CNY",
    "btc": "BTC", "биткоин": "BTC", "биткоины": "BTC", "биткоинов": "BTC", "биткоина": "BTC", "биток": "BTC", "битки": "BTC", "битков": "BTC", "битка": "BTC", "биткойн": "BTC", "биткойнов": "BTC", "₿": "BTC",
    "ton": "TON", "gram": "TON", "gramm": "TON", "грам": "TON", "граммы": "TON", "граммов": "TON", "грамма": "TON", "тонов": "TON", "тона": "TON", "тон": "TON", "тоны": "TON", "тоник": "TON", "тоника": "TON", "тоников": "TON",
    "stars": "STARS", "star": "STARS", "звезд": "STARS", "звёзд": "STARS", "звезда": "STARS", "звёзда": "STARS", "звезды": "STARS", "звёзды": "STARS", "звёздочек": "STARS", "звездочек": "STARS", "звёздочки": "STARS", "⭐": "STARS", "🌟": "STARS",
    "eth": "ETH", "эфир": "ETH", "эфириум": "ETH", "эфира": "ETH", "эфиров": "ETH", "эфирка": "ETH", "эфирки": "ETH", "эфирок": "ETH", "Ξ": "ETH",
    "usdt": "USDT", "тетер": "USDT", "тетеры": "USDT", "тетеров": "USDT", "тетера": "USDT", "тезер": "USDT", "тезеры": "USDT", "тезеров": "USDT", "юста": "USDT", "юсдт": "USDT", "усдт": "USDT",
    "kzt": "KZT", "тенге": "KZT", "теньге": "KZT", "₸": "KZT", "тг": "KZT", "тг.": "KZT",
    "uah": "UAH", "гривна": "UAH", "гривны": "UAH", "гривен": "UAH", "гривне": "UAH", "гривня": "UAH", "₴": "UAH", "грн": "UAH", "грн.": "UAH",
    "gbp": "GBP", "фунт": "GBP", "фунты": "GBP", "фунтов": "GBP", "фунта": "GBP", "стерлинг": "GBP", "стерлингов": "GBP", "£": "GBP",
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
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900, "тысяча": 1000
}

OPERATORS = {
    "умножить на": "*", "разделить на": "/", "поделить на": "/", "в степени": "**",
    "плюс": "+", "сложить": "+", "минус": "-", "вычесть": "-",
    "умножить": "*", "х": "*", "разделить": "/", "делить": "/", "поделить": "/", "степень": "**",
}

# ==========================================
# НОВЫЙ БЛОК: NFT АВТО-ПАРСИНГ
# ==========================================

async def resolve_link_to_address(session: aiohttp.ClientSession, text: str) -> str:
    """Умный поиск TON-адреса NFT из любого текста или ссылки"""
    # 1. Прямой TON-адрес
    ton_match = re.search(r"([EU]Q[a-zA-Z0-9_-]{46})", text)
    if ton_match:
        return ton_match.group(1)

    # 2. Ссылка Getgems
    gg_match = re.search(r"getgems\.io/collection/[^/]+/([a-zA-Z0-9_-]+)", text)
    if gg_match:
        return gg_match.group(1)

    # 3. Ссылка Телеграм (t.me/nft/LootBag-10251)
    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", text)
    if tg_match:
        slug = tg_match.group(1).replace("-", " ") # Превращаем в "LootBag 10251"
        url = "https://api.getgems.io/graphql"
        query = """
        query Search($query: String!) {
          alphaNftItemSearch(query: $query, first: 1) {
            edges { node { address } }
          }
        }
        """
        try:
            async with session.post(url, json={"query": query, "variables": {"query": slug}}) as resp:
                data = await resp.json()
                edges = data.get("data", {}).get("alphaNftItemSearch", {}).get("edges", [])
                if edges:
                    return edges[0]["node"]["address"]
        except Exception:
            pass

    return None

async def get_live_ton_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()
                return data.get("the-open-network", {}).get("usd", 0.0)
    except Exception:
        return 0.0

async def get_getgems_floor(collection_address: str, model_name: str) -> float:
    url = "https://api.getgems.io/graphql"
    query = """
    query NftSearch($collection: String!, $attributes: String!) {
      alphaNftItemSearch(collectionAddress: $collection, filters: { attributes: $attributes }, sort: PRICE_ASC, first: 1) {
        edges { node { sale { ... on NftSaleFixPrice { fullPrice } } } }
      }
    }
    """
    attributes_filter = json.dumps([{"value": model_name, "trait_type": "Model"}])
    variables = {"collection": collection_address, "attributes": attributes_filter}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"query": query, "variables": variables}) as response:
                data = await response.json()
                edges = data.get("data", {}).get("alphaNftItemSearch", {}).get("edges", [])
                if edges:
                    sale = edges[0].get("node", {}).get("sale")
                    if sale and "fullPrice" in sale:
                        return int(sale["fullPrice"]) / 10**9
        return 0.0
    except Exception:
        return 0.0

async def get_real_nft_data(nft_address: str) -> dict:
    headers = {"Authorization": f"Bearer {TONAPI_KEY}"}
    nft_url = f"https://tonapi.io/v2/nfts/{nft_address}"
    events_url = f"https://tonapi.io/v2/events?account_id={nft_address}&limit=20"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(nft_url, headers=headers) as resp1:
                if resp1.status != 200: return {}
                nft_raw = await resp1.json()
                
            async with session.get(events_url, headers=headers) as resp2:
                events_raw = await resp2.json()

        attributes = nft_raw.get("metadata", {}).get("attributes", [])
        model_name, bg_name, pat_name = "Unknown", "Classic", "Standard"
        
        for attr in attributes:
            t_type = attr.get("trait_type")
            if t_type == "Model": model_name = attr.get("value")
            elif t_type == "Backdrop": bg_name = attr.get("value")
            elif t_type == "Pattern": pat_name = attr.get("value")

        collection_address = nft_raw.get("collection", {}).get("address", "")
        floor_price_ton = await get_getgems_floor(collection_address, model_name) if collection_address and model_name != "Unknown" else 0.0

        sales_history = []
        for event in events_raw.get("events", []):
            for action in event.get("actions", []):
                if action.get("type") == "SmartContractExec":
                    ton_amount = action.get("ton_transfer", {}).get("amount", 0)
                    if ton_amount > 0:
                        date_str = datetime.fromtimestamp(event["timestamp"]).strftime("%d %b")
                        sales_history.append({
                            "item_id": nft_raw.get("index", 0),
                            "price": ton_amount / 10**9,
                            "date": date_str
                        })

        return {
            "model": model_name,
            "collection": nft_raw.get("collection", {}).get("name", "NFT"),
            "item_id": nft_raw.get("index", 0),
            "model_count": "?", 
            "model_percent": 0.0,
            "backdrop": {"name": bg_name, "emoji": "🔵", "percent": 0.0},
            "pattern": {"name": pat_name, "emoji": "🦅", "percent": 0.0},
            "floor_ton": floor_price_ton,
            "avg_ton": floor_price_ton, 
            "last_sale_ton": sales_history[0]["price"] if sales_history else 0.0,
            "sales_history": sales_history
        }
    except Exception as e:
        print(f"Ошибка парсинга NFT: {e}")
        return {}

def format_nft_card(data: dict, ton_usd_price: float) -> str:
    if not data:
        return "❌ Не удалось получить данные по этому NFT. Проверьте адрес или ключ TonAPI."
        
    model = data.get("model", "Unknown")
    collection = data.get("collection", "NFT")
    item_id = data.get("item_id", 0)
    
    bg_name = data.get("backdrop", {}).get("name", "Classic")
    bg_emoji = data.get("backdrop", {}).get("emoji", "🔵")
    pat_name = data.get("pattern", {}).get("name", "Standard")
    pat_emoji = data.get("pattern", {}).get("emoji", "🦅")
    
    floor_ton = data.get("floor_ton", 0.0)
    avg_ton = data.get("avg_ton", 0.0)
    last_sale_ton = data.get("last_sale_ton", 0.0)
    
    # Сборка текста
    lines = [f"🛍 <b>{model} ({collection}) #{item_id}</b>"]
    
    # Добавляем атрибуты только если они нестандартные (чтобы обычные NFT тоже красиво выводились)
    if bg_name != "Classic" or pat_name != "Standard":
        lines.append(f"Фон: {bg_emoji} <b>{bg_name}</b>, Узор: {pat_emoji} <b>{pat_name}</b>")
    
    lines.extend([
        "",
        f"<b>Floor:</b> {floor_ton:.1f} 💎  ≈ {floor_ton * ton_usd_price:.1f} $",
        f"<b>AVG:</b> {avg_ton:.1f} 💎  ≈ {avg_ton * ton_usd_price:.1f} $",
        f"<b>Последняя продажа:</b> {last_sale_ton:.1f} 💎  ≈ {last_sale_ton * ton_usd_price:.1f} $\n",
        "<b>История продаж модели:</b>",
        "<blockquote>"
    ])
    
    sales_history = data.get("sales_history", [])
    if sales_history:
        for sale in sales_history[:10]:
            lines.append(f"🔘 #{sale['item_id']}: {sale['price']:.1f} 💎 — {sale['date']}")
    else:
        lines.append("<i>Продаж пока не зафиксировано</i>")
        
    lines.append("</blockquote>")
    return "\n".join(lines)


# ==========================================
# ОСНОВНАЯ ЛОГИКА И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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
                if resp.status == 200: return await resp.json()
    except Exception:
        pass
    return None

async def force_update_all_rates():
    try:
        fiat_data = await fetch_json("https://open.er-api.com/v6/latest/USD")
        if fiat_data and "rates" in fiat_data:
            rates = fiat_data["rates"]
            for cur in ["RUB", "EUR", "CNY", "UAH", "KZT", "GBP", "JPY"]:
                if rates.get(cur): EXCHANGE_CACHE[cur] = 1.0 / rates[cur]

        for sym, code in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("TONUSDT", "TON")]:
            data = await fetch_json(f"https://api.mexc.com/api/v3/ticker/price?symbol={sym}")
            if data and "price" in data:
                EXCHANGE_CACHE[code] = float(data["price"]) 
            else:
                cg_id = {"BTC": "bitcoin", "ETH": "ethereum", "TON": "the-open-network"}.get(code)
                if cg_id:
                    cg_data = await fetch_json(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
                    if cg_data and cg_id in cg_data: EXCHANGE_CACHE[code] = float(cg_data[cg_id]["usd"])

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
    if from_cur == to_cur: return amount
    r_from = EXCHANGE_CACHE.get(from_cur)
    r_to = EXCHANGE_CACHE.get(to_cur)
    if r_from is None or r_to is None: return None
    return (amount * r_from) / r_to

async def send_currency_conversion(message: Message, amount: float, from_cur: str, original_expr: str = None):
    owner_id = message.from_user.id
    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    display_currencies = owner_settings.get("display_currencies", ["RUB", "STARS", "TON"])
    
    amount_str = f"{amount:.4f}".rstrip("0").rstrip(".") if isinstance(amount, float) else str(amount)
    lines = [f"💱 <b>{original_expr}</b> = <b>{amount_str} {from_cur}</b>:\n"] if original_expr else [f"💱 <b>{amount_str} {from_cur}</b>:\n"]

    for target in display_currencies:
        if target == from_cur: continue
        result = await convert_currency(amount, from_cur, target)
        if result is None: continue
        if target in ("BTC", "ETH", "TON"): formatted = f"{result:.6f}".rstrip("0").rstrip(".")
        elif target == "STARS": formatted = f"{result:.0f}"
        else: formatted = f"{result:.2f}"
        
        emoji = {"RUB": "🇷🇺", "USD": "🇺🇸", "EUR": "🇪🇺", "CNY": "🇨🇳", "UAH": "🇺🇦", "KZT": "🇰🇿", "GBP": "🇬🇧", "JPY": "🇯🇵", "BTC": "₿", "ETH": "Ξ", "USDT": "💵", "TON": "💎", "STARS": "⭐"}.get(target, "•")
        lines.append(f"{emoji} <b>{formatted}</b> {target}")
    with suppress(Exception):
        await message.reply("\n".join(lines), parse_mode="HTML")

class ReplyHasMedia(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        r = message.reply_to_message
        if not r: return False
        return bool(r.photo or r.video or r.video_note or r.animation or r.document or r.audio or r.voice)

class AdminStates(StatesGroup): waiting_for_broadcast = State()
class UserStates(StatesGroup): waiting_for_afk_text = State(); waiting_for_afk_time = State()

async def ensure_connection(conn_id: str, user_id: int, first_name: str):
    with suppress(Exception):
        await connections_collection.update_one(
            {"business_connection_id": conn_id},
            {"$set": {"business_connection_id": conn_id, "user_id": user_id, "first_name": first_name or "Без имени"}},
            upsert=True
        )

def check_auto_afk(start_h: int, end_h: int) -> bool:
    now = datetime.now(timezone.utc)
    local_hour = (now.hour + 3) % 24
    if start_h < end_h: return start_h <= local_hour < end_h
    return local_hour >= start_h or local_hour < end_h

async def dummy_handler(request): return web.Response(text="Multi-bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def on_startup():
    with suppress(Exception): await messages_collection.create_index("created_at", expireAfterSeconds=172800)
    asyncio.create_task(update_rates_loop())
    with suppress(Exception):
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Главное меню")], scope=BotCommandScopeDefault())
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Главное меню"), BotCommand(command="admin", description="👑 Админ-панель")], scope=BotCommandScopeChat(chat_id=SUPERADMIN_ID))
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
    with suppress(Exception): await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"user_id": message.from_user.id}}, upsert=True)
    kb = await get_user_main_kb(message.from_user.id)
    await message.answer("👋 **Твой личный бот-секретарь.**\nУправляй статусом и настройками ниже:", reply_markup=kb, parse_mode="Markdown")

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
        except Exception: continue
    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    if not has_mutes: await call.message.edit_text("У тебя сейчас нет активных мутов.", reply_markup=builder.as_markup(), parse_mode="Markdown")
    else: await call.message.edit_text("🔇 **Твои активные муты:**\nНажми на кнопку, чтобы снять мут с собеседника.", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("u_unmute_"))
async def user_unmute_callback(call: CallbackQuery):
    mute_key = call.data.replace("u_unmute_", "")
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.answer("✅ Мут успешно снят!", show_alert=True)
    else: await call.answer("Мут уже был снят.", show_alert=True)
    await user_mutes_handler(call)

@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("is_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"is_afk": new_status}}, upsert=True)
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
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": new_status}}, upsert=True)
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text(f"Текущий текст:\n_{current}_\n\nОтправь новый текст автоответчика:", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_afk_text)

@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Текст сохранен!", reply_markup=kb)
    await state.clear()

@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text("Отправь время включения и выключения в часах через пробел или дефис.\nПример: `23 7`", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_afk_time)

@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
    try:
        parts = message.text.replace("-", " ").split()
        start_h, end_h = int(parts[0]), int(parts[1])
        if 0 <= start_h <= 23 and 0 <= end_h <= 23:
            await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_start": start_h, "afk_end": end_h}})
            kb = await get_afk_settings_kb(message.from_user.id)
            await message.answer("✅ Время сохранено!", reply_markup=kb)
            await state.clear()
        else: await message.answer("⚠ Ошибка: часы должны быть от 0 до 23.")
    except: await message.answer("⚠️ Неверный формат. Напиши просто две цифры: `23 7`")

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
    await call.message.edit_text("💱 <b>Выбери валюты для отображения</b>:", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("cur_toggle_"))
async def currency_toggle_handler(call: CallbackQuery):
    cur = call.data.replace("cur_toggle_", "")
    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON"])
    if cur in display: display.remove(cur)
    else: display.append(cur)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"display_currencies": display}}, upsert=True)
    await currency_settings_handler(call)

@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    text = (
        "📖 **Список команд (писать в чатах):**\n\n"
        "🚫 `.мут` — удаляет сообщения собеседника\n"
        "💣 `.[число] [текст]` — спам сообщением\n"
        "🎭 `.п1`, `.п2`, `.п3` — анимации печати\n"
        "🧮 **Математика/Валюта:** `5+3`, `5 баксов`\n"
        "🖼 **NFT:** бот автоматически ловит ссылки t.me/nft/...\n"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main")
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())

# ==========================================
# БИЗНЕС-ЛОГИКА (МУТЫ, АНИМАЦИИ, СПАМ)
# ==========================================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled: await ensure_connection(connection.id, connection.user.id, connection.user.first_name)
    else:
        with suppress(Exception): await connections_collection.delete_one({"business_connection_id": connection.id})

@dp.business_message(ReplyHasMedia())
async def auto_save_replied_media(message: Message):
    if message.from_user.id == message.chat.id:
        return
    reply = message.reply_to_message
    if not reply:
        return

    file_id, media_kind, filename = None, None, "saved"
    if reply.photo: file_id, media_kind, filename = reply.photo[-1].file_id, "photo", "saved.jpg"
    elif reply.video: file_id, media_kind, filename = reply.video.file_id, "video", "saved.mp4"
    elif reply.video_note: file_id, media_kind, filename = reply.video_note.file_id, "video_note", "saved_note.mp4"
    elif reply.animation: file_id, media_kind, filename = reply.animation.file_id, "animation", "saved.mp4"
    elif reply.document: file_id, media_kind, filename = reply.document.file_id, "document", reply.document.file_name or "saved.bin"
    elif reply.audio: file_id, media_kind, filename = reply.audio.file_id, "audio", "saved.mp3"
    elif reply.voice: file_id, media_kind, filename = reply.voice.file_id, "voice", "saved.ogg"

    if not file_id: return
    owner_id = message.from_user.id
    sender_name = reply.from_user.first_name or reply.from_user.username or "Без имени" if reply.from_user else "Неизвестно"
    caption, header = reply.caption or "", f"🕵️ <b>Сохранено от {sender_name}</b>"

    try:
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        input_file = BufferedInputFile(buffer.read(), filename=filename)
        if media_kind == "photo": await bot.send_photo(chat_id=owner_id, photo=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "video": await bot.send_video(chat_id=owner_id, video=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "video_note": await bot.send_video_note(chat_id=owner_id, video_note=input_file); await bot.send_message(chat_id=owner_id, text=header, parse_mode="HTML")
        elif media_kind == "animation": await bot.send_animation(chat_id=owner_id, animation=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "document": await bot.send_document(chat_id=owner_id, document=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "audio": await bot.send_audio(chat_id=owner_id, audio=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "voice": await bot.send_voice(chat_id=owner_id, voice=input_file); await bot.send_message(chat_id=owner_id, text=header, parse_mode="HTML")
    except Exception as e:
        with suppress(Exception): await bot.send_message(chat_id=owner_id, text=f"❌ Не удалось сохранить: <code>{e}</code>", parse_mode="HTML")
        return

    with suppress(Exception): await history_collection.insert_one({"owner_id": owner_id, "text": f"🕵️ Авто-сейв медиа от {sender_name} ({media_kind})", "ts": datetime.now(timezone.utc)})

@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        mute_key = f"{conn_id}_{chat_id}"
        if mute_key in muted_chats: return
        muted_chats.add(mute_key)
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]])
        await bot.send_message(chat_id=message.chat.id, text="мут выдан", reply_markup=markup, business_connection_id=conn_id)

@dp.business_message(F.text.regexp(r"^\.(\d+)\s+"))
async def spam_command(message: Message):
    if message.from_user.id == message.chat.id: return
    match = re.match(r"^\.(\d+)\s+(.*)", message.text, re.DOTALL)
    if not match: return
    count = min(int(match.group(1)), 50)
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    for _ in range(count):
        with suppress(Exception): await bot.send_message(chat_id=message.chat.id, text=match.group(2), business_connection_id=message.business_connection_id)
        await asyncio.sleep(0.2)

# ==========================================
# НОВАЯ ФИШКА: АВТО-ПАРСЕР ССЫЛОК
# ==========================================
NFT_LINK_PATTERN = r"(t\.me/nft/[a-zA-Z0-9_-]+|getgems\.io/collection/[\w-]+/[\w-]+|[EU]Q[a-zA-Z0-9_-]{46})"

@dp.business_message(F.text.regexp(NFT_LINK_PATTERN))
@dp.message(F.text.regexp(NFT_LINK_PATTERN))
async def process_nft_link_auto(message: Message):
    conn_id = getattr(message, 'business_connection_id', None)
    
    # Не перехватываем свои же сообщения в личке или бизнес-чате (чтобы не лупить парсер на свои рассылки)
    if conn_id and message.from_user.id == message.chat.id:
        return

    loading_msg = None
    with suppress(Exception):
        loading_msg = await bot.send_message(
            chat_id=message.chat.id,
            text="🔍 Заметил NFT ссылку. Ищу данные...",
            business_connection_id=conn_id
        )

    async with aiohttp.ClientSession() as session:
        # 1. Сам находит адрес (Парсит ссылку и делает запрос к GetGems GraphQL, если нужно)
        nft_address = await resolve_link_to_address(session, message.text)
        
        if not nft_address:
            if loading_msg:
                with suppress(Exception):
                    await bot.delete_message(chat_id=message.chat.id, message_id=loading_msg.message_id, business_connection_id=conn_id)
            return

        # 2. Вытягивает инфу и цены
        ton_price = await get_live_ton_price()
        nft_data = await get_real_nft_data(nft_address)
        
        # 3. Собирает карточку
        result_text = format_nft_card(nft_data, ton_price)

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
    if message.from_user.id == message.chat.id: return
    if message.text.lstrip().startswith("."): return
    match_curr = re.match(AUTO_MATH_CURRENCY_PATTERN, message.text)
    if match_curr:
        math_expr, tail = match_curr.group(1).strip(), match_curr.group(2).lower()
        code = CURRENCY_ALIASES.get(tail)
        if not code: return
        if math_expr:
            raw = normalize_math_input(math_expr)
            parsed = parse_math_expression(raw)
            try:
                amount = float(simple_eval(parsed))
                formatted_expr = format_math_expression(parsed)
                await send_currency_conversion(message, amount, code, original_expr=formatted_expr if len(parsed.split()) > 1 else None)
            except Exception: return
    else:
        raw = normalize_math_input(message.text)
        parsed = parse_math_expression(raw)
        if not parsed or len(parsed.split()) < 3: return
        try:
            result = simple_eval(parsed)
            if isinstance(result, float):
                result = int(result) if round(result, 4).is_integer() else round(result, 4)
        except Exception: return
        formatted = format_math_expression(parsed)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
            await bot.send_message(chat_id=message.chat.id, text=f"{formatted} = {result}", business_connection_id=message.business_connection_id)

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
        in_schedule = check_auto_afk(owner_settings.get("afk_start", 23), owner_settings.get("afk_end", 7)) if owner_settings.get("auto_afk", False) else False
        
        if manual_afk or in_schedule:
            now = datetime.now().timestamp()
            last_sent = afk_cooldowns.get((owner_id, chat_id), 0)
            if now - last_sent > 300: 
                afk_text = owner_settings.get("afk_text", "Владелец сейчас занят и ответит позже. 💤")
                with suppress(Exception): await bot.send_message(chat_id=chat_id, text=afk_text, business_connection_id=conn_id)
                afk_cooldowns[(owner_id, chat_id)] = now

    mute_key = f"{conn_id}_{chat_id}"
    if mute_key in muted_chats:
        with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return
        
    with suppress(Exception):
        await messages_collection.insert_one({
            "business_connection_id": conn_id, "message_id": message.message_id, "chat_id": chat_id, "user_id": message.from_user.id,
            "username": message.from_user.username or "нет_юзернейма", "first_name": message.from_user.first_name or "Без имени",
            "text": message.text or message.caption or "[Без текста]", "created_at": datetime.now(timezone.utc)
        })

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"

    if call.from_user.id == chat_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest): await call.answer("вы не можете снять мут", show_alert=True)
        return
    if mute_key in muted_chats or call.from_user.id == SUPERADMIN_ID:
        if mute_key in muted_chats: muted_chats.remove(mute_key)
        with suppress(TelegramBadRequest): await call.message.edit_text("мут снят"); await call.answer("снял")
    else:
        with suppress(TelegramBadRequest): await call.message.edit_text("уже снял")

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
