import os
import asyncio
import random
import html
import re
from datetime import datetime, timezone
from aiohttp import web
from contextlib import suppress
from aiogram import Dispatcher, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, BusinessMessagesDeleted, BusinessConnection,
    BotCommand, MenuButtonCommands, BotCommandScopeDefault, BotCommandScopeChat,
    BufferedInputFile
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from simpleeval import simple_eval

# Импорт всего необходимого из нашего utils.py
from utils import (
    bot, connections_collection, users_collection, archive_collection, mutes_collection,
    muted_chats, active_amute, active_fakes, self_destruct_ids, action_dedup, copied_messages_cache,
    SUPERADMIN_ID, MENU_PHOTO, CAPTION_LIMIT, BOT_ID, BOT_USERNAME,
    parse_mute_duration, format_remaining_mute, is_chat_muted, handle_owner_activity,
    check_auto_afk, apply_format, format_mode_label, get_peer_info,
    check_and_save_protected_media, save_message_quick, notify_owner_about_edit, notify_owner_about_deletion,
    _init_drive, fetch_gift_satellite, get_gift_card_data, format_gift_card, _extract_media_info,
    force_update_all_rates, update_rates_loop, ALL_CURRENCIES,
    AUTO_MATH_CURRENCY_PATTERN, AUTO_MATH_PATTERN, MIXED_CURRENCY_FULL, CURRENCY_ALIASES,
    normalize_math_input, parse_math_expression, format_math_expression,
    send_currency_conversion, parse_mixed_currency, eval_mixed_currency, send_mixed_currency_conversion
)

import utils
dp = Dispatcher()

class UserStates(StatesGroup):
    waiting_for_afk_text = State()
    waiting_for_afk_time = State()

# ==========================================
# ВЕБ-СЕРВЕР И STARTUP
# ==========================================
_web_runner: web.AppRunner | None = None

async def dummy_handler(request):
    return web.Response(text="Multi-bot is running!")

async def start_web_server() -> web.AppRunner:
    global _web_runner
    app = web.Application()
    app.router.add_get("/", dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    _web_runner = runner
    print(f"✅ Веб-сервер запущен на порту {port}")
    return runner

async def on_startup():
    with suppress(Exception):
        await archive_collection.create_index([("owner_id", 1), ("conn_id", 1), ("chat_id", 1), ("created_at", -1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("message_id", 1)])
        await archive_collection.create_index([("user_id", 1)])
        await mutes_collection.create_index([("mute_key", 1)], unique=True)

    try:
        now_ts = datetime.now(timezone.utc).timestamp()
        stored_mutes = await mutes_collection.find({}).to_list(length=None)
        for m in stored_mutes:
            until_ts = m.get("until_ts")
            if until_ts is not None and now_ts >= until_ts:
                await mutes_collection.delete_one({"_id": m["_id"]})
            else:
                muted_chats[m["mute_key"]] = until_ts
        print(f"✅ Загружено активных мутов из базы: {len(muted_chats)}")
    except Exception as e:
        print(f"Ошибка загрузки мутов: {e}")

    asyncio.create_task(update_rates_loop())
    _init_drive()

    try:
        me = await bot.get_me()
        utils.BOT_ID = me.id
        utils.BOT_USERNAME = me.username or "your_bot_username"
        print(f"✅ Бот: @{utils.BOT_USERNAME} (ID: {utils.BOT_ID})")
    except Exception as e:
        print(f"❌ Не удалось получить данные бота: {e}")

    with suppress(Exception):
        me = await fetch_gift_satellite("/user/me")
        if me: print(f"✅ Gift Satellite: {me.get('username')}")

    with suppress(Exception):
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Главное меню")], scope=BotCommandScopeDefault())
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="admin", description="👑 Админ-панель")
        ], scope=BotCommandScopeChat(chat_id=SUPERADMIN_ID))
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

dp.startup.register(on_startup)

# ==========================================
# УТИЛИТЫ МЕНЮ И КЛАВИАТУРЫ
# ==========================================
async def is_bot_connected(user_id: int) -> bool:
    return bool(await connections_collection.find_one({"user_id": user_id}))

def get_connect_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть настройки Telegram", url="tg://settings/edit")],
        [InlineKeyboardButton(text="✅ Я подключил — проверить", callback_data="check_connection")]
    ])

def get_connect_text() -> str:
    return f"1. Настройки профиля - <a href=\"tg://settings/edit\">tg://settings/edit</a>\n\n2. «Автоматизация чатов»\n\n3. В поиск <code>{utils.BOT_USERNAME}</code> - добавить\n\n4. Дайте все права на сообщения"

