import os
import asyncio
from aiohttp import web
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

# Токен берется из настроек Render, либо используется дефолтный
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")

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
    
    # Удаляем сообщение, ЕСЛИ чат в муте И сообщение написал собеседник
    if chat_id in muted_chats and message.from_user.id == chat_id:
        try:
            # ИСПОЛЬЗУЕМ СПЕЦИАЛЬНЫЙ БИЗНЕС-МЕТОД УДАЛЕНИЯ
            # ВАЖНО: Мы больше не передаем сюда chat_id, чтобы не крашить код!
            await bot.delete_business_messages(
                business_connection_id=message.business_connection_id,
                message_ids=[message.message_id]
            )
        except TelegramBadRequest as e:
            print(f"ОШИБКА УДАЛЕНИЯ: {e}")

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
