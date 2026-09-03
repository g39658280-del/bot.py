import os
import asyncio
import random
import html
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

# --- База данных для мультиаккаунтности ---
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections']
    users_collection = db['users']
except Exception as e:
    print(f"Ошибка подключения к MongoDB: {e}")

muted_chats = set()

# --- Вспомогательная функция авто-восстановления коннекта ---
async def ensure_connection(conn_id: str, user_id: int, first_name: str):
    """Надежно связывает ID бизнес-подключения с ID владельца при любой его активности"""
    try:
        await connections_collection.update_one(
            {"business_connection_id": conn_id},
            {"$set": {
                "business_connection_id": conn_id,
                "user_id": user_id,
                "first_name": first_name or "Без имени"
            }},
            upsert=True
        )
    except Exception as e:
        print(f"Ошибка авто-привязки коннекта: {e}")

# --- Веб-сервер ---
async def dummy_handler(request):
    return web.Response(text="Multi-bot is running robustly!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    app.router.add_get("/healthz", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def on_startup():
    try:
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
        print("Бот и база данных успешно запущены!")
    except Exception as e:
        print(f"Ошибка индексов БД: {e}")

dp.startup.register(on_startup)

# --- ЛОВИМ ПОДКЛЮЧЕНИЯ ИЗ НАСТРОЕК ТЕЛЕГРАМА ---
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        await ensure_connection(connection.id, connection.user.id, connection.user.first_name)
        with suppress(Exception):
            await bot.send_message(
                connection.user.id,
                "🤖 Бот успешно привязан к твоему Telegram Business аккаунту!\nЛоги из твоих чатов будут приходить сюда."
            )
    else:
        with suppress(Exception):
            await connections_collection.delete_one({"business_connection_id": connection.id})

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": message.from_user.id},
            {"$set": {"user_id": message.from_user.id}},
            upsert=True
        )
    await message.answer("Бот активен! Убедись, что он добавлен в настройках Telegram Business.")

# --- ЛОГИКА МУТА ---
@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    
    # Если пишет владелец бизнеса:
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

        mute_key = f"{conn_id}_{chat_id}"
        if mute_key in muted_chats:
            await bot.send_message(chat_id=message.from_user.id, text="⚠️ Этот чат уже в муте.")
            return
            
        muted_chats.add(mute_key)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]
        ])
        await bot.send_message(chat_id=message.chat.id, text="67 покойошечка", reply_markup=markup, business_connection_id=conn_id)

# --- УДЛИНЕННАЯ АНИМАЦИЯ .дроч ---
@dp.business_message(F.text.lower().startswith(".дроч"))
async def anim_droch(message: Message):
    if message.from_user.id == message.chat.id: return
    conn_id = message.business_connection_id
    await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
    
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    
    frames = [
        "8==✊==D", "8==✊===D", "8===✊==D", "8====✊=D", 
        "8==✊==D", "8===✊==D", "8==✊===D", "8===✊==D", 
        "8====✊=D", "8==✊==D", "8===✊==D", "8==✊===D", 
        "8===✊==D", "8====✊=D", "8=====D💦", "8===✊==D", 
        "8==✊===D", "8======D💦"
    ]

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=conn_id)
        
    if not sent_msg: return
        
    for frame in frames[1:]:
        await asyncio.sleep(0.2)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=conn_id)
            
    await asyncio.sleep(3.0)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[sent_msg.message_id])

# --- ОСТАЛЬНЫЕ АНИМАЦИИ (.п1, .п2, .п3, привет, ку) ---
@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    if message.from_user.id == message.chat.id: return
    await ensure_connection(message.business_connection_id, message.from_user.id, message.from_user.first_name)
    full_text = message.text[3:].strip()
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0], business_connection_id=message.business_connection_id)
    if not sent_msg: return
    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    if message.from_user.id == message.chat.id: return
    await ensure_connection(message.business_connection_id, message.from_user.id, message.from_user.first_name)
    full_text = message.text[3:].strip()
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0] + "▌", business_connection_id=message.business_connection_id)
    if not sent_msg: return
    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str + "▌", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.3)
    with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    if message.from_user.id == message.chat.id: return
    await ensure_connection(message.business_connection_id, message.from_user.id, message.from_user.first_name)
    full_text = message.text[3:].strip()
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"
    sent_msg = await bot.send_message(chat_id=message.chat.id, text="...", business_connection_id=message.business_connection_id)
    if not sent_msg: return
    for i in range(len(full_text) + 1):
        await asyncio.sleep(0.2)
        correct_part = full_text[:i]
        random_part = "".join(random.choice(alphabet) for _ in range(len(full_text) - i))
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=correct_part + random_part, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower() == "привет")
async def anim_privet(message: Message):
    if message.from_user.id == message.chat.id: return
    await ensure_connection(message.business_connection_id, message.from_user.id, message.from_user.first_name)
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    frames = ["Привет 👋", "Привет 🖐️", "Привет 👋", "Привет 🖐️", "Привет 👋✨", "Привет"]
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=message.business_connection_id)
    if not sent_msg: return
    for frame in frames[1:]:
        await asyncio.sleep(0.4)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower() == "ку")
