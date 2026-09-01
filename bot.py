import os
import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ChatType

# Получаем переменные окружения (для Render.com)
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

if not all([API_ID, API_HASH, SESSION_STRING]):
    raise ValueError("Необходимо задать API_ID, API_HASH и SESSION_STRING в переменных окружения.")

app = Client(
    "mute_userbot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

# Хранилище ID замученных пользователей (в памяти)
# При перезапуске на Render список сбросится. Для постоянного хранения используйте БД (например, SQLite/Redis).
muted_users = set()

@app.on_message(filters.me & filters.command("мут", prefixes=".") & filters.reply & filters.private)
async def mute_user(client, message):
    """
    Включает мут. Использование: ответить командой .мут на сообщение собеседника.
    """
    target_id = message.reply_to_message.from_user.id
    muted_users.add(target_id)
    
    # Меняем текст нашей команды на информационное сообщение
    await message.edit_text(
        "🚫 **Собеседник переведен в мут.**\n"
        "Все его новые сообщения будут автоматически удаляться.\n\n"
        "Для отмены напишите `.размутить` в этом чате."
    )

@app.on_message(filters.me & filters.command("размутить", prefixes=".") & filters.private)
async def unmute_user(client, message):
    """
    Выключает мут. Использование: отправить .размутить в чате с пользователем.
    """
    target_id = message.chat.id
    if target_id in muted_users:
        muted_users.remove(target_id)
        await message.edit_text("✅ **Мут снят.** Собеседник снова может писать.")
    else:
        await message.edit_text("Этот собеседник не в муте.")

@app.on_message(filters.private & ~filters.me)
async def delete_muted(client, message):
    """
    Перехватывает и удаляет сообщения от замученных пользователей.
    """
    if message.from_user and message.from_user.id in muted_users:
        try:
            await message.delete()
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")

if __name__ == "__main__":
    print("Юзербот запущен...")
    app.run()
