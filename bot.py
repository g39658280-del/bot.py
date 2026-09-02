import os
import asyncio
from aiohttp import web
from pyrogram import Client, filters, idle

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# Хранилище мутов (очищается при перезагрузке Render)
muted_chats = set()

app = Client("mute_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# --- Веб-сервер для прохождения проверок Render ---
async def dummy_handler(request):
    return web.Response(text="Userbot is running!")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", dummy_handler)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- Логика Юзербота ---
@app.on_message(filters.me & filters.command("мут", prefixes=".") & filters.private)
async def mute_user(client, message):
    chat_id = message.chat.id
    if chat_id in muted_chats:
        await message.edit_text("⚠️ **Собеседник уже находится в муте!**")
        return
        
    muted_chats.add(chat_id)
    await message.edit_text("🚫 **Мут включен.**\nВсе сообщения собеседника будут удаляться у обоих.\n\nДля отмены напиши в этот чат: `.размутить`")

@app.on_message(filters.me & filters.command("размутить", prefixes=".") & filters.private)
async def unmute_user(client, message):
    chat_id = message.chat.id
    if chat_id in muted_chats:
        muted_chats.remove(chat_id)
        await message.edit_text("✅ **Мут снят.** Собеседник снова может писать.")
    else:
        await message.edit_text("⚠️ **Этот собеседник не в муте.**")

@app.on_message(filters.private & ~filters.me)
async def delete_messages(client, message):
    if message.chat and message.chat.id in muted_chats:
        try:
            # Юзербот удаляет сообщения принудительно для обоих (revoke=True)
            await message.delete()
        except Exception as e:
            print(f"Ошибка удаления: {e}")

async def main():
    await start_web_server()
    await app.start()
    print("Юзербот запущен...")
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
