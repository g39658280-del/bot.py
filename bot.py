import os
import asyncio
import random
import html
import io
import json
from datetime import datetime, timezone
import aiohttp
from aiohttp import web
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BusinessMessagesDeleted, BusinessConnection
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Токен берется из настроек Render.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8855259798:AAEw-jiTxWh2k0n9WjjbG7tPX64S4g5WUXU")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:xgHbZ5HMU2XDj6KZ@cluster0.6q3omrb.mongodb.net/?appName=Cluster0")
SUPERADMIN_ID = 6548121776

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client['telegram_multi_bot']
    messages_collection = db['messages']
    connections_collection = db['connections']
    users_collection = db['users']
    history_collection = db['history']
except Exception as e:
    print(f"Ошибка БД: {e}")

muted_chats = set()
afk_cooldowns = {}

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()

class UserStates(StatesGroup):
    waiting_for_afk_text = State()
    waiting_for_afk_time = State()

async def ensure_connection(conn_id: str, user_id: int, first_name: str):
    try:
        await connections_collection.update_one(
            {"business_connection_id": conn_id},
            {"$set": {"business_connection_id": conn_id, "user_id": user_id, "first_name": first_name or "Без имени"}},
            upsert=True
        )
    except Exception:
        pass

def check_auto_afk(start_h: int, end_h: int) -> bool:
    now = datetime.now(timezone.utc)
    local_hour = (now.hour + 3) % 24
    if start_h < end_h:
        return start_h <= local_hour < end_h
    else:
        return local_hour >= start_h or local_hour < end_h

# --- ВЕБ-СЕРВЕР ---
async def dummy_handler(request): return web.Response(text="Multi-bot is running!")
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def on_startup():
    try: await messages_collection.create_index("created_at", expireAfterSeconds=172800)
    except Exception: pass
dp.startup.register(on_startup)

# ==========================================
# ПОЛЬЗОВАТЕЛЬСКОЕ МЕНЮ 
# ==========================================
async def get_user_main_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    is_afk = user_data.get("is_afk", False)
    status_text = "🟢 ВЫКЛ" if not is_afk else "🔴 ВКЛ"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💤 Автоответчик: {status_text}", callback_data="toggle_afk")],
        [InlineKeyboardButton(text="⚙️ Настройки автоответчика", callback_data="afk_settings")],
        [InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes")],
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds")]
    ])

