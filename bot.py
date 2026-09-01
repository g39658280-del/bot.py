import os
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

# Вставь сюда свой НОВЫЙ токен (или получай через os.environ.get)
BOT_TOKEN = "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU"

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
    """Включает мут (Срабатывает только от владельца аккаунта)"""
    chat_id = message.chat.id
    
    # ПРОВЕРКА: Если команду отправил собеседник — бот её игнорирует.
    # В личных бизнес-чатах chat.id — это ID собеседника.
    if message.from_user.id == chat_id:
        return
        
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
    """Перехватывает сообщения и удаляет их, если собеседник в муте"""
    chat_id = message.chat.id
    
    # Проверяем, в муте ли чат И кто написал сообщение
    # Удаляем ТОЛЬКО если сообщение написал собеседник (его ID совпадает с chat_id)
    if chat_id in muted_chats and message.from_user.id == chat_id:
        try:
            await message.delete()
        except TelegramBadRequest as e:
            print(f"Ошибка удаления: {e}")

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    """Обрабатывает нажатие на кнопку 'Размутить'"""
    chat_id = int(call.data.split("_")[1])
    
    # ПРОВЕРКА КНОПКИ: Не даем собеседнику размутить самого себя
    if call.from_user.id == chat_id:
        # show_alert=True покажет всплывающее окно прямо по центру экрана
        await call.answer("🚫 Вы не можете снять мут сами с себя!", show_alert=True)
        return

    # Если кнопку нажал ты (владелец), снимаем мут
    if chat_id in muted_chats:
        muted_chats.remove(chat_id)
        await call.message.edit_text("✅ **Мут снят.** Собеседник снова может писать.")
        await call.answer("Мут успешно снят!")
    else:
        # Если случайно нажал на старую кнопку, когда мут уже снят
        await call.message.edit_text("✅ **Собеседник уже может писать.**")
        await call.answer("Этот чат уже не в муте.")

async def main():
    await start_web_server()
    print("Бот-секретарь запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
