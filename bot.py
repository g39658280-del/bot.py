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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")
SUPERADMIN_ID = 6548121776  # Твой ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# База данных
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections']
    users_collection = db['users']
except Exception as e:
    print(f"Ошибка БД: {e}")

muted_chats = set()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()

async def ensure_connection(conn_id: str, user_id: int, first_name: str):
    try:
        await connections_collection.update_one(
            {"business_connection_id": conn_id},
            {"$set": {"business_connection_id": conn_id, "user_id": user_id, "first_name": first_name or "Без имени"}},
            upsert=True
        )
    except Exception as e:
        print(f"Ошибка авто-привязки: {e}")

# --- ВЕБ-СЕРВЕР ---
async def dummy_handler(request):
    return web.Response(text="Multi-bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def on_startup():
    try:
        await messages_collection.create_index("created_at", expireAfterSeconds=172800)
    except Exception: pass

dp.startup.register(on_startup)

# --- АДМИН ПАНЕЛЬ ---
@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != SUPERADMIN_ID: return
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи и Логи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])
    await message.answer("👑 **Панель управления ботом:**", reply_markup=markup, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID: return
    action = call.data.split("_")[1]

    if action == "stats":
        users_count = await connections_collection.count_documents({})
        msgs_count = await messages_collection.count_documents({})
        await call.message.edit_text(f"📊 **Статистика:**\nПодключено бизнесов: {users_count}\nСообщений в базе: {msgs_count}", parse_mode="Markdown")

    elif action == "mutes":
        if not muted_chats:
            await call.message.edit_text("Активных мутов сейчас нет.")
            return
        
        builder = InlineKeyboardBuilder()
        for mute in list(muted_chats):
            conn, chat = mute.split("_")
            builder.button(text=f"Снять мут: {chat}", callback_data=f"forceunmute_{mute}")
        builder.adjust(1)
        await call.message.edit_text("🔇 **Список активных мутов:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

    elif action == "users":
        users = await connections_collection.find({}).to_list(length=100)
        text = "👥 **Подключенные кенты:**\n"
        for u in users:
            text += f"- {u.get('first_name')} (ID: `{u.get('user_id')}`)\n"
        text += "\n*(В следующих версиях сюда прикрутим детальные кнопки по каждому)*"
        await call.message.edit_text(text, parse_mode="Markdown")

    elif action == "broadcast":
        await call.message.edit_text("Напиши сообщение для рассылки всем кентам (или отправь 'отмена'):")
        await state.set_state(AdminStates.waiting_for_broadcast)

@dp.callback_query(F.data.startswith("forceunmute_"))
async def force_unmute(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    mute_key = call.data.replace("forceunmute_", "")
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.answer("Мут принудительно снят!", show_alert=True)
        await call.message.edit_text(f"✅ Мут {mute_key} снят админом.")
    else:
        await call.answer("Этот мут уже не активен.")

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await message.answer("Рассылка отменена.")
        await state.clear()
        return
        
    users = await connections_collection.find({}).to_list(length=100)
    count = 0
    for u in users:
        with suppress(Exception):
            await bot.send_message(u['user_id'], f"📢 **Сообщение от создателя:**\n\n{message.text}", parse_mode="Markdown")
            count += 1
            
    await message.answer(f"✅ Успешно отправлено {count} пользователям.")
    await state.clear()

# --- ОСНОВНАЯ ЛОГИКА ---
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        await ensure_connection(connection.id, connection.user.id, connection.user.first_name)
        with suppress(Exception): await bot.send_message(connection.user.id, "🤖 Бот привязан! Логи будут тут.")
    else:
        with suppress(Exception): await connections_collection.delete_one({"business_connection_id": connection.id})

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    with suppress(Exception): await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"user_id": message.from_user.id}}, upsert=True)
    await message.answer("Бот активен! Жду привязки в Business.")

