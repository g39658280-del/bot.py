import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

BOT_TOKEN = "8852544876:AAFHuvVEvsZy7F7N3eKfBRDrd8N8f4fVeko"

app = Client(
    "mut_bot",
    bot_token=BOT_TOKEN,
    api_id=6,
    api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e"
)

muted_chats = set()

@app.on_message(filters.command("мут") & filters.reply)
async def mute_user(client, message):
    chat_id = message.chat.id
    muted_chats.add(chat_id)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔓 Размутить", callback_data="unmute")]
    ])
    
    await message.reply("🔇 Режим мута включён! Все сообщения собеседника удаляются.", reply_markup=keyboard)
    await message.reply_to_message.delete()

@app.on_message(filters.text & ~filters.command("мут"))
async def delete_incoming(client, message):
    chat_id = message.chat.id
    if chat_id in muted_chats:
        try:
            await client.delete_messages(chat_id, message.id)
        except:
            pass

@app.on_callback_query()
async def handle_unmute(client, callback):
    if callback.data == "unmute":
        chat_id = callback.message.chat.id
        if chat_id in muted_chats:
            muted_chats.remove(chat_id)
            await callback.message.edit_text("🔊 Мут выключен! Собеседник снова может писать.")
            await callback.answer("✅ Размут выполнен!")
        else:
            await callback.answer("⚠️ Мут уже выключен.", show_alert=True)

print("🤖 Бот запущен! Пиши .мут в ответ на сообщение.")
app.run()