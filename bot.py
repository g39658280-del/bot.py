import os
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

# Вставляем токен напрямую (замени на новый после тестов!)
BOT_TOKEN = "8852544876:AAG-ADmDRQmW-ySwsW_JjHRKPF2tc4Wx7m8"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище ID замученных чатов
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
@dp.business_message(F.text == ".мут", F.reply_to_message)
async def mute_user(message: Message):
    chat_id = message.chat.id
    muted_chats.add(chat_id)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]
    ])
    
    await message.answer(
        "🚫 **Собеседник переведен в мут.**\nВсе его новые сообщения будут удаляться.", 
        reply_markup=markup, 
        parse_mode="Markdown"
    )

@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    if chat_id in muted_chats:
        # Удаляем сообщение собеседника
        if message.from_user.id == chat_id:
            try:
                await message.delete()
            except TelegramBadRequest as e:
                print(f"Ошибка удаления: {e}")

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    if chat_id in muted_chats:
        muted_chats.remove(chat_id)
        await call.message.edit_text("✅ **Мут снят.** Собеседник снова может писать.")
    else:
        await call.answer("Этот чат не в муте.", show_alert=True)

async def main():
    await start_web_server()
    print("Бот-секретарь запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
