import os
import asyncio
from datetime import datetime, timezone
from aiohttp import web
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BusinessMessagesDeleted
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient

# Токен берется из настроек Render, либо дефолтный
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
# Твой ID, куда бот будет скидывать удаленки и фотки (берется из Render)
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
# Ссылка на базу MongoDB (берется из Render)
MONGO_URI = os.environ.get("MONGO_URI", "тут_будет_твоя_ссылка_из_mongo")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключение к MongoDB
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_bot']
    messages_collection = db['messages']
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
    # Создаем TTL-индекс для автоудаления сообщений из БД через 48 часов (172800 секунд)
    try:
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
        print("База данных подключена, таймер на 48 часов запущен!")
    except Exception as e:
        print(f"Внимание: ошибка БД (вероятно, не настроен MONGO_URI): {e}")

dp.startup.register(on_startup)

@dp.business_message(F.text.lower() == ".мут")
async def mute_user(message: Message):
    chat_id = message.chat.id
    
    # Защита: команду можешь отправлять только ты (владелец аккаунта)
    if message.from_user.id == chat_id:
        return
        
    # Защита от двойного мута
    if chat_id in muted_chats:
        await message.answer("⚠️ уже в муте петух", parse_mode="Markdown")
        return
        
    # Включаем мут
    muted_chats.add(chat_id)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]
    ])
    
    await message.answer(
        "67 покойошечка", 
        reply_markup=markup, 
        parse_mode="Markdown"
    )

@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    is_interlocutor = (message.from_user.id == chat_id)
    
    # СОХРАНЕНИЕ В БАЗУ И ПЕРЕХВАТ МЕДИА
    if is_interlocutor:
        text_content = message.text or message.caption or "[Файл/Стикер без текста]"
        
        # Пишем в монго для слежки за удаленками
        with suppress(Exception):
            await messages_collection.insert_one({
                "message_id": message.message_id,
                "chat_id": chat_id,
                "text": text_content,
                "created_at": datetime.now(timezone.utc)
            })

        # Перехват фото и видео (шлем тебе в личку к боту)
        if message.photo or message.video:
            with suppress(Exception):
                if message.photo:
                    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📸 Фото от {message.from_user.first_name}\nПодпись: {text_content}")
                elif message.video:
                    await bot.send_video(ADMIN_ID, message.video.file_id, caption=f"🎥 Видео от {message.from_user.first_name}\nПодпись: {text_content}")

    # Логика мута (сносим сообщение, если чат замучен)
    if chat_id in muted_chats and is_interlocutor:
        try:
            await bot.delete_business_messages(
                business_connection_id=message.business_connection_id,
                message_ids=[message.message_id]
            )
        except TelegramBadRequest as e:
            print(f"ОШИБКА УДАЛЕНИЯ: {e}")

# ПЕРЕХВАТ ИЗМЕНЕННЫХ СООБЩЕНИЙ
@dp.edited_business_message()
async def catch_edits(message: Message):
    if message.from_user.id == message.chat.id:
        new_text = message.text or message.caption or "[Без текста]"
        
        # Ищем старый текст в базе
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({"message_id": message.message_id, "chat_id": message.chat.id})
            
        old_text = old_msg['text'] if old_msg else "[Не успел сохранить в БД]"
        
        # Кидаем тебе лог
        with suppress(Exception):
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✏️ **{message.from_user.first_name}** изменил сообщение:\n\n**Было:** {old_text}\n\n**Стало:** {new_text}",
                parse_mode="Markdown"
            )
            
        # Обновляем текст в базе
        with suppress(Exception):
            await messages_collection.update_one(
                {"message_id": message.message_id, "chat_id": message.chat.id},
                {"$set": {"text": new_text}}
            )

# ПЕРЕХВАТ УДАЛЕННЫХ СООБЩЕНИЙ
@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    # deleted.message_ids - это список ID стертых сообщений
    for msg_id in deleted.message_ids:
        old_msg = None
        with suppress(Exception):
            old_msg = await messages_collection.find_one({"message_id": msg_id, "chat_id": deleted.chat.id})
            
        if old_msg:
            with suppress(Exception):
                await bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🗑 **Удалено сообщение** в чате:\n\n{old_msg['text']}",
                    parse_mode="Markdown"
                )

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    
    # Защита: собеседник не может размутить сам себя
    if call.from_user.id == chat_id:
        with suppress(TelegramBadRequest):
            await call.answer("поной!", show_alert=True)
        return

    # Если кнопку нажал ты (владелец)
    if chat_id in muted_chats:
        muted_chats.remove(chat_id)
        with suppress(TelegramBadRequest):
            await call.message.edit_text("твой госоподин размутил тибя")
            await call.answer("снял наху")
    else:
        # Защита от спама по старой кнопке
        with suppress(TelegramBadRequest):
            await call.message.edit_text("уже снял еблан")
            await call.answer("Этот чат уже не в муте.")

async def main():
    await start_web_server()
    print("Бот-секретарь запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
