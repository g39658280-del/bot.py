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

# --- БАЗА ДАННЫХ ---
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections']
    users_collection = db['users']
    history_collection = db['history'] # Новая коллекция для логов
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

# --- ГЕНЕРАТОР ГЛАВНОГО МЕНЮ АДМИНКИ ---
def get_admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи и Логи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])

# --- АДМИН ПАНЕЛЬ ---
@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != SUPERADMIN_ID: return
    await message.answer("👑 **Панель управления ботом:**", reply_markup=get_admin_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_main")
async def back_to_main(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID: return
    await state.clear()
    await call.message.edit_text("👑 **Панель управления ботом:**", reply_markup=get_admin_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID: return
    action = call.data.replace("admin_", "")

    if action == "stats":
        users_count = await connections_collection.count_documents({})
        msgs_count = await messages_collection.count_documents({})
        logs_count = await history_collection.count_documents({})
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="admin_main")
        
        await call.message.edit_text(
            f"📊 **Статистика:**\nПодключено бизнесов: {users_count}\nСообщений в кэше: {msgs_count}\nСобытий в истории: {logs_count}", 
            reply_markup=builder.as_markup(), parse_mode="Markdown"
        )

    elif action == "mutes":
        builder = InlineKeyboardBuilder()
        if not muted_chats:
            builder.button(text="🔙 Назад", callback_data="admin_main")
            await call.message.edit_text("Активных мутов сейчас нет.", reply_markup=builder.as_markup())
            return
        
        for mute in list(muted_chats):
            conn, chat = mute.split("_")
            builder.button(text=f"Снять мут: {chat}", callback_data=f"forceunmute_{mute}")
        builder.button(text="🔙 Назад", callback_data="admin_main")
        builder.adjust(1)
        await call.message.edit_text("🔇 **Список активных мутов:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

    elif action == "users":
        users = await connections_collection.find({}).to_list(length=100)
        builder = InlineKeyboardBuilder()
        
        if not users:
            builder.button(text="🔙 Назад", callback_data="admin_main")
            await call.message.edit_text("Пока никто не подключил бота.", reply_markup=builder.as_markup())
            return
            
        for u in users:
            name = u.get('first_name', 'Без имени')
            uid = u.get('user_id')
            builder.button(text=f"👤 {name} ({uid})", callback_data=f"userlog_{uid}")
            
        builder.button(text="🔙 Назад", callback_data="admin_main")
        builder.adjust(1)
        await call.message.edit_text("👥 **Выбери пользователя для просмотра логов:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

    elif action == "broadcast":
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Отмена", callback_data="admin_main")
        await call.message.edit_text("Напиши сообщение для рассылки всем кентам:", reply_markup=builder.as_markup())
        await state.set_state(AdminStates.waiting_for_broadcast)

# --- ПРОСМОТР ЛОГОВ КОНКРЕТНОГО ЮЗЕРА ---
@dp.callback_query(F.data.startswith("userlog_"))
async def view_user_logs(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    target_id = int(call.data.replace("userlog_", ""))
    
    # Достаем последние 5 событий из базы
    logs = await history_collection.find({"owner_id": target_id}).sort("ts", -1).limit(5).to_list(length=5)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К списку кентов", callback_data="admin_users")
    
    if not logs:
        await call.message.edit_text("У этого пользователя пока нет зафиксированных удалений или правок.", reply_markup=builder.as_markup())
        return

    text = f"🗂 **Последние 5 событий кента (ID `{target_id}`):**\n\n"
    for log in logs:
        text += f"▪️ {log['text']}\n"
        text += "〰️〰️〰️〰️〰️〰️〰️\n"
        
    # Защита от переполнения сообщения
    if len(text) > 4000:
        text = text[:4000] + "...\n(Лог слишком длинный)"

    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("forceunmute_"))
async def force_unmute(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    mute_key = call.data.replace("forceunmute_", "")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К мутам", callback_data="admin_mutes")
    
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.answer("Мут принудительно снят!", show_alert=True)
        await call.message.edit_text(f"✅ Мут {mute_key} снят админом.", reply_markup=builder.as_markup())
    else:
        await call.message.edit_text("Этот мут уже не активен.", reply_markup=builder.as_markup())

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    users = await connections_collection.find({}).to_list(length=100)
    count = 0
    for u in users:
        with suppress(Exception):
            await bot.send_message(u['user_id'], f"📢 **Сообщение от создателя:**\n\n{message.text}", parse_mode="Markdown")
            count += 1
            
    await message.answer(f"✅ Успешно отправлено {count} пользователям.", reply_markup=get_admin_main_kb())
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
    
    safe_name = html.escape(message.from_user.first_name)
    safe_old = html.escape(old_text)
    safe_new = html.escape(new_text)
    owner_id = owner_data["user_id"]
    
    log_text = f"✏️ <b>Изменение от {safe_name}</b>\n<b>Было:</b> {safe_old}\n<b>Стало:</b> {safe_new}"
    
    # 1. Отправляем кенту
    with suppress(Exception):
        await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="HTML")
        
    # 2. Сохраняем в историю для админки
    with suppress(Exception):
        await history_collection.insert_one({
            "owner_id": owner_id, 
            "text": log_text, 
            "ts": datetime.now(timezone.utc)
        })
        
    with suppress(Exception): await messages_collection.update_one({"business_connection_id": conn_id, "message_id": message.message_id, "chat_id": chat_id}, {"$set": {"text": new_text}})

@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    owner_id = owner_data["user_id"]
    
    for msg_id in deleted.message_ids:
        old_msg = None
        with suppress(Exception): old_msg = await messages_collection.find_one({"business_connection_id": conn_id, "message_id": msg_id, "chat_id": deleted.chat.id})
        if old_msg:
            safe_name = html.escape(old_msg.get('first_name', 'Неизвестно'))
            safe_text = html.escape(old_msg['text'])
            
            log_text = f"🗑 <b>Удаление от {safe_name}</b>\n💬 Текст: {safe_text}"
            
            # 1. Отправляем кенту
            with suppress(Exception):
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="HTML")
                
            # 2. Сохраняем в историю для админки
            with suppress(Exception):
                await history_collection.insert_one({
                    "owner_id": owner_id, 
                    "text": log_text, 
                    "ts": datetime.now(timezone.utc)
                })

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"

    if call.from_user.id == chat_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest): await call.answer("поной!", show_alert=True)
        return

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
