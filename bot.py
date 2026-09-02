import os
import asyncio
import random
from datetime import datetime, timezone
from aiohttp import web
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BusinessMessagesDeleted
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient

# Токен берется из настроек Render, либо дефолтный
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
# Страховочный ID на случай, если владелец не нажал /start
FALLBACK_ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
# Ссылка на базу MongoDB
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключение к MongoDB
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_bot']
    messages_collection = db['messages']
    users_collection = db['users']
except Exception as e:
    print(f"Ошибка подключения к MongoDB: {e}")

muted_chats = set()

# --- Веб-сервер для прохождения проверок Render ---
async def dummy_handler(request):
    return web.Response(text="Bot is running!")

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

# --- Логика Telegram-бота ---

async def on_startup():
    try:
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
        print("База данных подключена, таймер на 48 часов запущен!")
    except Exception as e:
        print(f"Внимание: ошибка БД: {e}")

dp.startup.register(on_startup)

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": message.from_user.id},
            {"$set": {"user_id": message.from_user.id}},
            upsert=True
        )
    await message.answer("Бот-секретарь успешно активирован! Логи удалений и измененных сообщений будут приходить сюда.")

@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    
    # Сносим сообщение с командой .мут
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    # Если сообщение написал клиент, а не ты — игнорируем мут
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

# --- АНИМАЦИЯ .п1: Стандартная посимвольная печать (0.27 сек) ---
@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    if message.from_user.id == message.chat.id:
        return

    full_text = message.text[3:].strip()
    
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    if not full_text:
        return

    print(f"[TREK .п1] Старт. Текст: '{full_text}'")

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(
            chat_id=message.chat.id,
            text=full_text[0],
            business_connection_id=message.business_connection_id
        )

    if not sent_msg:
        return

    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=sent_msg.message_id,
                text=current_str,
                business_connection_id=message.business_connection_id
            )
    print("[TREK .п1] Завершено.")

# --- АНИМАЦИЯ .п2: Печатная машинка с мигающим курсором (▌) ---
@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    if message.from_user.id == message.chat.id:
        return

    full_text = message.text[3:].strip()
    
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    if not full_text:
        return

    print(f"[TREK .п2] Старт (курсор). Текст: '{full_text}'")

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(
            chat_id=message.chat.id,
            text=full_text[0] + "▌",
            business_connection_id=message.business_connection_id
        )

    if not sent_msg:
        return

    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=sent_msg.message_id,
                text=current_str + "▌",
                business_connection_id=message.business_connection_id
            )

    # Убираем курсор в конце
    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=sent_msg.message_id,
            text=current_str,
            business_connection_id=message.business_connection_id
        )
    print("[TREK .п2] Завершено.")

# --- АНИМАЦИЯ .п3: Глитч-дешифратор (рандомные символы превращаются в текст) ---
@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    if message.from_user.id == message.chat.id:
        return

    full_text = message.text[3:].strip()
    
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    if not full_text:
        return

    print(f"[TREK .п3] Старт (глитч). Текст: '{full_text}'")
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(
            chat_id=message.chat.id,
            text="...",
            business_connection_id=message.business_connection_id
        )

    if not sent_msg:
        return

    for i in range(len(full_text) + 1):
        await asyncio.sleep(0.2)
        correct_part = full_text[:i]
        random_part = "".join(random.choice(alphabet) for _ in range(len(full_text) - i))
        display_text = correct_part + random_part
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=sent_msg.message_id,
                text=display_text,
                business_connection_id=message.business_connection_id
            )
    print("[TREK .п3] Завершено.")

# --- АНИМАЦИЯ .дроч ---
@dp.business_message(F.text.lower() == ".дроч")
async def anim_droch(message: Message):
    if message.from_user.id == message.chat.id:
        return

    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    frames = [
        "8==✊===D",
        "8===✊==D",
        "8====✊=D",
        "8===✊==D",
        "8==✊===D",
        "8===✊==D",
        "8====✊=D",
        "8===✊==D",
        "8==✊===D",
        "8===✊==D",
        "8====✊=D",
        "8===✊==D",
        "8==✊===D",
        "8======D💦"
    ]

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(
            chat_id=message.chat.id,
            text=frames[0],
            business_connection_id=message.business_connection_id
        )

    if not sent_msg:
        return

    for frame in frames[1:]:
        await asyncio.sleep(0.25)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=sent_msg.message_id,
                text=frame,
                business_connection_id=message.business_connection_id
            )

    # Ждем 3 секунды после последнего кадра и сносим сообщение
    await asyncio.sleep(3.0)
    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[sent_msg.message_id]
        )

# --- АНИМАЦИЯ ПО СЛОВУ ПРИВЕТ ---
@dp.business_message(F.text.lower() == "привет")
async def anim_privet(message: Message):
    if message.from_user.id == message.chat.id:
        return

    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    frames = [
        "Привет 👋",
        "Привет 🖐️",
        "Привет 👋",
        "Привет 🖐️",
        "Привет 👋✨",
        "Привет"
    ]

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(
            chat_id=message.chat.id,
            text=frames[0],
            business_connection_id=message.business_connection_id
        )

    if not sent_msg:
        return

    for frame in frames[1:]:
        await asyncio.sleep(0.4)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=sent_msg.message_id,
                text=frame,
                business_connection_id=message.business_connection_id
            )

