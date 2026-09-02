import os
import asyncio
import random
from datetime import datetime, timezone
from aiohttp import web
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BusinessMessagesDeleted, BusinessConnection
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections'] # Хранит связку business_connection_id -> user_id владельца
except Exception as e:
    print(f"Ошибка подключения к MongoDB: {e}")

muted_chats = set()

async def dummy_handler(request):
    return web.Response(text="Multi-bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    app.router.add_get("/healthz", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Health-check сервер запущен на порту {port}")

async def on_startup():
    try:
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
        print("Мультиаккаунтная база данных подключена!")
    except Exception as e:
        print(f"Внимание: ошибка БД: {e}")

dp.startup.register(on_startup)

# --- УЛОВИМ ПОДКЛЮЧЕНИЕ БИЗНЕСА ---
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    # Сохраняем связку: ID бизнес-подключения -> ID пользователя (владельца аккаунта)
    if connection.is_enabled:
        with suppress(Exception):
            await connections_collection.update_one(
                {"business_connection_id": connection.id},
                {"$set": {
                    "business_connection_id": connection.id,
                    "user_id": connection.user.id,
                    "first_name": connection.user.first_name
                }},
                upsert=True
            )
        # Шлем владельцу приветствие в ЛС при подключении
        with suppress(Exception):
            await bot.send_message(
                connection.user.id,
                "🤖 Бот успешно привязан к твоему Telegram Business аккаунту! Теперь все логи будут приходить сюда."
            )
    else:
        # Если отключил бизнес
        with suppress(Exception):
            await connections_collection.delete_one({"business_connection_id": connection.id})

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Это общий бот-ассистент для Telegram Business.\n"
        "Чтобы бот начал работать, подключи его в настройках Telegram: **Настройки ➔ Telegram Business ➔ Чат-боты** и выбери этого бота."
    )

@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    if message.from_user.id == chat_id:
        return
        
    if chat_id in muted_chats:
        await message.answer("⚠️ уже в муте петух", parse_mode="Markdown")
        return
        
    muted_chats.add(chat_id)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]
    ])
    
    await message.answer(
        "67 покойошечка", 
        reply_markup=markup, 
        parse_mode="Markdown"
    )

# --- АНИМАЦИИ (.п1, .п2, .п3, .дроч, привет, ку) ---
@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    if message.from_user.id != message.chat.id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0], business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    if message.from_user.id != message.chat.id:
        return
    full_text = message.text[3:].strip()
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0] + "▌", business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str + "▌", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    if message.from_user.id != message.chat.id:
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
        correct_part = full_text[:i]
        random_part = "".join(random.choice(alphabet) for _ in range(len(full_text) - i))
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=correct_part + random_part, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".дроч"))
async def anim_droch(message: Message):
    if message.from_user.id != message.chat.id:
        return
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    frames = ["8==✊==D", "8====✊=D", "8==✊==D", "8====✊=D", "8==✊==D", "8====✊=D", "8=====D💦"]
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    for frame in frames[1:]:
        await asyncio.sleep(0.25)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=message.business_connection_id)
    await asyncio.sleep(3.0)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[sent_msg.message_id])

@dp.business_message(F.text.lower() == "привет")
async def anim_privet(message: Message):
    if message.from_user.id != message.chat.id:
        return
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    frames = ["Привет 👋", "Привет 🖐️", "Привет 👋", "Привет 🖐️", "Привет 👋✨", "Привет"]
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    for frame in frames[1:]:
        await asyncio.sleep(0.4)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower() == "ку")
async def anim_ku(message: Message):
    if message.from_user.id != message.chat.id:
        return
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    frames = ["Ку 👋", "Ку 🖐️", "Ку 👋", "Ку 🖐️", "Ку 👋✨", "Ку"]
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=message.business_connection_id)
    if not sent_msg:
        return
    for frame in frames[1:]:
        await asyncio.sleep(0.4)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=message.business_connection_id)