async def show_menu(call: CallbackQuery, caption: str, kb, with_photo: bool = True):
    msg = call.message
    if len(caption) > CAPTION_LIMIT: with_photo = False
    try:
        if with_photo:
            if msg.photo: await msg.edit_caption(caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                with suppress(Exception): await msg.delete()
                await bot.send_photo(call.from_user.id, photo=MENU_PHOTO, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            if msg.photo:
                with suppress(Exception): await msg.delete()
                await bot.send_message(call.from_user.id, text=caption, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
            else:
                with suppress(TelegramBadRequest): await msg.edit_text(caption, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e: print(f"show_menu error: {e}")

async def get_user_main_kb(user_id: int):
    user_data = await users_collection.find_one({"user_id": user_id}) or {}
    is_afk = user_data.get("is_afk", False)
    fmt_label = format_mode_label(user_data.get("format_mode", []))
    afk_btn = InlineKeyboardButton(text="🟢 Автоответчик — ВКЛЮЧЕН" if is_afk else "🔴 Автоответчик — ВЫКЛЮЧЕН", callback_data="afk_menu")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Доступные команды", callback_data="user_cmds")],
        [InlineKeyboardButton(text="🔇 Управление мутами", callback_data="user_mutes"), InlineKeyboardButton(text="🗑 Удалённые", callback_data="deleted_menu")],
        [InlineKeyboardButton(text="👤 Мой статус", callback_data="user_status"), InlineKeyboardButton(text="💱 Валюты", callback_data="currency_settings")],
        [InlineKeyboardButton(text=f"✍️ Форматирование: {fmt_label}", callback_data="format_menu")],
        [afk_btn],
    ])

# ==========================================
# ОСНОВНЫЕ КОМАНДЫ И МЕНЮ
# ==========================================
@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"user_id": message.from_user.id}}, upsert=True)
    if message.from_user.id != SUPERADMIN_ID and not await is_bot_connected(message.from_user.id):
        await message.answer_photo(MENU_PHOTO, caption=get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        return
    await message.answer_photo(MENU_PHOTO, caption="👋 <b>Твой личный бот-секретарь.</b>\nУправляй настройками ниже:", reply_markup=await get_user_main_kb(message.from_user.id), parse_mode="HTML")

@dp.callback_query(F.data == "check_connection")
async def check_connection_handler(call: CallbackQuery):
    conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    if not conns:
        await call.answer(f"❌ Бот ещё не подключён.\n\nОткрой настройки → Автоматизация чатов → Добавить бота → введи @{utils.BOT_USERNAME}", show_alert=True)
        return
    await call.answer("✅ Подключено!", show_alert=True)
    await show_menu(call, "🏠 <b>Главное меню:</b>", await get_user_main_kb(call.from_user.id), True)

@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        await show_menu(call, get_connect_text(), get_connect_kb(), True)
        return
    await show_menu(call, "🏠 <b>Главное меню:</b>", await get_user_main_kb(call.from_user.id), True)

# ==========================================
# ФОРМАТИРОВАНИЕ
# ==========================================
async def _render_format_menu(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    builder = InlineKeyboardBuilder()
    for key, label in [("caps", "🔠 КАПС"), ("bold", "🅱️ Жирный"), ("italic", "🇮 Курсив")]:
        builder.button(text=f"{'✅ ' if key in mode else ''}{label}", callback_data=f"format_toggle|{key}")
    builder.button(text="👁 Предпросмотр", callback_data="format_preview")
    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(2, 1, 1)
    await show_menu(call, f"✍️ <b>Форматирование сообщений</b>\n\nВыбери стили:\n\n<b>Сейчас:</b> {format_mode_label(mode)}", builder.as_markup(), True)

@dp.callback_query(F.data == "format_menu")
async def format_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    await _render_format_menu(call)

@dp.callback_query(F.data.startswith("format_toggle|"))
async def format_toggle_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    key = call.data.split("|")[1]
    if key not in ("caps", "bold", "italic"): return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if key in mode: mode.remove(key)
    else: mode.append(key)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"format_mode": mode}}, upsert=True)
    await _render_format_menu(call)

@dp.callback_query(F.data == "format_preview")
async def format_preview_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if not mode:
        await call.answer("Включи хотя бы один стиль", show_alert=True)
        return
    with suppress(Exception):
        await bot.send_message(call.from_user.id, f"👁 <b>Предпросмотр:</b>\n\n{apply_format('Привет! Это пример твоего форматирования текста', mode)}", parse_mode="HTML")
    await call.answer("Отправлено в чат")

# ==========================================
# МЕНЮ УДАЛЁНОК
# ==========================================
@dp.callback_query(F.data == "deleted_menu")
async def deleted_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    pipeline = [
        {"$match": {"owner_id": call.from_user.id, "$or": [{"is_deleted": True}, {"is_edited": True}]}},
        {"$group": {"_id": {"conn_id": "$conn_id", "chat_id": "$chat_id"}, "del_count": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}}, "edit_count": {"$sum": {"$cond": [{"$eq": ["$is_edited", True]}, 1, 0]}}, "first_name": {"$last": "$peer_first_name"}, "username": {"$last": "$peer_username"}}},
        {"$sort": {"del_count": -1, "edit_count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)
    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="user_main")
        await show_menu(call, "🗑 <b>Удалённые и изменения</b>\n\nПока пусто.", builder.as_markup(), True)
        return
    for ch in chats:
        parts = [f"🗑{ch.get('del_count')}" if ch.get('del_count') else "", f"✏️{ch.get('edit_count')}" if ch.get('edit_count') else ""]
        label = f"{ch.get('first_name') or 'Без имени'} @{ch.get('username')} · {' '.join(filter(None, parts))}"
        builder.button(text=label[:60], callback_data=f"delchat|{ch['_id']['conn_id']}|{ch['_id']['chat_id']}")
    builder.button(text="🔙 Назад", callback_data="user_main").adjust(1)
    await show_menu(call, "🗑 <b>Собеседники:</b>", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("delchat|"))
async def deleted_chat_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    _, conn_id, chat_id_str = call.data.split("|")
    chat_id, owner_id = int(chat_id_str), call.from_user.id
    base = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id}
    del_total, edit_total = await archive_collection.count_documents({**base, "is_deleted": True}), await archive_collection.count_documents({**base, "is_edited": True})
    builder = InlineKeyboardBuilder()
    if del_total: builder.button(text=f"📋 Все удалённые ({del_total})", callback_data=f"delshow|{conn_id}|{chat_id}|del|0")
    if edit_total: builder.button(text=f"✏️ Все изменения ({edit_total})", callback_data=f"delshow|{conn_id}|{chat_id}|edit|0")
    builder.button(text="🔙 Назад", callback_data="deleted_menu").adjust(1)
    await show_menu(call, f"🗑 <b>Активность</b>\n\nУдалено: <b>{del_total}</b>\nИзменено: <b>{edit_total}</b>", builder.as_markup(), True)

PAGE_SIZE = 10