async def get_afk_settings_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    auto_afk = user_data.get("auto_afk", False)
    start_h = user_data.get("afk_start", 23)
    end_h = user_data.get("afk_end", 7)
    auto_status = "ВКЛ" if auto_afk else "ВЫКЛ"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="afk_set_text")],
        [InlineKeyboardButton(text=f"🕒 Авто-включение: {auto_status}", callback_data="toggle_auto_afk")],
        [InlineKeyboardButton(text=f"⏰ Время: {start_h}:00 - {end_h}:00", callback_data="afk_set_time")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main")]
    ])

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    with suppress(Exception): 
        await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"user_id": message.from_user.id}}, upsert=True)
    kb = await get_user_main_kb(message.from_user.id)
    await message.answer("👋 **Твой личный бот-секретарь.**\nУправляй статусом и настройками ниже:", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = await get_user_main_kb(call.from_user.id)
    await call.message.edit_text("🏠 **Главное меню:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "user_mutes")
async def user_mutes_handler(call: CallbackQuery):
    user_conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in user_conns]
    
    builder = InlineKeyboardBuilder()
    has_mutes = False
    
    for mute in list(muted_chats):
        try:
            conn, chat = mute.rsplit("_", 1)
            if conn in conn_ids:
                has_mutes = True
                builder.button(text=f"Снять мут: {chat}", callback_data=f"u_unmute_{mute}")
        except Exception:
            continue
            
    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    
    if not has_mutes:
        await call.message.edit_text("У тебя сейчас нет активных мутов.", reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await call.message.edit_text("🔇 **Твои активные муты:**\nНажми на кнопку, чтобы снять мут с собеседника.", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("u_unmute_"))
async def user_unmute_callback(call: CallbackQuery):
    mute_key = call.data.replace("u_unmute_", "")
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.answer("✅ Мут успешно снят!", show_alert=True)
    else:
        await call.answer("Мут уже был снят.", show_alert=True)
    await user_mutes_handler(call)

@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("is_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"is_afk": new_status}}, upsert=True)
    kb = await get_user_main_kb(call.from_user.id)
    await call.message.edit_text("🏠 **Главное меню:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_auto_afk")
async def toggle_auto_afk(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("auto_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": new_status}}, upsert=True)
    kb = await get_afk_settings_kb(call.from_user.id)
    await call.message.edit_text("⚙️ **Настройки автоответчика:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text(f"Текущий текст:\n_{current}_\n\nОтправь новый текст автоответчика:", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_afk_text)

@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Текст сохранен!", reply_markup=kb)
    await state.clear()

@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await call.message.edit_text("Отправь время включения и выключения в часах через пробел или дефис.\nПример: `23 7` (с 23:00 до 07:00)", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_afk_time)

@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
    try:
        parts = message.text.replace("-", " ").split()
        start_h, end_h = int(parts[0]), int(parts[1])
        if 0 <= start_h <= 23 and 0 <= end_h <= 23:
            await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_start": start_h, "afk_end": end_h}})
            kb = await get_afk_settings_kb(message.from_user.id)
            await message.answer("✅ Время сохранено!", reply_markup=kb)
            await state.clear()
        else:
            await message.answer("⚠️ Ошибка: часы должны быть от 0 до 23. Попробуй еще раз:")
    except:
        await message.answer("⚠️ Неверный формат. Напиши просто две цифры, например: `23 7`")

@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    text = (
        "📖 **Список команд (писать в чатах):**\n\n"
        "🚫 `.мут` — удаляет сообщения собеседника\n"
        "🎭 `.п1`, `.п2`, `.п3` — анимации печати\n"
        "👋 `привет`, `ку` — анимация приветствия"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main")
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())

# ==========================================
# АДМИН ПАНЕЛЬ
# ==========================================
def get_admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи и Логи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🔇 Активные муты", callback_data="admin_mutes")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])

@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != SUPERADMIN_ID: return
    await message.answer("👑 **Панель управления ботом:**", reply_markup=get_admin_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_main")
async def back_to_main_admin(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID: return
    await state.clear()
    await call.message.edit_text("👑 **Панель управления ботом:**", reply_markup=get_admin_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID: return
    action = call.data.replace("admin_", "")
    builder = InlineKeyboardBuilder()

    try:
        if action == "stats":
            users_count = await connections_collection.count_documents({})
            msgs_count = await messages_collection.count_documents({})
            logs_count = await history_collection.count_documents({})
            builder.button(text="🔙 Назад", callback_data="admin_main")
            await call.message.edit_text(f"📊 **Статистика:**\nБизнесов: {users_count}\nСообщений: {msgs_count}\nЛогов: {logs_count}", reply_markup=builder.as_markup(), parse_mode="Markdown")

        elif action == "mutes":
            if not muted_chats:
                builder.button(text="🔙 Назад", callback_data="admin_main")
                await call.message.edit_text("Активных мутов сейчас нет.", reply_markup=builder.as_markup())
                return
            for mute in list(muted_chats):
                conn, chat = mute.rsplit("_", 1)
                builder.button(text=f"Снять мут: {chat}", callback_data=f"forceunmute_{mute}")
            builder.button(text="🔙 Назад", callback_data="admin_main")
            builder.adjust(1)
            await call.message.edit_text("🔇 **Активные муты:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

        elif action == "users":
            users = await connections_collection.find({}).to_list(length=100)
            if not users:
                builder.button(text="🔙 Назад", callback_data="admin_main")
                await call.message.edit_text("Никого нет.", reply_markup=builder.as_markup())
                return
            for u in users:
                name, uid = u.get('first_name', 'Без имени'), u.get('user_id')
                builder.button(text=f"👤 {name} ({uid})", callback_data=f"userlog_{uid}")
            builder.button(text="🔙 Назад", callback_data="admin_main")
            builder.adjust(1)
            await call.message.edit_text("👥 **Выбери пользователя:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

        elif action == "broadcast":
            builder.button(text="🔙 Отмена", callback_data="admin_main")
            await call.message.edit_text("Напиши сообщение для рассылки:", reply_markup=builder.as_markup())
            await state.set_state(AdminStates.waiting_for_broadcast)
            
    except Exception as e:
        print(f"Ошибка в меню админки: {e}")
        
    with suppress(Exception):
        await call.answer()

@dp.callback_query(F.data.startswith("userlog_"))
async def view_user_logs(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    target_id = int(call.data.replace("userlog_", ""))
    logs = await history_collection.find({"owner_id": target_id}).sort("ts", -1).limit(5).to_list(length=5)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К списку кентов", callback_data="admin_users")
    if not logs:
        await call.message.edit_text("Логов пока нет.", reply_markup=builder.as_markup())
        return
    text = f"🗂 **Последние 5 событий (ID `{target_id}`):**\n\n"
    for log in logs: text += f"▪️ {log['text']}\n〰️〰️〰️〰️〰️〰️〰️\n"
    if len(text) > 4000: text = text[:4000] + "..."
    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("forceunmute_"))
async def force_unmute(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    mute_key = call.data.replace("forceunmute_", "")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К мутам", callback_data="admin_mutes")
    if mute_key in muted_chats:
        muted_chats.remove(mute_key)
        await call.message.edit_text(f"✅ Мут снят.", reply_markup=builder.as_markup())
    else:
        await call.message.edit_text("Мут уже снят.", reply_markup=builder.as_markup())
    with suppress(Exception): await call.answer()

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    users = await connections_collection.find({}).to_list(length=100)
    count = 0
    for u in users:
        with suppress(Exception):
            await bot.send_message(u['user_id'], f"📢 **Сообщение от создателя:**\n\n{message.text}", parse_mode="Markdown")
            count += 1
    await message.answer(f"✅ Отправлено {count} пользователям.", reply_markup=get_admin_main_kb())
    await state.clear()

# ==========================================
# БИЗНЕС ЛОГИКА (В ЧАТАХ)
# ==========================================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        await ensure_connection(connection.id, connection.user.id, connection.user.first_name)
    else:
        with suppress(Exception): await connections_collection.delete_one({"business_connection_id": connection.id})

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
        await bot.send_message(chat_id=message.chat.id, text="мут выдан", reply_markup=markup, business_connection_id=conn_id)

@dp.business_message(F.text.lower().startswith(".дроч"))
async def anim_droch(message: Message):
    if message.from_user.id == message.chat.id: return
    conn_id = message.business_connection_id
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    frames = ["8==✊===D", "8==✊===D", "8===✊==D", "8====✊=D", "8==✊===D", "8===✊==D", "8==✊===D", "8===✊==D", "8====✊=D", "8==✊===D", "8===✊==D", "8==✊===D", "8===✊==D", "8====✊=D", "8=====D💦", "8===✊==D", "8==✊===D", "8======D💦"]
    sent_msg = None
    with suppress(Exception): sent_msg = await bot.send_message(chat_id=message.chat.id, text=frames[0], business_connection_id=conn_id)
    if not sent_msg: return
    for frame in frames[1:]:
        await asyncio.sleep(0.2)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=frame, business_connection_id=conn_id)
    await asyncio.sleep(3.0)
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[sent_msg.message_id])

@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    if message.from_user.id == message.chat.id: return
    full_text = message.text[3:].strip()
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    sent_msg = await bot.send_message(chat_id=message.chat.id, text=full_text[0], business_connection_id=message.business_connection_id)
    if not sent_msg: return
    current_str = full_text[0]
    for char in full_text[1:]:
        current_str += char
        await asyncio.sleep(0.27)
        with suppress(Exception): await bot.edit_message_text(chat_id=message.chat.id, message_id=sent_msg.message_id, text=current_str, business_connection_id=message.business_connection_id)

# --- ПЕРЕХВАТ И РАСШИФРОВКА ГОЛОСОВЫХ ЧЕРЕЗ HUGGING FACE ---
@dp.business_message(F.voice)
async def handle_voice(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id == chat_id:
        mute_key = f"{conn_id}_{chat_id}"
        if mute_key in muted_chats:
            with suppress(Exception): await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            return

        owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
        if owner_data:
            owner_id = owner_data["user_id"]
            safe_name = html.escape(message.from_user.first_name)
            
            hf_token = os.environ.get("HF_TOKEN")
            transcribed_text = "<i>[Добавь HF_TOKEN в Render, чтобы включить расшифровку]</i>"
            
            if hf_token:
                try:
                    file_id = message.voice.file_id
                    file_info = await bot.get_file(file_id)
                    voice_io = io.BytesIO()
                    await bot.download_file(file_info.file_path, voice_io)
                    voice_data = voice_io.getvalue()
                    
                    # Используем более надежную модель и добавляем заголовок Content-Type
                    api_url = "https://api-inference.huggingface.co/models/openai/whisper-large-v3"
                    headers = {
                        "Authorization": f"Bearer {hf_token}",
                        "Content-Type": "audio/ogg"
                    }
                    
                    async with aiohttp.ClientSession() as session:
                        for i in range(4): # Делаем 4 попытки, если модель еще загружается
                            async with session.post(api_url, headers=headers, data=voice_data) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    transcribed_text = data.get('text', '').strip()
                                    break
                                elif resp.status == 503:
                                    await asyncio.sleep(5)
                                else:
                                    err_msg = await resp.text()
                                    transcribed_text = f"<i>[Ошибка HF {resp.status}: {html.escape(err_msg)}]</i>"
                                    break
                        else:
                            if "Ошибка HF" not in transcribed_text and "Добавь" not in transcribed_text:
                                transcribed_text = "<i>[Нейросеть не успела загрузиться, попробуй позже]</i>"

                except Exception as e:
                    print(f"Ошибка расшифровки: {e}")
                    transcribed_text = f"<i>[Системная ошибка: {html.escape(str(e))}]</i>"
            
            log_text = f"🎤 <b>Голосовое сообщение от {safe_name}</b>\n\n📝 <b>Текст:</b> {transcribed_text}"
            
            with suppress(Exception):
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="HTML")

# --- ПЕРЕХВАТ ТЕКСТОВЫХ И АВТООТВЕТЧИК ---
@dp.business_message(~F.voice)
async def handle_messages(message: Message):
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    if message.from_user.id != chat_id:
        await ensure_connection(conn_id, message.from_user.id, message.from_user.first_name)
        return
        
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if owner_data:
        owner_id = owner_data["user_id"]
        owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
        
        manual_afk = owner_settings.get("is_afk", False)
        auto_afk = owner_settings.get("auto_afk", False)
        in_schedule = False
        
        if auto_afk:
            s_hour = owner_settings.get("afk_start", 23)
            e_hour = owner_settings.get("afk_end", 7)
            in_schedule = check_auto_afk(s_hour, e_hour)
            
        if manual_afk or in_schedule:
            now = datetime.now().timestamp()
            last_sent = afk_cooldowns.get((owner_id, chat_id), 0)
            if now - last_sent > 300: 
                afk_text = owner_settings.get("afk_text", "Владелец сейчас занят и ответит позже. 💤")
                with suppress(Exception):
                    await bot.send_message(chat_id=chat_id, text=afk_text, business_connection_id=conn_id)
                afk_cooldowns[(owner_id, chat_id)] = now

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
    
    with suppress(Exception): await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="HTML")
    with suppress(Exception): await history_collection.insert_one({"owner_id": owner_id, "text": log_text, "ts": datetime.now(timezone.utc)})
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
            with suppress(Exception): await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="HTML")
            with suppress(Exception): await history_collection.insert_one({"owner_id": owner_id, "text": log_text, "ts": datetime.now(timezone.utc)})

@dp.callback_query(F.data.startswith("unmute_"))
async def unmute_user(call: CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{chat_id}"

    if call.from_user.id == chat_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest): await call.answer("вы не можете снять мут", show_alert=True)
        return
    if mute_key in muted_chats or call.from_user.id == SUPERADMIN_ID:
        if mute_key in muted_chats: muted_chats.remove(mute_key)
        with suppress(TelegramBadRequest):
            await call.message.edit_text("мут снят")
            await call.answer("снял")
    else:
        with suppress(TelegramBadRequest): await call.message.edit_text("уже снял")

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