# --- АНИМАЦИЯ ПО СЛОВУ КУ ---
@dp.business_message(F.text.lower() == "ку")
async def anim_ku(message: Message):
    if message.from_user.id == message.chat.id:
        return

    with suppress(Exception):
        await bot.delete_business_messages(
            business_connection_id=message.business_connection_id,
            message_ids=[message.message_id]
        )

    frames = [
        "Ку 👋",
        "Ку 🖐️",
        "Ку 👋",
        "Ку 🖐️",
        "Ку 👋✨",
        "Ку"
    ]

    sent_msg = None
    with suppress(Exception):
        sent_msg = await bot.send_message(
            chat_id=message.chat.id,
            text=frames[0],
            business_connection_id=message.business_connection_id
        )

    if not sent_msg:
        return

    for frame in frames[1:]:
        await asyncio.sleep(0.4)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=sent_msg.message_id,
                text=frame,
                business_connection_id=message.business_connection_id
            )

@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    is_interlocutor = (message.from_user.id == chat_id)
    
    if is_interlocutor:
        text_content = message.text or message.caption or "[Файл/Стикер без текста]"
        user = message.from_user
        
        with suppress(Exception):
            await messages_collection.insert_one({
                "message_id": message.message_id,
                "chat_id": chat_id,
                "user_id": user.id,
                "username": user.username or "нет_юзернейма",
                "first_name": user.first_name or "Без имени",
                "text": text_content,
                "created_at": datetime.now(timezone.utc)
            })

        if message.photo or message.video:
            target_admin = FALLBACK_ADMIN_ID
            with suppress(Exception):
                first_user = await users_collection.find_one({})
                if first_user:
                    target_admin = first_user['user_id']

            if target_admin:
                username_str = f"@{user.username}" if user.username else f"ID: {user.id}"
                caption_text = f"📸 Медиа от {user.first_name} ({username_str})\nПодпись: {text_content}"
                with suppress(Exception):
                    if message.photo:
                        await bot.send_photo(target_admin, message.photo[-1].file_id, caption=caption_text)
                    elif message.video:
                        await bot.send_video(target_admin, message.video.file_id, caption=caption_text.replace("📸 Медиа", "🎥 Видео"))

    if chat_id in muted_chats and is_interlocutor:
        try:
            await bot.delete_business_messages(
                business_connection_id=message.business_connection_id,
                message_ids=[message.message_id]
            )
        except TelegramBadRequest as e:
            print(f"ОШИБКА УДАЛЕНИЯ: {e}")

@dp.edited_business_message()
async def catch_edits(message: Message):
    if message.from_user.id == message.chat.id:
        new_text = message.text or message.caption or "[Без текста]"
        user = message.from_user
        
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({"message_id": message.message_id, "chat_id": message.chat.id})
            
        old_text = old_msg['text'] if old_msg else "[Не успел сохранить в БД]"
        username_str = f"@{user.username}" if user.username else f"ID: {user.id}"
        
        target_admin = FALLBACK_ADMIN_ID
        with suppress(Exception):
            first_user = await users_collection.find_one({})
            if first_user:
                target_admin = first_user['user_id']

        if target_admin:
            with suppress(Exception):
                await bot.send_message(
                    chat_id=target_admin,
                    text=(
                        f"✏️ **Изменено сообщение!**\n"
                        f"👤 Пользователь: {user.first_name} ({username_str})\n"
                        f"🆔 ID: `{user.id}`\n\n"
                        f"**Было:** {old_text}\n"
                        f"**Стало:** {new_text}"
                    ),
                    parse_mode="Markdown"
                )
            
        with suppress(Exception):
            await messages_collection.update_one(
                {"message_id": message.message_id, "chat_id": message.chat.id},
                {"$set": {"text": new_text}}
            )

@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    for msg_id in deleted.message_ids:
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({"message_id": msg_id, "chat_id": deleted.chat.id})
            
        if old_msg:
            target_admin = FALLBACK_ADMIN_ID
            with suppress(Exception):
                first_user = await users_collection.find_one({})
                if first_user:
                    target_admin = first_user['user_id']

            if target_admin:
                uname = f"@{old_msg['username']}" if old_msg.get('username') and old_msg['username'] != "нет_юзернейма" else f"ID: {old_msg['user_id']}"
                with suppress(Exception):
                    await bot.send_message(
                        chat_id=target_admin,
                        text=(
                            f"🗑 **Удалено сообщение!**\n"
                            f"👤 Пользователь: {old_msg.get('first_name', 'Неизвестно')} ({uname})\n"
                            f"🆔 ID: `{old_msg.get('user_id', 'Неизвестно')}`\n\n"
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
            await call.message.edit_text("твой госоподин размутил тибя")
            await call.answer("снял наху")
    else:
        with suppress(TelegramBadRequest):
            await call.message.edit_text("уже снял еблан")
            await call.answer("Этот чат уже не в муте.")

async def main():
    await start_web_server()
    print("Бот-секретарь запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