@dp.callback_query(F.data.startswith("delshow|"))
async def show_deleted_messages(call: CallbackQuery):
    _, conn_id, chat_id_str, mode, offset_str = call.data.split("|")
    chat_id, offset, owner_id = int(chat_id_str), int(offset_str), call.from_user.id
    base = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id}
    query, sort_field = ({**base, "is_deleted": True}, "created_at") if mode == "del" else ({**base, "is_edited": True}, "edited_at")
    total = await archive_collection.count_documents(query)
    msgs = await archive_collection.find(query).sort(sort_field, -1).skip(offset).limit(PAGE_SIZE).to_list(length=PAGE_SIZE)
    msgs.reverse()
    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return
    lines = []
    for m in msgs:
        ts_str = m.get("created_at").strftime("%d.%m %H:%M") if isinstance(m.get("created_at"), datetime) else "?"
        sender = "Я" if m.get("is_owner") else m.get("first_name")
        prefix = "🗑 " if m.get("is_deleted") and not m.get("is_edited") else "✏️ " if m.get("is_edited") else ""
        body = f"<b>Было:</b> <i>{m.get('old_text')[:400]}</i>\n<b>Стало:</b> {m.get('text')[:400]}" if m.get("is_edited") else m.get("text") or "[медиа]"
        lines.append(f"{prefix}<b>{ts_str} · {sender}</b>\n{body}\n{'─' * 18}")
    text = f"<b>📄 Показано {offset + 1}–{offset + len(msgs)} из {total}</b>\n\n" + "\n".join(lines)
    builder = InlineKeyboardBuilder()
    if (offset + len(msgs)) < total: builder.button(text="⬇️ Показать ещё", callback_data=f"delshow|{conn_id}|{chat_id}|{mode}|{offset + len(msgs)}")
    builder.button(text="🔙 Назад", callback_data=f"delchat|{conn_id}|{chat_id}").adjust(1)
    await call.message.answer(text[:3800], parse_mode="HTML", reply_markup=builder.as_markup(), disable_web_page_preview=True)

# ==========================================
# АДМИНКА
# ==========================================
@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id == SUPERADMIN_ID:
        await message.answer("👑 <b>Панель управления:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")], [InlineKeyboardButton(text="💼 Твинки", callback_data="admin_twinks")], [InlineKeyboardButton(text="🔇 Муты", callback_data="admin_mutes")]]), parse_mode="HTML")

@dp.callback_query(F.data == "admin_main")
async def admin_main(call: CallbackQuery):
    if call.from_user.id == SUPERADMIN_ID:
        await show_menu(call, "👑 <b>Панель:</b>", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")], [InlineKeyboardButton(text="💼 Твинки", callback_data="admin_twinks")], [InlineKeyboardButton(text="🔇 Муты", callback_data="admin_mutes")]]), True)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    builder = InlineKeyboardBuilder().button(text="🔙 Назад", callback_data="admin_main")
    await show_menu(call, f"📊 <b>Статистика:</b>\n\nБизнесов: {await connections_collection.count_documents({})}\nСообщений: {await archive_collection.count_documents({})}\nУдалённых: {await archive_collection.count_documents({'is_deleted': True})}\nИзменённых: {await archive_collection.count_documents({'is_edited': True})}", builder.as_markup(), True)

@dp.callback_query(F.data == "admin_twinks")
async def admin_twinks(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    conns = await connections_collection.find({}).to_list(length=100)
    builder = InlineKeyboardBuilder()
    for c in conns:
        count = await archive_collection.count_documents({"conn_id": c["business_connection_id"]})
        builder.button(text=f"💼 {c.get('first_name', 'Без имени')} @{c.get('username', '')} ({count})"[:60], callback_data=f"twink|{c['business_connection_id']}")
    builder.button(text="🔙 Назад", callback_data="admin_main").adjust(1)
    await show_menu(call, "💼 <b>Твинки:</b>", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("twink|"))
async def twink_chats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    conn_id = call.data.split("|")[1]
    pipeline = [{"$match": {"conn_id": conn_id}}, {"$group": {"_id": {"chat_id": "$chat_id"}, "count": {"$sum": 1}, "deleted": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}}, "first_name": {"$last": "$peer_first_name"}, "username": {"$last": "$peer_username"}}}, {"$sort": {"count": -1}}]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)
    builder = InlineKeyboardBuilder()
    for ch in chats:
        label = f"{ch.get('first_name') or 'Без имени'} @{ch.get('username')} · {ch['count']} msg" + (f" · 🗑 {ch.get('deleted')}" if ch.get('deleted') else "")
        builder.button(text=label[:60], callback_data=f"twinkchat|{conn_id}|{ch['_id']['chat_id']}")
    builder.button(text="🔙 Назад", callback_data="admin_twinks").adjust(1)
    await show_menu(call, "💬 <b>Переписки:</b>", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("twinkchat|"))
async def twink_full_chat(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    _, conn_id, chat_id_str = call.data.split("|")
    chat_id = int(chat_id_str)
    msgs = await archive_collection.find({"conn_id": conn_id, "chat_id": chat_id}).sort("created_at", -1).limit(50).to_list(length=50)
    msgs.reverse()
    lines = [f"{'🗑' if m.get('is_deleted') else ''}{'✏️' if m.get('is_edited') else ''} <b>{m.get('created_at').strftime('%d.%m %H:%M')} · {'Я' if m.get('is_owner') else m.get('first_name')}</b>\n{m.get('text') or '[медиа]'}\n{'─' * 15}" for m in msgs]
    await call.message.answer(f"<b>💬 Переписка (последние 50):</b>\n\n" + "\n".join(lines)[:3900], parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data == "admin_mutes")
async def admin_mutes(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    builder, has_mutes = InlineKeyboardBuilder(), False
    for mute_key in list(muted_chats.keys()):
        try:
            conn, chat_str = mute_key.rsplit("_", 1)
            if not await is_chat_muted(conn, int(chat_str)): continue
            has_mutes = True
            info = await get_peer_info(conn, int(chat_str))
            builder.button(text=f"🔇 {info.get('first_name')} @{info.get('username')} ({format_remaining_mute(muted_chats.get(mute_key))})"[:60], callback_data=f"forceunmute|{mute_key}")
        except: pass
    builder.button(text="🔙 Назад", callback_data="admin_main").adjust(1)
    await show_menu(call, "🔇 <b>Активные муты:</b>" if has_mutes else "Активных мутов нет.", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("forceunmute|"))
async def force_unmute(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID: return
    mute_key = call.data.split("|")[1]
    await mutes_collection.delete_one({"mute_key": mute_key})
    muted_chats.pop(mute_key, None)
    await show_menu(call, "✅ Мут снят.", InlineKeyboardBuilder().button(text="🔙 К мутам", callback_data="admin_mutes").as_markup(), True)

# ==========================================
# АВТООТВЕТЧИК
# ==========================================
@dp.callback_query(F.data == "afk_menu")
async def afk_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    is_afk = user_data.get("is_afk", False)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🟢 Выключить автоответчик" if is_afk else "🔴 Включить автоответчик", callback_data="toggle_afk")], [InlineKeyboardButton(text="⚙️ Настройки", callback_data="afk_settings")], [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main")]])
    await show_menu(call, f"💤 <b>Автоответчик</b>\n\nСтатус: <b>{'🟢 ВКЛ' if is_afk else '🔴 ВЫКЛ'}</b>\n<i>💡 Выключается сам, если вы напишете в чат.</i>", kb, True)

