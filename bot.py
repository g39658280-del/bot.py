import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

# Получаем токен из переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в переменных окружения")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище замученных чатов: {chat_id: True}
# При перезапуске сервера (например, на бесплатном тарифе Render) этот список сбросится.
muted_chats = {}

@dp.business_message(F.text == ".мут", F.reply_to_message)
async def mute_user(message: Message):
    """Включает мут при ответе на сообщение собеседника командой .мут"""
    chat_id = message.chat.id
    
    # Добавляем чат в список мута
    muted_chats[chat_id] = True
    
    # Создаем inline-кнопку для размута
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
    """Перехватывает все сообщения в бизнес-чатах"""
    chat_id = message.chat.id
    
    # Проверяем, находится ли этот диалог в муте
    if chat_id in muted_chats:
        # Удаляем сообщение, если его отправил собеседник, а не владелец аккаунта
        # В личных чатах ID собеседника совпадает с ID чата
        if message.from_user.id == chat_id:
            try:
                # В Aiogram 3.7+ метод message.delete() под капотом автоматически 
                # использует нужный контекст Business API (deleteBusinessMessages)
                await message.delete()
            except TelegramBadRequest as e:
                print(f"Ошибка удаления (проверьте, выданы ли права на удаление): {e}")

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    """Обрабатывает нажатие на кнопку 'Размутить'"""
    chat_id = int(call.data.split("_")[1])
    
    if chat_id in muted_chats:
        del muted_chats[chat_id]
        await call.message.edit_text("✅ **Мут снят.** Собеседник снова может писать.")
    else:
        await call.answer("Этот чат не в муте.", show_alert=True)

async def main():
    print("Бот-секретарь (Business Mode) запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