@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        mute_key = f"{conn_id}_{chat_id}"
        if mute_key in muted_chats: return
        muted_chats.add(mute_key)
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute_{chat_id}")]])
        await bot.send_message(chat_id=message.chat.id, text="67 покойошечка", reply_markup=markup, business_connection_id=conn_id)

@dp.business_message(F.text.lower().startswith(".дроч"))
async def anim_droch(message: Message):
    if message.from_user.id == message.chat.id: return
    conn_id = message.business_connection_id
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    frames = ["8==✊==D", "8==✊===D", "8===✊==D", "8====✊=D", "8==✊==D", "8===✊==D", "8==✊===D", "8===✊==D", "8====✊=D", "8==✊==D", "8===✊==D", "8==✊===D", "8===✊==D", "8====✊=D", "8=====D💦", "8===✊==D", "8==✊===D", "8======D💦"]
    sent_msg = None
    with suppress(Exception): sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=conn_id)
    if not sent_msg: return
    for frame in frames[1:]:
        await asyncio.sleep(0.2)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=conn_id)
    await asyncio.sleep(3.0)
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[sent_msg.message_id])

@dp.business_message()
async def handle_messages(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        return
    mute_key = f"{conn_id}_{chat_id}"
    if mute_key in muted_chats:
        with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return
    with suppress(Exception):
        await messages_collection.insert_one({
            "business_connection_id": conn_id, "message_id": message.message_id,
            "chat_id": chat_id, "user_id": message.from_user.id,
            "username": message.from_user.username or "нет_юзернейма",
            "first_name": message.from_user.first_name or "Без имени",
            "text": message.text or message.caption or "[Без текста]",
            "created_at": datetime.now(timezone.utc)
        })

@dp.edited_business_message()
async def catch_edits(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id: return
    new_text = message.text or message.caption or "[Без текста]"
    old_msg = None
    with suppress(Exception): old_msg = await messages_collection.find_one({"business_connection_id": conn_id, "message_id": message.message_id, "chat_id": chat_id})
    old_text = old_msg['text'] if old_msg else "[Не успел сохранить]"
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    safe_name, safe_old, safe_new = html.escape(message.from_user.first_name), html.escape(old_text), html.escape(new_text)
    with suppress(Exception):
        await bot.send_message(chat_id=owner_data["user_id"], text=f"✏️ <b>Собеседник изменил сообщение!</b>\n👤 От: {safe_name}\n\n<b>Было:</b> {safe_old}\n<b>Стало:</b> {safe_new}", parse_mode="HTML")
    with suppress(Exception): await messages_collection.update_one({"business_connection_id": conn_id, "message_id": message.message_id, "chat_id": chat_id}, {"$set": {"text": new_text}})

@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    for msg_id in deleted.message_ids:
        old_msg = None
        with suppress(Exception): old_msg = await messages_collection.find_one({"business_connection_id": conn_id, "message_id": msg_id, "chat_id": deleted.chat.id})
        if old_msg:
            safe_name, safe_text = html.escape(old_msg.get('first_name', 'Неизвестно')), html.escape(old_msg['text'])
            with suppress(Exception):
                await bot.send_message(chat_id=owner_data["user_id"], text=f"🗑 <b>Собеседник удалил сообщение!</b>\n👤 От: {safe_name}\n\n💬 Текст: {safe_text}", parse_mode="HTML")

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"

    # Если нажал сам собеседник, а не ты
    if call.from_user.id == chat_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest): await call.answer("поной!", show_alert=True)
        return

    # Если нажал кент (владелец чата) ИЛИ ты (Глобальный админ)
    if mute_key in muted_chats or call.from_user.id == SUPERADMIN_ID:
        if mute_key in muted_chats: muted_chats.remove(mute_key)
        with suppress(TelegramBadRequest):
            await call.message.edit_text("твой господин размутил тебя")
            await call.answer("снял")
    else:
        with suppress(TelegramBadRequest): await call.message.edit_text("уже снял")

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