async def get_afk_settings_kb(user_id: int):
    u = await users_collection.find_one({"user_id": user_id}) or {}
    tz = u.get("tz_offset", 3)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="afk_set_text")],
        [InlineKeyboardButton(text=f"🕒 Авто-включение: {'ВКЛ' if u.get('auto_afk') else 'ВЫКЛ'}", callback_data="toggle_auto_afk")],
        [InlineKeyboardButton(text=f"⏰ Время: {u.get('afk_start', 23)}:00 - {u.get('afk_end', 7)}:00", callback_data="afk_set_time")],
        [InlineKeyboardButton(text=f"🌍 Пояс: UTC{'+' if tz >= 0 else ''}{tz}", callback_data="afk_set_tz")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="afk_menu")],
    ])

@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    await show_menu(call, "⚙️ <b>Настройки:</b>", await get_afk_settings_kb(call.from_user.id), True)

@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"is_afk": not u.get("is_afk", False)}}, upsert=True)
    await afk_menu_handler(call)

@dp.callback_query(F.data == "toggle_auto_afk")
async def toggle_auto_afk(call: CallbackQuery):
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": not u.get("auto_afk", False)}}, upsert=True)
    await show_menu(call, "⚙️ <b>Настройки:</b>", await get_afk_settings_kb(call.from_user.id), True)

@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    await show_menu(call, f"Текущий текст:\n<i>{u.get('afk_text') or 'Владелец занят. 💤'}</i>\n\nОтправь новый:", InlineKeyboardBuilder().button(text="🔙 Отмена", callback_data="afk_settings").as_markup(), True)
    await state.set_state(UserStates.waiting_for_afk_text)

@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Отправь <b>текстовое</b> сообщение.", parse_mode="HTML")
        return
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    await message.answer("✅ Сохранено!", reply_markup=await get_afk_settings_kb(message.from_user.id))
    await state.clear()

@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    await show_menu(call, "Формат: <code>23 7</code>", InlineKeyboardBuilder().button(text="🔙 Отмена", callback_data="afk_settings").as_markup(), True)
    await state.set_state(UserStates.waiting_for_afk_time)

@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Формат: <code>23 7</code>", parse_mode="HTML")
        return
    try:
        s, e = map(int, message.text.replace("-", " ").split())
        if 0 <= s <= 23 and 0 <= e <= 23:
            await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_start": s, "afk_end": e}})
            await message.answer("✅ Сохранено!", reply_markup=await get_afk_settings_kb(message.from_user.id))
            await state.clear()
        else: await message.answer("⚠ Часы 0-23")
    except: await message.answer("⚠ Формат: <code>23 7</code>", parse_mode="HTML")

@dp.callback_query(F.data == "afk_set_tz")
async def afk_set_tz(call: CallbackQuery):
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    builder = InlineKeyboardBuilder()
    for offset in range(-12, 15):
        builder.button(text=f"{'✅ ' if offset == u.get('tz_offset', 3) else ''}UTC{'+' if offset >= 0 else ''}{offset}", callback_data=f"afk_tz_set|{offset}")
    builder.button(text="🔙 Назад", callback_data="afk_settings").adjust(3, 3, 3, 3, 3, 3, 3, 3, 3, 1)
    await show_menu(call, "🌍 <b>Выбери пояс:</b>", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("afk_tz_set|"))
async def afk_tz_set_handler(call: CallbackQuery):
    offset = int(call.data.split("|")[1])
    if -12 <= offset <= 14:
        await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"tz_offset": offset}}, upsert=True)
    await afk_settings_handler(call)

# ==========================================
# ВАЛЮТЫ, СТАТУС, КОМАНДЫ, МУТЫ В МЕНЮ
# ==========================================
@dp.callback_query(F.data == "currency_settings")
async def currency_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = u.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    builder = InlineKeyboardBuilder()
    for cur in ALL_CURRENCIES: builder.button(text=f"{'✅ ' if cur in display else ''}{cur}", callback_data=f"cur_toggle|{cur}")
    builder.button(text="🔙 Назад", callback_data="user_main").adjust(2)
    await show_menu(call, "💱 <b>Выбери валюты:</b>", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("cur_toggle|"))
async def currency_toggle_handler(call: CallbackQuery):
    cur = call.data.split("|")[1]
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = u.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    if cur in display: display.remove(cur)
    else: display.append(cur)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"display_currencies": display}}, upsert=True)
    await currency_settings_handler(call)

@dp.callback_query(F.data == "user_status")
async def user_status_handler(call: CallbackQuery):
    u = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in conns]
    total_a = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}}) if conn_ids else 0
    total_d = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}, "is_deleted": True}) if conn_ids else 0
    total_e = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}, "is_edited": True}) if conn_ids else 0
    lines = [f"👤 <b>Мой статус</b>\n\n🆔 ID: <code>{call.from_user.id}</code>", f"🔗 Бот: {'✅ Да' if conns else '❌ Нет'}", f"💼 Аккаунтов: {len(conns)}\n", f"<b>📦 Архив</b>\n• Всего: <b>{total_a}</b>\n• Удалено: <b>{total_d}</b>\n• Изменено: <b>{total_e}</b>\n", f"💤 AFK: {'🟢 ВКЛ' if u.get('is_afk') else '🔴 ВЫКЛ'}", f"💱 Валюты: <code>{', '.join(u.get('display_currencies', ['RUB', 'STARS', 'TON', 'USDT']))}</code>", f"✍️ Формат: {format_mode_label(u.get('format_mode', []))}"]
    await show_menu(call, "\n".join(lines), InlineKeyboardBuilder().button(text="🔙 Назад", callback_data="user_main").as_markup(), True)