# --- СОХРАНЕНИЕ СООБЩЕНИЙ СОБЕСЕДНИКОВ С ПРИВЯЗКОЙ К АККАУНТУ ---
@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    is_interlocutor = (message.from_user.id != chat_id)
    
    if is_interlocutor:
        text_content = message.text or message.caption or "[Без текста]"
        user = message.from_user
        with suppress(Exception):
            await messages_collection.insert_one({
                "business_connection_id": message.business_connection_id,
                "message_id": message.message_id,
                "chat_id": chat_id,
                "user_id": user.id,
                "username": user.username or "нет_юзернейма",
                "first_name": user.first_name or "Без имени",
                "text": text_content,
                "created_at": datetime.now(timezone.utc)
            })

    if chat_id in muted_chats and is_interlocutor:
        with suppress(Exception):
            await bot.delete_business_messages(
                business_connection_id=message.business_connection_id,
                message_ids=[message.message_id]
            )

# --- ИЗМЕНЕНИЯ СООБЩЕНИЙ (ШЛЕМ ВЛАДЕЛЬЦУ ЭТОГО БИЗНЕСА) ---
@dp.edited_business_message()
async def catch_edits(message: Message):
    chat_id = message.chat.id
    if message.from_user.id != chat_id:
        new_text = message.text or message.caption or "[Без текста]"
        user = message.from_user
        
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({
                "business_connection_id": message.business_connection_id,
                "message_id": message.message_id,
                "chat_id": chat_id
            })
            
        old_text = old_msg['text'] if old_msg else "[Не успел сохранить]"
        
        # Находим владельца конкретно этого business_connection_id в базе
        owner_data = await connections_collection.find_one({"business_connection_id": message.business_connection_id})
        if owner_data:
            owner_id = owner_data["user_id"]
            username_str = f"@{user.username}" if user.username else f"ID: {user.id}"
            with suppress(Exception):
                await bot.send_message(
                    chat_id=owner_id,
                    text=(
                        f"✏️ **Собеседник изменил сообщение!**\n"
                        f"👤 Пользователь: {user.first_name} ({username_str})\n\n"
                        f"**Было:** {old_text}\n"
                        f"**Стало:** {new_text}"
                    ),
                    parse_mode="Markdown"
                )
            
        with suppress(Exception):
            await messages_collection.update_one(
                {"business_connection_id": message.business_connection_id, "message_id": message.message_id, "chat_id": chat_id},
                {"$set": {"text": new_text}}
            )

# --- УДАЛЕНИЯ СООБЩЕНИЙ (ШЛЕМ ВЛАДЕЛЬЦУ ЭТОГО БИЗНЕСА) ---
@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    connection_id = deleted.business_connection_id
    chat_id = deleted.chat.id
    
    owner_data = await connections_collection.find_one({"business_connection_id": connection_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]

    for msg_id in deleted.message_ids:
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({
                "business_connection_id": connection_id,
                "message_id": msg_id,
                "chat_id": chat_id
            })
            
        if old_msg:
            uname = f"@{old_msg['username']}" if old_msg.get('username') and old_msg['username'] != "нет_юзернейма" else f"ID: {old_msg['user_id']}"
            with suppress(Exception):
                await bot.send_message(
                    chat_id=owner_id,
                    text=(
                        f"🗑 **Собеседник удалил сообщение!**\n"
                        f"👤 Пользователь: {old_msg.get('first_name', 'Неизвестно')} ({uname})\n\n"
                        f"💬 Текст: {old_msg['text']}"
                    ),
                    parse_mode="Markdown"
                )

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    if call.from_user.id == chat_id:
        with suppress(TelegramBadRequest):
            await call.answer("поной!", show_alert=True)
        return

    if chat_id in muted_chats:
        muted_chats.remove(chat_id)
        with suppress(TelegramBadRequest):
            await call.message.edit_text("твой господин размутил тебя")
            await call.answer("снял")
    else:
        with suppress(TelegramBadRequest):
            await call.message.edit_text("уже снял")

async def main():
    await start_web_server()
    print("Мультиаккаунтный бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