async def anim_ku(message: Message):
    if message.from_user.id == message.chat.id: return
    await ensure_connection(message.business_connection_id, message.from_user.id, message.from_user.first_name)
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    frames = ["Ку 👋", "Ку 🖐️", "Ку 👋", "Ку 🖐️", "Ку 👋✨", "Ку"]
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=message.business_connection_id)
    if not sent_msg: return
    for frame in frames[1:]:
        await asyncio.sleep(0.4)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=message.business_connection_id)

# --- СОХРАНЕНИЕ И МУТ СООБЩЕНИЙ ПЕТУХОВ ---
@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    
    # Если пишет владелец бизнеса (ты или кент) -> чиним привязку и игнорируем логгирование
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        return
        
    # Сюда дойдет только сообщение Собеседника (Петуха)
    mute_key = f"{conn_id}_{chat_id}"
    if mute_key in muted_chats:
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return # Выходим, удаленное в муте сообщение не логируем

    # Если собеседник не в муте, сохраняем его сообщение в БД
    text_content = message.text or message.caption or "[Без текста]"
    user = message.from_user
    with suppress(Exception):
        await messages_collection.insert_one({
            "business_connection_id": conn_id,
            "message_id": message.message_id,
            "chat_id": chat_id,
            "user_id": user.id,
            "username": user.username or "нет_юзернейма",
            "first_name": user.first_name or "Без имени",
            "text": text_content,
            "created_at": datetime.now(timezone.utc)
        })

# --- ИЗМЕНЕНИЯ СООБЩЕНИЙ СОБЕСЕДНИКА ---
@dp.edited_business_message()
async def catch_edits(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    
    # Игнорируем правки владельца (тебя или кента)
    if message.from_user.id != chat_id:
        return

    new_text = message.text or message.caption or "[Без текста]"
    user = message.from_user
    
    old_msg = None
    with suppress(Exception):
        old_msg = await messages_collection.find_one({
            "business_connection_id": conn_id,
            "message_id": message.message_id,
            "chat_id": chat_id
        })
        
    old_text = old_msg['text'] if old_msg else "[Не успел сохранить]"
    
    # Ищем, кому отправлять лог
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        print(f"[!] Изменение пропущено: нет привязки для коннекта {conn_id}")
        return
        
    owner_id = owner_data["user_id"]
    username_str = f"@{user.username}" if user.username else f"ID: {user.id}"
    
    # ЭКРАНИРОВАНИЕ HTML: спасает от падения из-за левых символов
    safe_name = html.escape(user.first_name)
    safe_old = html.escape(old_text)
    safe_new = html.escape(new_text)
    
    try:
        await bot.send_message(
            chat_id=owner_id,
            text=(
                f"✏️ <b>Собеседник изменил сообщение!</b>\n"
                f"👤 От: {safe_name} ({username_str})\n\n"
                f"<b>Было:</b> {safe_old}\n"
                f"<b>Стало:</b> {safe_new}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Ошибка отправки изменения владельцу {owner_id}: {e}")
        
    with suppress(Exception):
        await messages_collection.update_one(
            {"business_connection_id": conn_id, "message_id": message.message_id, "chat_id": chat_id},
            {"$set": {"text": new_text}}
        )

# --- УДАЛЕНИЯ СООБЩЕНИЙ СОБЕСЕДНИКА ---
@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    chat_id = deleted.chat.id
    
    # Ищем владельца бизнеса
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        print(f"[!] Удаление пропущено: нет привязки для коннекта {conn_id}")
        return
        
    owner_id = owner_data["user_id"]

    for msg_id in deleted.message_ids:
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({
                "business_connection_id": conn_id,
                "message_id": msg_id,
                "chat_id": chat_id
            })
            
        if old_msg:
            uname = f"@{old_msg['username']}" if old_msg.get('username') and old_msg['username'] != "нет_юзернейма" else f"ID: {old_msg['user_id']}"
            safe_name = html.escape(old_msg.get('first_name', 'Неизвестно'))
            safe_text = html.escape(old_msg['text'])
            
            try:
                await bot.send_message(
                    chat_id=owner_id,
                    text=(
                        f"🗑 <b>Собеседник удалил сообщение!</b>\n"
                        f"👤 От: {safe_name} ({uname})\n\n"
                        f"💬 Текст: {safe_text}"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"Ошибка отправки удаления владельцу {owner_id}: {e}")

# --- КНОПКА РАЗМУТИТЬ ---
@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    
    if call.from_user.id == chat_id:
        with suppress(TelegramBadRequest):
            await call.answer("поной!", show_alert=True)
        return

    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"

    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        with suppress(TelegramBadRequest):
            await call.message.edit_text("твой господин размутил тебя")
            await call.answer("снял")
    else:
        with suppress(TelegramBadRequest):
            await call.message.edit_text("уже снял")

async def main():
    await start_web_server()
    print("Авто-восстанавливающийся мультибот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