@dp.callback_query(F.data == "user_mutes")
async def user_mutes_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    conn_ids = [c["business_connection_id"] for c in await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)]
    builder, has_mutes = InlineKeyboardBuilder(), False
    for mute_key in list(muted_chats.keys()):
        try:
            conn, chat_str = mute_key.rsplit("_", 1)
            if conn not in conn_ids or not await is_chat_muted(conn, int(chat_str)): continue
            has_mutes = True
            info = await get_peer_info(conn, int(chat_str))
            builder.button(text=f"🔇 {info.get('first_name')} @{info.get('username')} · {format_remaining_mute(muted_chats.get(mute_key))}"[:60], callback_data=f"u_unmute|{mute_key}")
        except: pass
    builder.button(text="🔙 Назад", callback_data="user_main").adjust(1)
    await show_menu(call, "🔇 <b>Активные муты:</b>" if has_mutes else "Активных мутов нет.", builder.as_markup(), True)

@dp.callback_query(F.data.startswith("u_unmute|"))
async def user_unmute_callback(call: CallbackQuery):
    mute_key = call.data.split("|")[1]
    await mutes_collection.delete_one({"mute_key": mute_key})
    muted_chats.pop(mute_key, None)
    await user_mutes_handler(call)

# ==========================================
# КАТАЛОГ КОМАНД
# ==========================================
@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id): return
    await show_menu(call, "📖 <b>Справочник команд</b>\nВыбери раздел:", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛡 Модерация", callback_data="cmds_cat|mod")], [InlineKeyboardButton(text="🎬 Анимации", callback_data="cmds_cat|anim")], [InlineKeyboardButton(text="🧮 Валюты и NFT", callback_data="cmds_cat|auto")], [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main")]]), True)

@dp.callback_query(F.data.startswith("cmds_cat|"))
async def show_cmds_category(call: CallbackQuery):
    cat = call.data.split("|")[1]
    if cat == "mod":
        admin_block = "<b>📋 .коп / .вст</b>\nОтветь <code>.коп</code> на сообщение, затем <code>.вст</code>.\n\n" if call.from_user.id == SUPERADMIN_ID else ""
        text = f"🛡 <b>Модерация</b>\n\n<b>🔇 .мут [время]</b>\nПримеры: <code>.мут</code> · <code>.мут 10м</code>\n\n<b>🔁 .амут [текст]</b>\nПример: <code>.амут привет</code>\n\n<b>🎭 .фейк [тип] [сек]</b>\n<code>.фейк печать 30</code> / <code>.фейк стоп</code>\n\n{admin_block}<b>💣 .[N] [текст]</b>\nПример: <code>.10 привет</code>"
    elif cat == "anim":
        text = "🎬 <b>Анимации</b>\n\n• <code>.танос [текст]</code>\n• <code>.пак [текст]</code>\n• <code>.брут [текст]</code>\n• <code>.глитч [текст]</code>\n• <code>.табло [текст]</code>\n• <code>.п1</code> / <code>.п2</code> / <code>.п3</code>"
    else:
        text = "🧮 <b>Валюты и NFT</b>\n\n<b>💱 Конвертер</b>\n<code>100 баксов</code>\n<code>10 тон + 5 тон</code>\n\n<b>🧮 Калькулятор</b>\n<code>25 * 4</code>\n\n<b>🎁 NFT Парсер</b>\nОтветь <code>цена</code> или <code>.ц</code> на ссылку <code>t.me/nft/...</code>"
    await show_menu(call, text, InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="user_cmds")]]), True)

# ==========================================
# БИЗНЕС-СОБЫТИЯ
# ==========================================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        await connections_collection.update_one({"business_connection_id": connection.id}, {"$set": {"business_connection_id": connection.id, "user_id": connection.user.id, "first_name": connection.user.first_name or "Без имени", "username": connection.user.username or ""}}, upsert=True)
        with suppress(Exception): await bot.send_message(connection.user.id, "✅ <b>Бот успешно подключён!</b>\nОткрой /start.", parse_mode="HTML")
    else:
        with suppress(Exception): await connections_collection.delete_one({"business_connection_id": connection.id})

# ==========================================
# КОПИРОВАНИЕ И ВСТАВКА
# ==========================================
@dp.business_message(F.text.lower().startswith(".коп"))
@dp.message(F.text.lower().startswith(".коп"))
async def copy_message_superadmin(message: Message):
    if message.from_user.id != SUPERADMIN_ID: return
    conn_id = getattr(message, "business_connection_id", None)
    if conn_id:
        await check_and_save_protected_media(message, SUPERADMIN_ID)
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    else: await message.delete()
    reply = message.reply_to_message
    if reply:
        media_type, file_id, _, _, _ = _extract_media_info(reply)
        html_text = getattr(reply, "html_text", None) or reply.text or reply.caption or ""
        copied_messages_cache[SUPERADMIN_ID] = {"media_type": media_type, "file_id": file_id, "html_text": html_text}
        await users_collection.update_one({"user_id": SUPERADMIN_ID}, {"$set": {"copied_msg": copied_messages_cache[SUPERADMIN_ID]}}, upsert=True)

@dp.business_message(F.text.lower().startswith(".вст"))
@dp.message(F.text.lower().startswith(".вст"))
async def paste_message_superadmin(message: Message):
    if message.from_user.id != SUPERADMIN_ID: return
    conn_id = getattr(message, "business_connection_id", None)
    if conn_id:
        await handle_owner_activity(SUPERADMIN_ID, message.chat.id)
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    else: await message.delete()
    clip_data = copied_messages_cache.get(SUPERADMIN_ID) or (await users_collection.find_one({"user_id": SUPERADMIN_ID}) or {}).get("copied_msg")
    if not clip_data: return
    mt, fid, html_text = clip_data.get("media_type"), clip_data.get("file_id"), clip_data.get("html_text") or ""
    reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None
    with suppress(Exception):
        if mt == "photo": await bot.send_photo(message.chat.id, photo=fid, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "video": await bot.send_video(message.chat.id, video=fid, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "video_note": await bot.send_video_note(message.chat.id, video_note=fid, reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "animation": await bot.send_animation(message.chat.id, animation=fid, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "voice": await bot.send_voice(message.chat.id, voice=fid, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "audio": await bot.send_audio(message.chat.id, audio=fid, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "document": await bot.send_document(message.chat.id, document=fid, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif mt == "sticker": await bot.send_sticker(message.chat.id, sticker=fid, reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif html_text: await bot.send_message(message.chat.id, text=html_text, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)

# ==========================================
# МУТЫ И ФЕЙКИ
# ==========================================
@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    owner_data = await connections_collection.find_one({"business_connection_id": message.business_connection_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]: return
    peer_id, mute_key = message.chat.id, f"{message.business_connection_id}_{message.chat.id}"
    duration_sec, duration_label = parse_mute_duration(message.text)
    until_ts = (datetime.now(timezone.utc).timestamp() + duration_sec) if duration_sec else None
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    await mutes_collection.update_one({"mute_key": mute_key}, {"$set": {"mute_key": mute_key, "until_ts": until_ts}}, upsert=True)
    muted_chats[mute_key] = until_ts
    await bot.send_message(peer_id, f"🔇 Замьючен ({duration_label}).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute|{peer_id}")]]), business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".амут"))
async def amute_command(message: Message):
    owner_data = await connections_collection.find_one({"business_connection_id": message.business_connection_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]: return
    text = message.text[5:].strip()
    if not text: return
    await handle_owner_activity(owner_data["user_id"], message.chat.id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    sent = await bot.send_message(message.chat.id, text, business_connection_id=message.business_connection_id)
    if sent: active_amute[f"{message.business_connection_id}_{message.chat.id}"] = {"text": text, "message_id": sent.message_id}

FAKE_ACTION_MAP = {"печать": "typing", "текст": "typing", "т": "typing", "гс": "record_voice", "войс": "record_voice", "голос": "record_voice", "кружок": "record_video_note", "к": "record_video_note", "видео": "upload_video", "в": "upload_video", "фото": "upload_photo", "ф": "upload_photo", "док": "upload_document", "файл": "upload_document", "стикер": "choose_sticker", "игра": "choose_sticker"}

@dp.business_message(F.text.lower().startswith(".фейк"))
async def fake_action_command(message: Message):
    owner_data = await connections_collection.find_one({"business_connection_id": message.business_connection_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]: return
    await handle_owner_activity(owner_data["user_id"], message.chat.id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    key = f"{message.business_connection_id}_{message.chat.id}"
    if key in active_fakes: active_fakes.pop(key).cancel()
    args = message.text[5:].strip().lower().split()
    if args and args[0] in ("стоп", "stop", "выкл", "off"): return
    action, duration = "typing", 25
    for arg in args:
        if arg.isdigit(): duration = min(max(int(arg), 5), 120)
        elif arg in FAKE_ACTION_MAP: action = FAKE_ACTION_MAP[arg]
    async def _run_fake():
        end_time = datetime.now(timezone.utc).timestamp() + duration
        while datetime.now(timezone.utc).timestamp() < end_time:
            try: await bot.send_chat_action(chat_id=message.chat.id, action=action, business_connection_id=message.business_connection_id)
            except Exception as e:
                print(f"❌ Ошибка .фейк: {e}")
                break
            await asyncio.sleep(4.3)
    active_fakes[key] = asyncio.create_task(_run_fake())

@dp.business_message(F.text.regexp(r"^\.(\d+)\s+"))
async def spam_command(message: Message):
    owner_data = await connections_collection.find_one({"business_connection_id": message.business_connection_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]: return
    match = re.match(r"^\.(\d+)\s+(.*)", message.text, re.DOTALL)
    if not match: return
    count = min(int(match.group(1)), 50)
    await handle_owner_activity(owner_data["user_id"], message.chat.id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    for _ in range(count):
        with suppress(Exception): await bot.send_message(message.chat.id, match.group(2), business_connection_id=message.business_connection_id)
        await asyncio.sleep(0.5)

# ==========================================
# АНИМАЦИИ
# ==========================================
@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    full_text = message.text[3:].strip()
    await handle_owner_activity(message.from_user.id, message.chat.id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    sent_msg = await bot.send_message(message.chat.id, full_text[0], business_connection_id=message.business_connection_id)
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        await asyncio.sleep(0.25)
        with suppress(Exception): await bot.edit_message_text(message.chat.id, sent_msg.message_id, full_text[:i], business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    full_text = message.text[3:].strip()
    await handle_owner_activity(message.from_user.id, message.chat.id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    sent_msg = await bot.send_message(message.chat.id, full_text[0] + "▌", business_connection_id=message.business_connection_id)
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        await asyncio.sleep(0.25)
        with suppress(Exception): await bot.edit_message_text(message.chat.id, sent_msg.message_id, full_text[:i] + "▌", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.25)
    with suppress(Exception): await bot.edit_message_text(message.chat.id, sent_msg.message_id, full_text, business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    full_text = message.text[3:].strip()
    await handle_owner_activity(message.from_user.id, message.chat.id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    if not full_text: return
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"
    sent_msg = await bot.send_message(message.chat.id, "...", business_connection_id=message.business_connection_id)
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        idx = min(i, len(full_text))
        await asyncio.sleep(0.22)
        with suppress(Exception): await bot.edit_message_text(message.chat.id, sent_msg.message_id, full_text[:idx] + "".join(random.choice(alphabet) for _ in range(len(full_text) - idx)), business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".танос"))
async def anim_thanos(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip(): return
    raw_text, peer_id = parts[1].strip(), message.chat.id
    await handle_owner_activity(message.from_user.id, peer_id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    sent = await bot.send_message(peer_id, f"<code>{html.escape(raw_text)}</code>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    if not sent: return
    self_destruct_ids[f"{message.business_connection_id}_{sent.message_id}"] = True
    await asyncio.sleep(1.5)
    chars, indices = list(raw_text), [i for i, c in enumerate(raw_text) if c != " "]
    random.shuffle(indices)
    dust, steps, chunk_size, turning = ["░", "▒", "·", "˙", "˚"], 7, max(1, (len(indices) + 6) // 7), []
    for s in range(steps):
        for idx in turning: chars[idx] = " "
        batch = indices[s * chunk_size:(s + 1) * chunk_size]
        for idx in batch: chars[idx] = random.choice(dust)
        turning = batch
        if not "".join(chars).strip(): break
        await asyncio.sleep(0.32)
        with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, f"<code>{html.escape(''.join(chars))}</code>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.3)
    with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, "💨 <i>*испарилось*</i>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.7)
    with suppress(Exception): await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[sent.message_id])

@dp.business_message(F.text.lower().startswith(".пак"))
async def anim_pacman(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip(): return
    raw_text, peer_id = parts[1].strip(), message.chat.id
    await handle_owner_activity(message.from_user.id, peer_id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    n, step = len(raw_text), max(1, len(raw_text) // 9)
    sent = await bot.send_message(peer_id, f"<code>ᗧ{'•' * min(n, 24)}</code>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    if not sent: return
    for i, pos in enumerate(range(step, n, step)):
        await asyncio.sleep(0.28)
        with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, f"<code>{html.escape(raw_text[:pos])}</code> {'ᗤ' if i % 2 == 0 else 'ᗧ'}<code>{'•' * min(n - pos, 20)}</code>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.28)
    with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, html.escape(raw_text), parse_mode="HTML", business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".брут"))
async def anim_brute(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip(): return
    raw_text, peer_id = parts[1].strip(), message.chat.id
    await handle_owner_activity(message.from_user.id, peer_id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    n, step, hex_chars = len(raw_text), max(1, len(raw_text) // 8), "0123456789ABCDEF#$%&*@!?"
    sent = await bot.send_message(peer_id, f"🔐 <code>[{'█' * min(n, 20)}] 0%</code>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    if not sent: return
    for pos in range(0, n, step):
        await asyncio.sleep(0.26)
        with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, f"🔐 <code>{html.escape(raw_text[:pos])}{''.join(random.choice(hex_chars) for _ in range(min(n - pos, 16)))}</code> <b>[{int((pos / n) * 100)}%]</b>", parse_mode="HTML", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.26)
    with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, f"🔓 <code>{html.escape(raw_text)}</code>", parse_mode="HTML", business_connection_id=message.business_connection_id)

ZALGO_MARKS = [chr(c) for c in range(0x0300, 0x0338)]
def _zalgoify(text: str, intensity: int) -> str: return "".join(ch + (random.choice(ZALGO_MARKS) * random.randint(1, intensity) if ch != " " and intensity > 0 else "") for ch in text)

@dp.business_message(F.text.lower().startswith(".глитч"))
async def anim_glitch(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip(): return
    raw_text, peer_id = parts[1].strip(), message.chat.id
    await handle_owner_activity(message.from_user.id, peer_id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    sent = await bot.send_message(peer_id, f"⚡ {html.escape(_zalgoify(raw_text, 6))}", parse_mode="HTML", business_connection_id=message.business_connection_id)
    if not sent: return
    for lvl in [5, 3, 6, 2, 4, 1]:
        await asyncio.sleep(0.28)
        with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, f"{random.choice(['⚡', '⚠️', '📡', '🔌'])} {html.escape(_zalgoify(raw_text, lvl))}", parse_mode="HTML", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.28)
    with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, html.escape(raw_text), parse_mode="HTML", business_connection_id=message.business_connection_id)

@dp.business_message(F.text.lower().startswith(".табло"))
async def anim_ticker(message: Message):
    if message.from_user.id != (await connections_collection.find_one({"business_connection_id": message.business_connection_id}) or {}).get("user_id"): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip(): return
    raw_text, peer_id, width = parts[1].strip(), message.chat.id, 18
    await handle_owner_activity(message.from_user.id, peer_id)
    await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
    padded, total, step = (" " * width) + raw_text + (" " * width), len(raw_text) + width, max(1, (len(raw_text) + width) // 9)
    def _box(c: str) -> str: return f"<code>┏{'━' * (width + 2)}┓\n┃ {html.escape(c)} ┃\n┗{'━' * (width + 2)}┛</code>"
    sent = await bot.send_message(peer_id, _box(padded[:width]), parse_mode="HTML", business_connection_id=message.business_connection_id)
    if not sent: return
    for pos in range(step, total + 1, step):
        await asyncio.sleep(0.28)
        with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, _box(padded[pos:pos + width].ljust(width)), parse_mode="HTML", business_connection_id=message.business_connection_id)
    await asyncio.sleep(0.3)
    with suppress(Exception): await bot.edit_message_text(peer_id, sent.message_id, f"📟 <b>{html.escape(raw_text)}</b>", parse_mode="HTML", business_connection_id=message.business_connection_id)

# ==========================================
# NFT ПАРСЕР И МАТЕМАТИКА
# ==========================================
@dp.business_message(F.text.lower().in_({"цена", ".ц"}))
async def handle_nft_price_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    reply = message.reply_to_message
    if not reply or not reply.text:
        await bot.send_message(message.chat.id, "⚠️ Ответь этой командой на сообщение со ссылкой на NFT.", business_connection_id=conn_id)
        return
    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", reply.text)
    if not tg_match:
        await bot.send_message(message.chat.id, "⚠️ В сообщении нет ссылки на NFT (t.me/nft/...).", business_connection_id=conn_id)
        return
    slug = tg_match.group(1)
    loading_msg = await bot.send_message(message.chat.id, "🔍 Анализирую...", business_connection_id=conn_id)
    data = await get_gift_card_data(slug)
    result_text = await format_gift_card(data, owner_data["user_id"])
    with suppress(Exception):
        await bot.edit_message_text(chat_id=message.chat.id, message_id=loading_msg.message_id, text=result_text, parse_mode="HTML", business_connection_id=conn_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

@dp.business_message(F.text.regexp(AUTO_MATH_CURRENCY_PATTERN) | F.text.regexp(AUTO_MATH_PATTERN) | F.text.regexp(MIXED_CURRENCY_FULL))
async def auto_math_and_currency(message: Message):
    if not message.text or len(message.text) > 200 or message.text.lstrip().startswith("."): return
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    owner_id, peer_id, is_owner = owner_data["user_id"], message.chat.id, (message.from_user.id == owner_data["user_id"])
    if not is_owner and await is_chat_muted(conn_id, peer_id):
        if message.from_user.id != utils.BOT_ID: await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return
    with suppress(Exception): await save_message_quick(message, conn_id, peer_id, owner_id, owner_data.get("first_name", "owner"), is_owner)
    if is_owner:
        await check_and_save_protected_media(message, owner_id)
        await handle_owner_activity(owner_id, peer_id)
    elif await is_bot_connected(message.from_user.id): return
    dedup_key = ("calc", frozenset({owner_id, peer_id}), message.text.strip().lower())
    if dedup_key in action_dedup: return
    action_dedup[dedup_key] = True
    if MIXED_CURRENCY_FULL.match(message.text):
        parsed = parse_mixed_currency(message.text)
        if parsed:
            result_usdt = await eval_mixed_currency(parsed)
            if result_usdt is not None:
                await send_mixed_currency_conversion(message, parsed, result_usdt, owner_id)
                return
    match_curr = AUTO_MATH_CURRENCY_PATTERN.match(message.text)
    if match_curr:
        math_expr, code = match_curr.group(1).strip(), CURRENCY_ALIASES.get(match_curr.group(2).lower())
        if not code or not math_expr: return
        raw = normalize_math_input(math_expr.lower())
        parsed = parse_math_expression(raw)
        if not parsed: return
        try:
            amount = float(simple_eval(parsed))
            formatted_expr = format_math_expression(parsed)
            await send_currency_conversion(message, amount, code, original_expr=formatted_expr if len(formatted_expr.split()) > 1 else None, owner_id=owner_id)
        except: pass
        return
    raw = normalize_math_input(message.text.lower())
    parsed = parse_math_expression(raw)
    if not parsed: return
    formatted = format_math_expression(parsed)
    if len(formatted.split()) < 3: return
    try:
        result = simple_eval(parsed)
        if isinstance(result, float): result = int(result) if round(result, 4).is_integer() else round(result, 4)
    except: return
    if is_owner: await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    with suppress(Exception): await bot.send_message(peer_id, f"{formatted} = {result}", business_connection_id=conn_id)

# ==========================================
# ГЛАВНЫЙ ОБРАБОТЧИК И РЕДАКТИРОВАНИЕ
# ==========================================
@dp.business_message()
async def handle_messages(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    owner_id, owner_name, peer_id = owner_data["user_id"], owner_data.get("first_name", "owner"), message.chat.id
    is_owner = (message.from_user.id == owner_id)
    if is_owner:
        await check_and_save_protected_media(message, owner_id)
        mode = (await users_collection.find_one({"user_id": owner_id}) or {}).get("format_mode", [])
        doc = await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)
        if isinstance(mode, list) and mode and message.text and not message.text.startswith("."):
            formatted = apply_format(message.text, mode)
            if formatted != message.text:
                try:
                    await bot.edit_message_text(chat_id=peer_id, message_id=message.message_id, text=formatted, parse_mode="HTML", business_connection_id=conn_id)
                    if doc: await archive_collection.update_one({"_id": doc["_id"]}, {"$set": {"text": formatted}})
                except Exception as e: print(f"❌ Ошибка форматирования: {e}")
        await handle_owner_activity(owner_id, peer_id)
        return
    with suppress(Exception): await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)
    if await is_chat_muted(conn_id, peer_id):
        if message.from_user.id != owner_id and message.from_user.id != utils.BOT_ID: await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return
    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    if (owner_settings.get("is_afk") or (owner_settings.get("auto_afk") and check_auto_afk(owner_settings.get("afk_start", 23), owner_settings.get("afk_end", 7), owner_settings.get("tz_offset", 3)))) and (owner_id, peer_id) not in afk_cooldowns:
        await bot.send_message(peer_id, owner_settings.get("afk_text") or "Владелец сейчас занят. 💤", business_connection_id=conn_id)
        afk_cooldowns[(owner_id, peer_id)] = True

@dp.edited_business_message()
async def catch_edits(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    owner_id, peer_id, is_owner = owner_data["user_id"], message.chat.id, (message.from_user.id == owner_data["user_id"])
    old_doc = await archive_collection.find_one({"conn_id": conn_id, "chat_id": peer_id, "message_id": message.message_id})
    if not old_doc: return
    new_text, old_text, edited_at = message.text or message.caption or "[Без текста]", old_doc.get("text", ""), datetime.now(timezone.utc)
    await archive_collection.update_one({"_id": old_doc["_id"]}, {"$set": {"text": new_text, "old_text": old_text, "is_edited": True, "is_viewed": False, "edited_at": edited_at}})
    if not is_owner: await notify_owner_about_edit(owner_id, old_doc.get("first_name"), old_doc.get("username"), old_text, new_text, edited_at)

@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id, peer_id = deleted.business_connection_id, deleted.chat.id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data: return
    owner_id = owner_data["user_id"]
    for msg_id in deleted.message_ids:
        if f"{conn_id}_{msg_id}" in self_destruct_ids: continue
        session = active_amute.get(f"{conn_id}_{peer_id}")
        if session and session.get("message_id") == msg_id:
            new_sent = await bot.send_message(peer_id, session["text"], business_connection_id=conn_id)
            if new_sent: session["message_id"] = new_sent.message_id
            continue
        old_msg = await archive_collection.find_one({"conn_id": conn_id, "chat_id": peer_id, "message_id": msg_id})
        if old_msg:
            await archive_collection.update_one({"_id": old_msg["_id"]}, {"$set": {"is_deleted": True, "is_viewed": False, "deleted_at": datetime.now(timezone.utc)}})
            if not old_msg.get("is_owner"): await notify_owner_about_deletion(owner_id, old_msg.get("first_name"), old_msg.get("username"), old_msg.get("text"), old_msg.get("media_type"), old_msg.get("drive_link"), datetime.now(timezone.utc), old_msg.get("media_file_id"))

@dp.callback_query(F.data.startswith("unmute|"))
async def unmute_user(call: CallbackQuery):
    peer_id, conn_id = int(call.data.split("|")[1]), call.message.business_connection_id
    if call.from_user.id not in (await connections_collection.find_one({"business_connection_id": conn_id}) or {}).get("user_id", SUPERADMIN_ID) and call.from_user.id != SUPERADMIN_ID: return
    await mutes_collection.delete_one({"mute_key": f"{conn_id}_{peer_id}"})
    muted_chats.pop(f"{conn_id}_{peer_id}", None)
    with suppress(TelegramBadRequest): await call.message.edit_text("🔊 Мут снят."); await call.answer("Снят")

# ==========================================
# MAIN
# ==========================================
async def main():
    web_task = asyncio.create_task(start_web_server())
    web_task.add_done_callback(lambda t: print(f"❌ Веб-сервер упал: {t.exception()}") if not t.cancelled() and t.exception() else None)
    try: await dp.start_polling(bot)
    finally:
        if _web_runner: await _web_runner.cleanup()
        web_task.cancel()
        with suppress(asyncio.CancelledError): await web_task

if __name__ == "__main__":
    asyncio.run(main())
