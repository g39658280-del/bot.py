# ==========================================
# main.py — точка входа, dp, все хендлеры
# ==========================================
import asyncio
import html
import re
import random
from datetime import datetime, timezone
from contextlib import suppress

from aiogram import Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, BusinessMessagesDeleted, BusinessConnection,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
    BotCommand, MenuButtonCommands, BotCommandScopeDefault, BotCommandScopeChat,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from simpleeval import simple_eval

import core
from core import (
    bot, SUPERADMIN_ID, MENU_PHOTO, CAPTION_LIMIT,
    users_collection, connections_collection, archive_collection, mutes_collection,
    muted_chats, afk_cooldowns, active_amute, action_dedup,
    self_destruct_ids, active_fakes, copied_messages_cache, bot_formatted_ids,
    show_menu, apply_format, format_mode_label,
    get_connect_kb, get_connect_text, is_bot_connected,
    is_chat_muted, parse_mute_duration, format_remaining_mute, handle_owner_activity,
    get_peer_info, save_message_quick, check_and_save_protected_media, _extract_media_info,
    notify_owner_about_deletion, notify_owner_about_edit,
    fetch_gift_satellite, get_gift_card_data, format_gift_card,
    parse_math_expression, normalize_math_input, format_math_expression,
    parse_mixed_currency, eval_mixed_currency,
    send_currency_conversion, send_mixed_currency_conversion,
    AUTO_MATH_CURRENCY_PATTERN, AUTO_MATH_PATTERN, MIXED_CURRENCY_FULL, CURRENCY_ALIASES,
    check_auto_afk, start_web_server, UserStates,
    get_user_main_kb, get_afk_settings_kb, get_admin_main_kb,
    get_cmds_catalog_kb, get_cmds_back_kb, _render_format_menu,
    _init_drive, update_rates_loop,
)

dp = Dispatcher()

# ==========================================
# ЛОКАЛЬНЫЕ КОНСТАНТЫ
# ==========================================
PAGE_SIZE = 10

FAKE_ACTION_MAP = {
    "печать": "typing", "текст": "typing", "т": "typing",
    "гс": "record_voice", "войс": "record_voice", "голос": "record_voice",
    "кружок": "record_video_note", "к": "record_video_note",
    "видео": "upload_video", "в": "upload_video",
    "фото": "upload_photo", "ф": "upload_photo",
    "док": "upload_document", "файл": "upload_document",
    "стикер": "choose_sticker", "игра": "choose_sticker",
}

NFT_LINK_PATTERN = r"(t\.me/nft/[a-zA-Z0-9_-]+)"

ZALGO_MARKS = [chr(code) for code in range(0x0300, 0x0338)]


def _zalgoify(text: str, intensity: int) -> str:
    out = []
    for ch in text:
        out.append(ch)
        if ch != " " and intensity > 0:
            for _ in range(random.randint(1, intensity)):
                out.append(random.choice(ZALGO_MARKS))
    return "".join(out)

# ==========================================
# STARTUP
# ==========================================
async def on_startup():
    with suppress(Exception):
        await archive_collection.create_index([("owner_id", 1), ("conn_id", 1), ("chat_id", 1), ("created_at", -1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("message_id", 1)])
        await archive_collection.create_index([("user_id", 1)])
        await archive_collection.create_index([("conn_id", 1), ("chat_id", 1), ("is_owner", 1), ("created_at", -1)])
    with suppress(Exception):
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
        core.BOT_ID = me.id
        core.BOT_USERNAME = me.username or "your_bot_username"
        print(f"✅ Бот: @{core.BOT_USERNAME} (ID: {core.BOT_ID})")
    except Exception as e:
        print(f"❌ Не удалось получить данные бота: {e}")

    with suppress(Exception):
        me = await fetch_gift_satellite("/user/me")
        if me:
            print(f"✅ Gift Satellite: {me.get('username')}")

    with suppress(Exception):
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Главное меню")], scope=BotCommandScopeDefault())
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="admin", description="👑 Админ-панель")
        ], scope=BotCommandScopeChat(chat_id=SUPERADMIN_ID))
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


dp.startup.register(on_startup)

# ==========================================
# СТАРТ
# ==========================================
@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": message.from_user.id},
            {"$set": {"user_id": message.from_user.id}},
            upsert=True
        )
    if message.from_user.id != SUPERADMIN_ID and not await is_bot_connected(message.from_user.id):
        try:
            await message.answer_photo(photo=MENU_PHOTO, caption=get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        except Exception:
            await message.answer(get_connect_text(), reply_markup=get_connect_kb(), parse_mode="HTML")
        return
    kb = await get_user_main_kb(message.from_user.id)
    text = "👋 <b>Твой личный бот-секретарь.</b>\nУправляй настройками ниже:"
    try:
        await message.answer_photo(photo=MENU_PHOTO, caption=text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "check_connection")
async def check_connection_handler(call: CallbackQuery):
    conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    if not conns:
        await call.answer(
            "❌ Бот ещё не подключён.\n\n"
            "Открой настройки → Автоматизация чатов → Добавить бота → "
            f"введи @{core.BOT_USERNAME}",
            show_alert=True
        )
        return
    await call.answer("✅ Подключено!", show_alert=True)
    kb = await get_user_main_kb(call.from_user.id)
    await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "user_main")
async def user_main_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return
    kb = await get_user_main_kb(call.from_user.id)
    await show_menu(call, "🏠 <b>Главное меню:</b>", kb, with_photo=True)

# ==========================================
# ФОРМАТИРОВАНИЕ
# ==========================================
@dp.callback_query(F.data == "format_menu")
async def format_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return
    await _render_format_menu(call)


@dp.callback_query(F.data.startswith("format_toggle|"))
async def format_toggle_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    key = parts[1]
    if key not in ("caps", "bold", "italic"):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if not isinstance(mode, list):
        mode = []
    if key in mode:
        mode.remove(key)
    else:
        mode.append(key)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"format_mode": mode}},
        upsert=True
    )
    await _render_format_menu(call)


@dp.callback_query(F.data == "format_preview")
async def format_preview_handler(call: CallbackQuery):
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    mode = user_data.get("format_mode", [])
    if not mode:
        await call.answer("Включи хотя бы один стиль", show_alert=True)
        return
    sample = "Привет! Это пример твоего форматирования текста"
    formatted = apply_format(sample, mode)
    with suppress(Exception):
        await bot.send_message(
            chat_id=call.from_user.id,
            text=f"👁 <b>Предпросмотр:</b>\n\n{formatted}",
            parse_mode="HTML"
        )
    with suppress(TelegramBadRequest):
        await call.answer("Отправлено в чат")

# ==========================================
# МЕНЮ УДАЛЁНОК
# ==========================================
@dp.callback_query(F.data == "deleted_menu")
async def deleted_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    owner_id = call.from_user.id
    pipeline = [
        {"$match": {
            "owner_id": owner_id,
            "$or": [{"is_deleted": True}, {"is_edited": True}]
        }},
        {"$group": {
            "_id": {"conn_id": "$conn_id", "chat_id": "$chat_id"},
            "del_count": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}},
            "edit_count": {"$sum": {"$cond": [{"$eq": ["$is_edited", True]}, 1, 0]}},
            "first_name": {"$last": "$peer_first_name"},
            "username": {"$last": "$peer_username"},
        }},
        {"$sort": {"del_count": -1, "edit_count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)

    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="user_main")
        await show_menu(call, "🗑 <b>Удалённые и изменения</b>\n\nПока пусто.", builder.as_markup(), with_photo=True)
        return

    for ch in chats:
        conn_id = ch["_id"]["conn_id"]
        chat_id = ch["_id"]["chat_id"]
        name = ch.get("first_name") or "Без имени"
        uname = f"@{ch['username']}" if ch.get("username") else ""
        del_c = ch.get("del_count", 0)
        edit_c = ch.get("edit_count", 0)
        parts_label = []
        if del_c: parts_label.append(f"🗑{del_c}")
        if edit_c: parts_label.append(f"✏️{edit_c}")
        label = f"{name} {uname} · {' '.join(parts_label)}"
        builder.button(text=label[:60], callback_data=f"delchat|{conn_id}|{chat_id}")

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    await show_menu(call, "🗑 <b>Собеседники (удалённые / изменения):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("delchat|"))
async def deleted_chat_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Ошибка навигации", show_alert=True)
        return
    _, conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id

    base = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id}
    del_total = await archive_collection.count_documents({**base, "is_deleted": True})
    edit_total = await archive_collection.count_documents({**base, "is_edited": True})

    builder = InlineKeyboardBuilder()
    if del_total:
        builder.button(text=f"📋 Все удалённые ({del_total})", callback_data=f"delshow|{conn_id}|{chat_id}|del|0")
    if edit_total:
        builder.button(text=f"✏️ Все изменения ({edit_total})", callback_data=f"delshow|{conn_id}|{chat_id}|edit|0")
    if del_total + edit_total > 30:
        builder.button(text="📥 Скачать архивом", callback_data=f"deldl|{conn_id}|{chat_id}")
    builder.button(text="🔙 Назад", callback_data="deleted_menu")
    builder.adjust(1)

    await show_menu(
        call,
        f"🗑 <b>Активность собеседника</b>\n\n"
        f"Удалено: <b>{del_total}</b>\n"
        f"Изменено: <b>{edit_total}</b>",
        builder.as_markup(), with_photo=True
    )


async def _render_page(conn_id: str, chat_id: int, owner_id: int, mode: str, offset: int):
    base = {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id}
    if mode == "del":
        query = {**base, "is_deleted": True}
        sort_field = "created_at"
    elif mode == "edit":
        query = {**base, "is_edited": True}
        sort_field = "edited_at"
    else:
        return None, 0, 0, False

    total = await archive_collection.count_documents(query)
    msgs = await archive_collection.find(query).sort(sort_field, -1).skip(offset).limit(PAGE_SIZE).to_list(length=PAGE_SIZE)
    msgs.reverse()

    if not msgs:
        return None, 0, total, False

    lines = []
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m %H:%M") if isinstance(ts, datetime) else "?"
        author = m.get("first_name") or "Без имени"
        is_owner = m.get("is_owner", False)

        prefix = ""
        if m.get("is_deleted") and not m.get("is_edited"):
            prefix = "🗑 "
        elif m.get("is_edited"):
            prefix = "✏️ "

        sender = "Я" if is_owner else html.escape(author)

        if m.get("is_edited"):
            old_t = html.escape(m.get("old_text") or "[не сохранено]")
            new_t = html.escape(m.get("text") or "")
            body = f"<b>Было:</b> <i>{old_t[:400]}</i>\n<b>Стало:</b> {new_t[:400]}"
        else:
            body = html.escape(m.get("text") or "[медиа]")
            if m.get("media_type") and m.get("drive_link"):
                body += f'\n📎 <a href="{m["drive_link"]}">[{m["media_type"]}]</a>'
            elif m.get("media_type"):
                body += f'\n📎 [{m["media_type"]}] (загрузка...)'

        lines.append(f"{prefix}<b>{ts_str} · {sender}</b>\n{body}\n{'─' * 18}")

    header = f"<b>📄 Показано {offset + 1}–{offset + len(msgs)} из {total}</b>\n\n"
    text = header + "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n<i>...обрезано</i>"

    has_more = (offset + len(msgs)) < total
    return text, len(msgs), total, has_more


@dp.callback_query(F.data.startswith("delshow|"))
async def show_deleted_messages(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 5:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id, chat_id_str, mode, offset_str = parts
    chat_id = int(chat_id_str)
    offset = int(offset_str)
    owner_id = call.from_user.id

    text, shown, total, has_more = await _render_page(conn_id, chat_id, owner_id, mode, offset)
    if not text:
        await call.answer("Пусто", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if has_more:
        builder.button(
            text=f"⬇️ Показать ещё {min(PAGE_SIZE, total - offset - shown)}",
            callback_data=f"delshow|{conn_id}|{chat_id}|{mode}|{offset + shown}"
        )
    builder.button(text="🔙 К собеседнику", callback_data=f"delchat|{conn_id}|{chat_id}")
    builder.adjust(1)

    with suppress(Exception):
        await call.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup(), disable_web_page_preview=True)
    with suppress(TelegramBadRequest):
        await call.answer()


def _build_archive_html(msgs: list, chat_title: str) -> str:
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        f'<title>Архив: {html.escape(chat_title)}</title>',
        '<style>body{font-family:sans-serif;background:#0e1621;color:#e9edf1;padding:20px;max-width:900px;margin:0 auto;}',
        '.m{padding:10px 14px;background:#17212b;border-radius:8px;margin-bottom:6px;}',
        '.m.edit{background:#2b3f52;border-left:3px solid #f0a72c;}',
        '.m.del{background:#3a2124;border-left:3px solid #e05555;}',
        '.h{color:#5eb5f7;font-size:13px;margin-bottom:4px;}',
        '.o{color:#8899a6;text-decoration:line-through;}',
        '.t{white-space:pre-wrap;}</style></head><body>',
        f'<h1>Архив: {html.escape(chat_title)}</h1>',
        f'<p>Сообщений: {len(msgs)} · {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>'
    ]
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m.%Y %H:%M") if isinstance(ts, datetime) else "?"
        author = html.escape(m.get("first_name") or "Без имени")
        if m.get("is_owner"):
            author = "Я"
        cls = "m"
        if m.get("is_deleted"):
            cls += " del"
        if m.get("is_edited"):
            cls += " edit"

        if m.get("is_edited") and m.get("old_text"):
            body = f'<div class="o">{html.escape(m.get("old_text"))}</div><div class="t">{html.escape(m.get("text") or "")}</div>'
        else:
            body = f'<div class="t">{html.escape(m.get("text") or "[медиа]")}</div>'
        if m.get("drive_link"):
            body += f'<div><a href="{m["drive_link"]}" style="color:#5eb5f7;">📎 {m.get("media_type")}</a></div>'

        parts.append(f'<div class="{cls}"><div class="h">{ts_str} · {author}</div>{body}</div>')
    parts.append('</body></html>')
    return "".join(parts)


@dp.callback_query(F.data.startswith("deldl|"))
async def download_archive(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)
    owner_id = call.from_user.id

    msgs = await archive_collection.find(
        {"owner_id": owner_id, "conn_id": conn_id, "chat_id": chat_id,
         "$or": [{"is_deleted": True}, {"is_edited": True}]}
    ).sort("created_at", 1).to_list(length=5000)

    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return

    chat_title = msgs[0].get("peer_first_name") or msgs[0].get("first_name") or f"chat_{chat_id}"
    content = _build_archive_html(msgs, chat_title).encode("utf-8")
    filename = f"archive_{chat_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.html"

    input_file = BufferedInputFile(content, filename=filename)
    with suppress(Exception):
        await bot.send_document(chat_id=owner_id, document=input_file,
                                caption=f"📥 Архив: {len(msgs)} событий")
    with suppress(TelegramBadRequest):
        await call.answer("Отправлено в личку")

# ==========================================
# АДМИНКА
# ==========================================
@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return
    await message.answer("👑 <b>Панель управления:</b>", reply_markup=get_admin_main_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_main")
async def back_to_main_admin(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await show_menu(call, "👑 <b>Панель управления:</b>", get_admin_main_kb(), with_photo=True)


@dp.callback_query(F.data == "admin_twinks")
async def admin_twinks(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    conns = await connections_collection.find({}).to_list(length=100)
    builder = InlineKeyboardBuilder()
    if not conns:
        builder.button(text="🔙 Назад", callback_data="admin_main")
        await show_menu(call, "Нет подключённых аккаунтов.", builder.as_markup(), with_photo=True)
        return

    for c in conns:
        name = c.get("first_name", "Без имени")
        uname = f"@{c.get('username', '')}" if c.get("username") else ""
        conn_id = c["business_connection_id"]
        count = await archive_collection.count_documents({"conn_id": conn_id})
        label = f"💼 {name} {uname} ({count})"
        builder.button(text=label[:60], callback_data=f"twink|{conn_id}")
    builder.button(text="🔙 Назад", callback_data="admin_main")
    builder.adjust(1)
    await show_menu(call, "💼 <b>Выбери аккаунт (твинк):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("twink|"))
async def twink_chats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id = parts

    pipeline = [
        {"$match": {"conn_id": conn_id}},
        {"$group": {
            "_id": {"chat_id": "$chat_id"},
            "count": {"$sum": 1},
            "deleted": {"$sum": {"$cond": [{"$eq": ["$is_deleted", True]}, 1, 0]}},
            "first_name": {"$last": "$peer_first_name"},
            "username": {"$last": "$peer_username"},
        }},
        {"$sort": {"count": -1}},
    ]
    chats = await archive_collection.aggregate(pipeline).to_list(length=None)

    builder = InlineKeyboardBuilder()
    if not chats:
        builder.button(text="🔙 Назад", callback_data="admin_twinks")
        await show_menu(call, "У этого аккаунта пока нет переписок.", builder.as_markup(), with_photo=True)
        return

    for ch in chats:
        chat_id = ch["_id"]["chat_id"]
        name = ch.get("first_name") or "Без имени"
        uname = f"@{ch['username']}" if ch.get("username") else ""
        label = f"{name} {uname} · {ch['count']} msg"
        if ch.get("deleted"):
            label += f" · 🗑 {ch['deleted']}"
        builder.button(text=label[:60], callback_data=f"twinkchat|{conn_id}|{chat_id}")
    builder.button(text="🔙 Назад", callback_data="admin_twinks")
    builder.adjust(1)
    await show_menu(call, "💬 <b>Переписки аккаунта:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("twinkchat|"))
async def twink_full_chat(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Ошибка", show_alert=True)
        return
    _, conn_id, chat_id_str = parts
    chat_id = int(chat_id_str)

    total = await archive_collection.count_documents({"conn_id": conn_id, "chat_id": chat_id})
    deleted = await archive_collection.count_documents({"conn_id": conn_id, "chat_id": chat_id, "is_deleted": True})
    msgs = await archive_collection.find({"conn_id": conn_id, "chat_id": chat_id}).sort("created_at", -1).limit(50).to_list(length=50)
    msgs.reverse()

    if not msgs:
        await call.answer("Пусто", show_alert=True)
        return

    lines = []
    for m in msgs:
        ts = m.get("created_at")
        ts_str = ts.strftime("%d.%m %H:%M") if isinstance(ts, datetime) else "?"
        author = html.escape(m.get("first_name") or "Без имени")
        if m.get("is_owner"):
            author = "Я"
        prefix = ""
        if m.get("is_deleted"): prefix += "🗑"
        if m.get("is_edited"): prefix += "✏️"
        text_body = html.escape(m.get("text") or "[медиа]")
        if m.get("media_type") and m.get("drive_link"):
            text_body += f'\n📎 <a href="{m["drive_link"]}">[{m["media_type"]}]</a>'
        lines.append(f"{prefix} <b>{ts_str} · {author}</b>\n{text_body}\n{'─' * 15}")

    peer_name = msgs[-1].get("peer_first_name") or "Собеседник"
    peer_uname = msgs[-1].get("peer_username") or ""
    peer_str = f"{peer_name} @{peer_uname}" if peer_uname else peer_name

    header = f"<b>💬 Переписка с {html.escape(peer_str)}</b>\nВсего: {total} · Удалено: {deleted}\nПоказано: последние {len(msgs)}\n{'━' * 18}\n\n"
    full_text = header + "\n".join(lines)
    if len(full_text) > 3900:
        full_text = full_text[:3900] + "\n<i>...обрезано</i>"

    with suppress(Exception):
        await call.message.answer(full_text, parse_mode="HTML", disable_web_page_preview=True)
    with suppress(TelegramBadRequest):
        await call.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_main")

    total_archive = await archive_collection.count_documents({})
    total_deleted = await archive_collection.count_documents({"is_deleted": True})
    total_edited = await archive_collection.count_documents({"is_edited": True})
    total_conns = await connections_collection.count_documents({})

    await show_menu(
        call,
        f"📊 <b>Статистика:</b>\n\n"
        f"Бизнесов: {total_conns}\n"
        f"Сообщений в архиве: {total_archive}\n"
        f"Удалённых: {total_deleted}\n"
        f"Изменённых: {total_edited}",
        builder.as_markup(), with_photo=True
    )


@dp.callback_query(F.data == "admin_mutes")
async def admin_mutes(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    has_mutes = False
    for mute_key in list(muted_chats.keys()):
        try:
            conn, chat_str = mute_key.rsplit("_", 1)
            peer_id = int(chat_str)
            if not await is_chat_muted(conn, peer_id):
                continue
            has_mutes = True
            peer_info = await get_peer_info(conn, peer_id)
            name = peer_info.get("first_name") or chat_str
            uname = f" @{peer_info['username']}" if peer_info.get("username") else ""
            rem_str = format_remaining_mute(muted_chats.get(mute_key))
            btn_text = f"🔇 {name}{uname} ({rem_str})"
            builder.button(text=btn_text[:60], callback_data=f"forceunmute|{mute_key}")
        except Exception:
            continue

    builder.button(text="🔙 Назад", callback_data="admin_main")
    builder.adjust(1)
    if not has_mutes:
        await show_menu(call, "Активных мутов нет.", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "🔇 <b>Активные муты (нажми, чтобы снять):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("forceunmute|"))
async def force_unmute(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    mute_key = parts[1]
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К мутам", callback_data="admin_mutes")

    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})

    if mute_key in muted_chats:
        muted_chats.pop(mute_key, None)
        await show_menu(call, "✅ Мут снят.", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "Мут уже снят.", builder.as_markup(), with_photo=True)

# ==========================================
# АВТООТВЕТЧИК
# ==========================================
@dp.callback_query(F.data == "afk_menu")
async def afk_menu_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    is_afk = user_data.get("is_afk", False)

    if is_afk:
        toggle_btn = InlineKeyboardButton(text="🟢 Выключить автоответчик", callback_data="toggle_afk")
    else:
        toggle_btn = InlineKeyboardButton(text="🔴 Включить автоответчик", callback_data="toggle_afk")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [toggle_btn],
        [InlineKeyboardButton(text="⚙️ Настройки автоответчика", callback_data="afk_settings")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_main")],
    ])

    state = "🟢 ВКЛЮЧЕН" if is_afk else "🔴 ВЫКЛЮЧЕН"
    await show_menu(
        call,
        f"💤 <b>Автоответчик</b>\n\nТекущий статус: <b>{state}</b>\n"
        f"<i>💡 Автоответчик выключается сам, если вы напишете в чат вручную.</i>",
        kb, with_photo=True
    )


@dp.callback_query(F.data == "afk_settings")
async def afk_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return
    kb = await get_afk_settings_kb(call.from_user.id)
    await show_menu(call, "⚙️ <b>Настройки автоответчика:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "toggle_afk")
async def toggle_afk_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("is_afk", False)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"is_afk": new_status}},
        upsert=True
    )
    await afk_menu_handler(call)


@dp.callback_query(F.data == "toggle_auto_afk")
async def toggle_auto_afk(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    new_status = not user_data.get("auto_afk", False)
    await users_collection.update_one({"user_id": call.from_user.id}, {"$set": {"auto_afk": new_status}}, upsert=True)
    kb = await get_afk_settings_kb(call.from_user.id)
    await show_menu(call, "⚙️ <b>Настройки автоответчика:</b>", kb, with_photo=True)


@dp.callback_query(F.data == "afk_set_text")
async def afk_set_text(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("afk_text", "Владелец занят. 💤")
    if current is None:
        current = "Владелец занят. 💤"
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await show_menu(
        call,
        f"Текущий текст:\n<i>{html.escape(current)}</i>\n\nОтправь новый:",
        builder.as_markup(), with_photo=True
    )
    await state.set_state(UserStates.waiting_for_afk_text)


@dp.message(UserStates.waiting_for_afk_text)
async def save_afk_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Отправь <b>текстовое</b> сообщение.", parse_mode="HTML")
        return
    await users_collection.update_one({"user_id": message.from_user.id}, {"$set": {"afk_text": message.text}})
    kb = await get_afk_settings_kb(message.from_user.id)
    await message.answer("✅ Сохранено!", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data == "afk_set_time")
async def afk_set_time(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Отмена", callback_data="afk_settings")
    await show_menu(
        call,
        "Формат: <code>23 7</code> (с 23:00 до 07:00)",
        builder.as_markup(), with_photo=True
    )
    await state.set_state(UserStates.waiting_for_afk_time)


@dp.message(UserStates.waiting_for_afk_time)
async def save_afk_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Отправь время в формате: <code>23 7</code>", parse_mode="HTML")
        return
    try:
        parts = message.text.replace("-", " ").split()
        start_h, end_h = int(parts[0]), int(parts[1])
        if 0 <= start_h <= 23 and 0 <= end_h <= 23:
            await users_collection.update_one(
                {"user_id": message.from_user.id},
                {"$set": {"afk_start": start_h, "afk_end": end_h}}
            )
            kb = await get_afk_settings_kb(message.from_user.id)
            await message.answer("✅ Сохранено!", reply_markup=kb)
            await state.clear()
        else:
            await message.answer("⚠ Часы от 0 до 23")
    except Exception:
        await message.answer("⚠ Формат: <code>23 7</code>", parse_mode="HTML")


@dp.callback_query(F.data == "afk_set_tz")
async def afk_set_tz(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    user_data = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    current = user_data.get("tz_offset", 3)

    builder = InlineKeyboardBuilder()
    for offset in range(-12, 15):
        sign = "+" if offset >= 0 else ""
        prefix = "✅ " if offset == current else ""
        builder.button(text=f"{prefix}UTC{sign}{offset}", callback_data=f"afk_tz_set|{offset}")
    builder.button(text="🔙 Назад", callback_data="afk_settings")
    builder.adjust(3, 3, 3, 3, 3, 3, 3, 3, 3, 1)
    await show_menu(call, "🌍 <b>Выбери свой часовой пояс:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("afk_tz_set|"))
async def afk_tz_set_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    try:
        offset = int(call.data.split("|")[1])
    except (ValueError, IndexError):
        return
    if -12 <= offset <= 14:
        await users_collection.update_one(
            {"user_id": call.from_user.id},
            {"$set": {"tz_offset": offset}},
            upsert=True
        )
    await afk_settings_handler(call)

# ==========================================
# ВАЛЮТЫ (настройки)
# ==========================================
@dp.callback_query(F.data == "currency_settings")
async def currency_settings_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])

    builder = InlineKeyboardBuilder()
    for cur in core.ALL_CURRENCIES:
        if cur in display:
            builder.button(text=f"✅ {cur}", callback_data=f"cur_toggle|{cur}")
        else:
            builder.button(text=cur, callback_data=f"cur_toggle|{cur}")

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(2)
    await show_menu(call, "💱 <b>Выбери валюты:</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("cur_toggle|"))
async def currency_toggle_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    cur = parts[1]
    owner_settings = await users_collection.find_one({"user_id": call.from_user.id}) or {}
    display = owner_settings.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    if cur in display:
        display.remove(cur)
    else:
        display.append(cur)
    await users_collection.update_one(
        {"user_id": call.from_user.id},
        {"$set": {"display_currencies": display}},
        upsert=True
    )
    await currency_settings_handler(call)

# ==========================================
# МУТЫ В МЕНЮ
# ==========================================
@dp.callback_query(F.data == "user_mutes")
async def user_mutes_handler(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        kb = get_connect_kb()
        await show_menu(call, get_connect_text(), kb, with_photo=True)
        return

    user_conns = await connections_collection.find({"user_id": call.from_user.id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in user_conns]
    builder = InlineKeyboardBuilder()
    has_mutes = False

    for mute_key in list(muted_chats.keys()):
        try:
            conn, chat_str = mute_key.rsplit("_", 1)
            if conn not in conn_ids:
                continue
            peer_id = int(chat_str)
            if not await is_chat_muted(conn, peer_id):
                continue
            has_mutes = True
            peer_info = await get_peer_info(conn, peer_id)
            name = peer_info.get("first_name") or chat_str
            uname = f" @{peer_info['username']}" if peer_info.get("username") else ""
            rem_str = format_remaining_mute(muted_chats.get(mute_key))
            btn_text = f"🔇 {name}{uname} · {rem_str}"
            builder.button(text=btn_text[:60], callback_data=f"u_unmute|{mute_key}")
        except Exception:
            continue

    builder.button(text="🔙 Назад", callback_data="user_main")
    builder.adjust(1)
    if not has_mutes:
        await show_menu(call, "🔇 <b>Активных мутов нет.</b>", builder.as_markup(), with_photo=True)
    else:
        await show_menu(call, "🔇 <b>Активные муты (нажми на собеседника, чтобы размутить):</b>", builder.as_markup(), with_photo=True)


@dp.callback_query(F.data.startswith("u_unmute|"))
async def user_unmute_callback(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    mute_key = parts[1]
    try:
        conn, chat = mute_key.rsplit("_", 1)
        owner_conn = await connections_collection.find_one({
            "business_connection_id": conn,
            "user_id": call.from_user.id
        })
        if not owner_conn and call.from_user.id != SUPERADMIN_ID:
            await call.answer("Нет доступа", show_alert=True)
            return
    except Exception:
        await call.answer("Ошибка", show_alert=True)
        return

    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})

    if mute_key in muted_chats:
        muted_chats.pop(mute_key, None)
        await call.answer("✅ Мут снят!", show_alert=True)
    else:
        await call.answer("Уже снят.", show_alert=True)
    await user_mutes_handler(call)

# ==========================================
# СТАТУС
# ==========================================
@dp.callback_query(F.data == "user_status")
async def user_status_handler(call: CallbackQuery):
    owner_id = call.from_user.id
    user_data = await users_collection.find_one({"user_id": owner_id}) or {}
    is_afk = user_data.get("is_afk", False)
    afk_text = user_data.get("afk_text") or "—"
    display_cur = user_data.get("display_currencies", ["RUB", "STARS", "TON", "USDT"])
    format_mode = user_data.get("format_mode", [])
    if not isinstance(format_mode, list):
        format_mode = []

    conns = await connections_collection.find({"user_id": owner_id}).to_list(length=None)
    conn_ids = [c["business_connection_id"] for c in conns]

    total_archive = total_deleted = total_edited = 0
    if conn_ids:
        total_archive = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}})
        total_deleted = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}, "is_deleted": True})
        total_edited = await archive_collection.count_documents({"conn_id": {"$in": conn_ids}, "is_edited": True})

    connected = len(conns) > 0

    lines = ["👤 <b>Мой статус</b>", ""]
    lines.append(f"🆔 <b>ID:</b> <code>{owner_id}</code>")
    lines.append(f"🔗 <b>Бот подключён:</b> {'✅ Да' if connected else '❌ Нет'}")
    lines.append(f"💼 <b>Аккаунтов:</b> {len(conns)}")
    lines.append("")
    lines.append("<b>📦 Архив сообщений</b>")
    lines.append(f"• Всего: <b>{total_archive}</b>")
    lines.append(f"• Удалено: <b>{total_deleted}</b>")
    lines.append(f"• Изменено: <b>{total_edited}</b>")
    lines.append("")
    lines.append(f"💤 <b>Автоответчик:</b> {'🟢 ВКЛ' if is_afk else '🔴 ВЫКЛ'}")
    if is_afk and afk_text and afk_text != "—":
        lines.append(f"💬 <i>{html.escape(afk_text[:100])}</i>")
    lines.append("")
    lines.append(f"💱 <b>Валюты:</b> <code>{', '.join(display_cur)}</code>")
    lines.append(f"✍️ <b>Форматирование:</b> {format_mode_label(format_mode)}")

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="user_main")
    await show_menu(call, "\n".join(lines), builder.as_markup(), with_photo=True)

# ==========================================
# КАТАЛОГ КОМАНД
# ==========================================
@dp.callback_query(F.data == "user_cmds")
async def show_cmds(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return
    text = (
        "📖 <b>Справочник команд</b>\n\n"
        "Все команды вводятся в бизнес-чатах с точкой в начале.\n"
        "Выбери нужный раздел ниже:"
    )
    await show_menu(call, text, get_cmds_catalog_kb(), with_photo=True)


@dp.callback_query(F.data.startswith("cmds_cat|"))
async def show_cmds_category(call: CallbackQuery):
    if call.from_user.id != SUPERADMIN_ID and not await is_bot_connected(call.from_user.id):
        return

    cat = call.data.split("|")[1]

    if cat == "mod":
        admin_block = ""
        if call.from_user.id == SUPERADMIN_ID:
            admin_block = (
                "<b>📋 .коп / .вст</b> (Супер-Админ)\n"
                "Ответь <code>.коп</code> на любое сообщение (текст, стикер, ГС, кружок, видео), затем напиши <code>.вст</code> для вставки.\n\n"
            )
        text = (
            "🛡 <b>Модерация и Утилиты</b>\n\n"
            "<b>🔇 .мут [время]</b>\n"
            "Удаляет все сообщения собеседника (навсегда или по таймеру).\n"
            "Примеры: <code>.мут</code> · <code>.мут 10м</code> · <code>.мут 2ч</code> · <code>.мут 1д</code>\n\n"
            "<b>🔁 .амут [текст]</b>\n"
            "Антимут. Отправляет текст от лица бота в обход чужих мутов и защищает его 15 сек.\n"
            "Пример: <code>.амут привет</code>\n\n"
            "<b>🎭 .фейк [тип] [секунды]</b>\n"
            "Имитирует активность в шапке чата (до 120 сек).\n"
            "• <code>.фейк печать 30</code> — <i>печатает...</i>\n"
            "• <code>.фейк гс 40</code> — <i>записывает ГС...</i>\n"
            "• <code>.фейк кружок 20</code> — <i>записывает кружок...</i>\n"
            "• <code>.фейк видео 20</code> / <code>.фейк фото 15</code>\n"
            "• <code>.фейк стоп</code> — остановить имитацию\n\n"
            f"{admin_block}"
            "<b>💣 .[N] [текст]</b>\n"
            "Отправляет сообщение N раз подряд (до 50).\n"
            "Пример: <code>.10 привет</code>"
        )
    elif cat == "anim":
        text = (
            "🎬 <b>Анимации и Эффекты</b>\n\n"
            "<b>🔥 Алгоритмические спецэффекты:</b>\n"
            "• <code>.танос [текст]</code> — текст рассыпается в пыль и исчезает 💨\n"
            "• <code>.пак [текст]</code> — Пакман ᗧ съедает блоки и открывает текст\n"
            "• <code>.брут [текст]</code> — скоростной подбор пароля по буквам 🔐\n"
            "• <code>.глитч [текст]</code> — Zalgo-помехи и стабилизация сигнала ⚡\n"
            "• <code>.табло [текст]</code> — бегущая неоновая LED-строка 📟\n\n"
            "<b>⌨️ Классическая печать:</b>\n"
            "• <code>.п1 [текст]</code> — плавная печать по буквам\n"
            "• <code>.п2 [текст]</code> — печать с мигающим курсором ▌\n"
            "• <code>.п3 [текст]</code> — сборка из случайных символов"
        )
    else:
        text = (
            "🧮 <b>Валюты, Математика и NFT</b>\n\n"
            "<b>💱 Конвертер валют (на лету)</b>\n"
            "Пиши сумму или пример с валютой — бот мгновенно переведёт её:\n"
            "• <code>6 тон</code>\n"
            "• <code>100 баксов</code>\n"
            "• <code>500 звезд</code>\n"
            "• <code>10 тон + 5 тон</code>\n"
            "• <code>5 тон + 7 баксов</code> <i>(смешанные валюты)</i>\n\n"
            "<b>🧮 Умный калькулятор</b>\n"
            "Считает примеры цифрами и словами:\n"
            "• <code>25 * 4</code> → <code>25 * 4 = 100</code>\n"
            "• <code>пять плюс пять</code> → <code>5 + 5 = 10</code>\n\n"
            "<b>🎁 Парсер NFT-подарков</b>\n"
            "Отправь ссылку вида <code>t.me/nft/PlushPepe-274</code> — бот выведет карточку с редкостью, Floor-ценой и дешёвыми офферами."
        )

    await show_menu(call, text, get_cmds_back_kb(), with_photo=True)

# ==========================================
# БИЗНЕС-СОБЫТИЯ
# ==========================================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    if connection.is_enabled:
        rights_dict = {}
        if hasattr(connection, "rights") and connection.rights:
            r = connection.rights
            rights_dict = {
                "can_reply": getattr(r, "can_reply", False),
                "can_read_messages": getattr(r, "can_read_messages", False),
                "can_delete_sent_messages": getattr(r, "can_delete_sent_messages", False),
                "can_delete_all_messages": getattr(r, "can_delete_all_messages", False),
                "can_edit_name": getattr(r, "can_edit_name", False),
                "can_edit_bio": getattr(r, "can_edit_bio", False),
                "can_edit_profile_photo": getattr(r, "can_edit_profile_photo", False),
                "can_edit_username": getattr(r, "can_edit_username", False),
            }

        await connections_collection.update_one(
            {"business_connection_id": connection.id},
            {"$set": {
                "business_connection_id": connection.id,
                "user_id": connection.user.id,
                "first_name": connection.user.first_name or "Без имени",
                "username": connection.user.username or "",
                "rights": rights_dict,
                "updated_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

        with suppress(Exception):
            await bot.send_message(
                chat_id=connection.user.id,
                text=(
                    "✅ <b>Бот успешно подключён!</b>\n\n"
                    "Открой /start, чтобы пользоваться меню."
                ),
                parse_mode="HTML"
            )
    else:
        with suppress(Exception):
            await connections_collection.delete_one({"business_connection_id": connection.id})

# ==========================================
# КОПИРОВАНИЕ И ВСТАВКА (.коп / .вст)
# ==========================================
@dp.business_message(F.text.lower().startswith(".коп"))
@dp.message(F.text.lower().startswith(".коп"))
async def copy_message_superadmin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    conn_id = getattr(message, "business_connection_id", None)
    if conn_id:
        await check_and_save_protected_media(message, SUPERADMIN_ID)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    else:
        with suppress(Exception):
            await message.delete()

    reply = message.reply_to_message
    if not reply:
        return

    media_type, file_id, _, _, _ = _extract_media_info(reply)
    html_text = getattr(reply, "html_text", None) or reply.text or reply.caption or ""

    clip_data = {
        "media_type": media_type,
        "file_id": file_id,
        "html_text": html_text,
    }
    copied_messages_cache[SUPERADMIN_ID] = clip_data
    with suppress(Exception):
        await users_collection.update_one(
            {"user_id": SUPERADMIN_ID},
            {"$set": {"copied_msg": clip_data}},
            upsert=True
        )


@dp.business_message(F.text.lower().startswith(".вст"))
@dp.message(F.text.lower().startswith(".вст"))
async def paste_message_superadmin(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    conn_id = getattr(message, "business_connection_id", None)
    if conn_id:
        await handle_owner_activity(SUPERADMIN_ID, message.chat.id)
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    else:
        with suppress(Exception):
            await message.delete()

    clip_data = copied_messages_cache.get(SUPERADMIN_ID)
    if not clip_data:
        user_doc = await users_collection.find_one({"user_id": SUPERADMIN_ID}) or {}
        clip_data = user_doc.get("copied_msg")
        if clip_data:
            copied_messages_cache[SUPERADMIN_ID] = clip_data

    if not clip_data:
        return

    media_type = clip_data.get("media_type")
    file_id = clip_data.get("file_id")
    html_text = clip_data.get("html_text") or ""
    chat_id = message.chat.id
    reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None

    with suppress(Exception):
        if media_type == "photo" and file_id:
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "video" and file_id:
            await bot.send_video(chat_id=chat_id, video=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "video_note" and file_id:
            await bot.send_video_note(chat_id=chat_id, video_note=file_id, reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "animation" and file_id:
            await bot.send_animation(chat_id=chat_id, animation=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "voice" and file_id:
            await bot.send_voice(chat_id=chat_id, voice=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "audio" and file_id:
            await bot.send_audio(chat_id=chat_id, audio=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "document" and file_id:
            await bot.send_document(chat_id=chat_id, document=file_id, caption=html_text or None, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif media_type == "sticker" and file_id:
            await bot.send_sticker(chat_id=chat_id, sticker=file_id, reply_to_message_id=reply_to_id, business_connection_id=conn_id)
        elif html_text:
            await bot.send_message(chat_id=chat_id, text=html_text, parse_mode="HTML", reply_to_message_id=reply_to_id, business_connection_id=conn_id)

# ==========================================
# МУТ (.мут / .амут)
# ==========================================
@dp.business_message(F.text.lower().startswith(".мут"))
async def mute_user(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return

    peer_id = message.chat.id
    mute_key = f"{conn_id}_{peer_id}"

    duration_sec, duration_label = parse_mute_duration(message.text)
    until_ts = (datetime.now(timezone.utc).timestamp() + duration_sec) if duration_sec else None

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    with suppress(Exception):
        await mutes_collection.update_one(
            {"mute_key": mute_key},
            {"$set": {
                "mute_key": mute_key,
                "conn_id": conn_id,
                "peer_id": peer_id,
                "owner_id": owner_id,
                "until_ts": until_ts,
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

    muted_chats[mute_key] = until_ts
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute|{peer_id}")]])
    with suppress(Exception):
        await bot.send_message(
            chat_id=peer_id,
            text=f"🔇 Собеседник замьючен ({duration_label}).",
            reply_markup=markup,
            business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".амут"))
async def amute_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return

    text = message.text[5:].strip()
    if not text:
        return

    peer_id = message.chat.id
    await handle_owner_activity(owner_id, peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    sent = None
    with suppress(Exception):
        sent = await bot.send_message(chat_id=peer_id, text=text, business_connection_id=conn_id)

    if not sent:
        return

    key = f"{conn_id}_{peer_id}"
    active_amute[key] = {"text": text, "message_id": sent.message_id}

# ==========================================
# ФЕЙК-АКТИВНОСТИ
# ==========================================
@dp.business_message(F.text.lower().startswith(".фейк"))
async def fake_action_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return

    peer_id = message.chat.id
    owner_id = owner_data["user_id"]
    await handle_owner_activity(owner_id, peer_id)

    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    key = f"{conn_id}_{peer_id}"
    old_task = active_fakes.pop(key, None)
    if old_task and not old_task.done():
        old_task.cancel()

    args = message.text[5:].strip().lower().split()
    if args and args[0] in ("стоп", "stop", "выкл", "off"):
        return

    action = "typing"
    duration = 25

    for arg in args:
        if arg.isdigit():
            duration = min(max(int(arg), 5), 120)
        elif arg in FAKE_ACTION_MAP:
            action = FAKE_ACTION_MAP[arg]

    async def _run_fake():
        end_time = datetime.now(timezone.utc).timestamp() + duration
        while datetime.now(timezone.utc).timestamp() < end_time:
            try:
                await bot.send_chat_action(chat_id=peer_id, action=action, business_connection_id=conn_id)
            except Exception as e:
                print(f"❌ Ошибка .фейк: {e}")
                break
            await asyncio.sleep(4.3)

    task = asyncio.create_task(_run_fake())
    active_fakes[key] = task


@dp.business_message(F.text.regexp(r"^\.(\d+)\s+"))
async def spam_command(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if message.from_user.id != owner_id:
        return
    match = re.match(r"^\.(\d+)\s+(.*)", message.text, re.DOTALL)
    if not match:
        return
    count = min(int(match.group(1)), 50)
    peer_id = message.chat.id
    await handle_owner_activity(owner_id, peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    for _ in range(count):
        with suppress(Exception):
            await bot.send_message(chat_id=peer_id, text=match.group(2), business_connection_id=conn_id)
        await asyncio.sleep(0.5)

# ==========================================
# АНИМАЦИИ ПЕЧАТИ
# ==========================================
@dp.business_message(F.text.lower().startswith(".п1"))
async def type_animation_p1(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    full_text = message.text[3:].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=peer_id, text=full_text[0], business_connection_id=conn_id)
    if not sent_msg:
        return
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        current = full_text[:i]
        await asyncio.sleep(0.25)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current, business_connection_id=conn_id)


@dp.business_message(F.text.lower().startswith(".п2"))
async def type_animation_p2(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    full_text = message.text[3:].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    sent_msg = await bot.send_message(chat_id=peer_id, text=full_text[0] + "▌", business_connection_id=conn_id)
    if not sent_msg:
        return
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        current = full_text[:i]
        await asyncio.sleep(0.25)
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=current + "▌", business_connection_id=conn_id)
    await asyncio.sleep(0.25)
    with suppress(Exception):
        await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=full_text, business_connection_id=conn_id)


@dp.business_message(F.text.lower().startswith(".п3"))
async def type_animation_p3(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    full_text = message.text[3:].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    if not full_text:
        return
    alphabet = "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789_#@$%"
    sent_msg = await bot.send_message(chat_id=peer_id, text="...", business_connection_id=conn_id)
    if not sent_msg:
        return
    step = max(1, len(full_text) // 12)
    for i in range(step, len(full_text) + step, step):
        idx = min(i, len(full_text))
        await asyncio.sleep(0.22)
        correct = full_text[:idx]
        rnd = "".join(random.choice(alphabet) for _ in range(len(full_text) - idx))
        with suppress(Exception):
            await bot.edit_message_text(chat_id=peer_id, message_id=sent_msg.message_id, text=correct + rnd, business_connection_id=conn_id)

# ==========================================
# СПЕЦЭФФЕКТЫ
# ==========================================
@dp.business_message(F.text.lower().startswith(".танос"))
async def anim_thanos(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    sent = await bot.send_message(
        chat_id=peer_id,
        text=f"<code>{html.escape(raw_text)}</code>",
        parse_mode="HTML",
        business_connection_id=conn_id
    )
    if not sent:
        return
    self_destruct_ids[f"{conn_id}_{sent.message_id}"] = True
    await asyncio.sleep(1.5)

    chars = list(raw_text)
    indices = [i for i, c in enumerate(chars) if c != " "]
    random.shuffle(indices)
    dust_particles = ["░", "▒", "·", "˙", "˚"]
    steps = 7
    chunk_size = max(1, (len(indices) + steps - 1) // steps)
    turning_to_space = []
    for s in range(steps):
        for idx in turning_to_space:
            chars[idx] = " "
        batch = indices[s * chunk_size:(s + 1) * chunk_size]
        for idx in batch:
            chars[idx] = random.choice(dust_particles)
        turning_to_space = batch
        current_str = "".join(chars)
        if not current_str.strip():
            break
        await asyncio.sleep(0.32)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id, message_id=sent.message_id,
                text=f"<code>{html.escape(current_str)}</code>",
                parse_mode="HTML", business_connection_id=conn_id
            )
    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id, message_id=sent.message_id,
            text="💨 <i>*испарилось*</i>", parse_mode="HTML", business_connection_id=conn_id
        )
    await asyncio.sleep(0.7)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[sent.message_id])


@dp.business_message(F.text.lower().startswith(".пак"))
async def anim_pacman(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    n = len(raw_text)
    step = max(1, n // 9)
    init_dots = "•" * min(n, 24)
    sent = await bot.send_message(
        chat_id=peer_id, text=f"<code>ᗧ{init_dots}</code>",
        parse_mode="HTML", business_connection_id=conn_id
    )
    if not sent:
        return
    frame_num = 0
    for pos in range(step, n, step):
        frame_num += 1
        pac = "ᗤ" if frame_num % 2 == 1 else "ᗧ"
        revealed = html.escape(raw_text[:pos])
        dots_left = "•" * min(n - pos, 20)
        frame = f"<code>{revealed}</code> {pac}<code>{dots_left}</code>"
        await asyncio.sleep(0.28)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id, message_id=sent.message_id, text=frame,
                parse_mode="HTML", business_connection_id=conn_id
            )
    await asyncio.sleep(0.28)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id, message_id=sent.message_id,
            text=html.escape(raw_text), parse_mode="HTML", business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".брут"))
async def anim_brute(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    hex_chars = "0123456789ABCDEF#$%&*@!?"
    n = len(raw_text)
    step = max(1, n // 8)
    sent = await bot.send_message(
        chat_id=peer_id, text=f"🔐 <code>[{'█' * min(n, 20)}] 0%</code>",
        parse_mode="HTML", business_connection_id=conn_id
    )
    if not sent:
        return
    for pos in range(0, n, step):
        locked = html.escape(raw_text[:pos])
        rem_len = min(n - pos, 16)
        spinning = "".join(random.choice(hex_chars) for _ in range(rem_len))
        pct = int((pos / n) * 100)
        frame = f"🔐 <code>{locked}{spinning}</code> <b>[{pct}%]</b>"
        await asyncio.sleep(0.26)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id, message_id=sent.message_id, text=frame,
                parse_mode="HTML", business_connection_id=conn_id
            )
    await asyncio.sleep(0.26)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id, message_id=sent.message_id,
            text=f"🔓 <code>{html.escape(raw_text)}</code>",
            parse_mode="HTML", business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".глитч"))
async def anim_glitch(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    intensities = [5, 3, 6, 2, 4, 1]
    sent = await bot.send_message(
        chat_id=peer_id,
        text=f"⚡ {html.escape(_zalgoify(raw_text, 6))}",
        parse_mode="HTML", business_connection_id=conn_id
    )
    if not sent:
        return
    for lvl in intensities:
        await asyncio.sleep(0.28)
        glitched = html.escape(_zalgoify(raw_text, lvl))
        prefix = random.choice(["⚡", "⚠️", "📡", "🔌"])
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id, message_id=sent.message_id,
                text=f"{prefix} {glitched}",
                parse_mode="HTML", business_connection_id=conn_id
            )
    await asyncio.sleep(0.28)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id, message_id=sent.message_id,
            text=html.escape(raw_text), parse_mode="HTML", business_connection_id=conn_id
        )


@dp.business_message(F.text.lower().startswith(".табло"))
async def anim_ticker(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data or message.from_user.id != owner_data["user_id"]:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return
    raw_text = parts[1].strip()
    peer_id = message.chat.id
    await handle_owner_activity(owner_data["user_id"], peer_id)
    with suppress(Exception):
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])

    width = 18
    padded = (" " * width) + raw_text + (" " * width)
    total_shifts = len(padded) - width
    step = max(1, total_shifts // 9)

    def _box(content: str) -> str:
        return (
            f"<code>┏{'━' * (width + 2)}┓\n"
            f"┃ {html.escape(content)} ┃\n"
            f"┗{'━' * (width + 2)}┛</code>"
        )

    sent = await bot.send_message(
        chat_id=peer_id, text=_box(padded[:width]),
        parse_mode="HTML", business_connection_id=conn_id
    )
    if not sent:
        return
    for pos in range(step, total_shifts + 1, step):
        window = padded[pos:pos + width].ljust(width)
        await asyncio.sleep(0.28)
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=peer_id, message_id=sent.message_id,
                text=_box(window), parse_mode="HTML", business_connection_id=conn_id
            )
    await asyncio.sleep(0.3)
    with suppress(Exception):
        await bot.edit_message_text(
            chat_id=peer_id, message_id=sent.message_id,
            text=f"📟 <b>{html.escape(raw_text)}</b>",
            parse_mode="HTML", business_connection_id=conn_id
        )

# ==========================================
# NFT ПАРСЕР
# ==========================================
@dp.business_message(F.text.regexp(NFT_LINK_PATTERN))
@dp.message(F.text.regexp(NFT_LINK_PATTERN))
async def process_gift_link_auto(message: Message):
    conn_id = getattr(message, "business_connection_id", None)
    tg_match = re.search(r"t\.me/nft/([a-zA-Z0-9_-]+)", message.text)
    if not tg_match:
        return
    slug = tg_match.group(1)

    if conn_id:
        owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
        if not owner_data:
            return
        owner_id = owner_data["user_id"]
        peer_id = message.chat.id
        is_owner = (message.from_user.id == owner_id)

        if not is_owner and await is_chat_muted(conn_id, peer_id):
            if message.from_user.id != core.BOT_ID:
                with suppress(Exception):
                    await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            return

        with suppress(Exception):
            await save_message_quick(message, conn_id, peer_id, owner_id, owner_data.get("first_name", "owner"), is_owner)

        if is_owner:
            await handle_owner_activity(owner_id, peer_id)
        else:
            if await is_bot_connected(message.from_user.id):
                return

        dedup_key = ("nft", frozenset({owner_id, peer_id}), slug.lower())
        if dedup_key in action_dedup:
            return
        action_dedup[dedup_key] = True

    loading_msg = None
    with suppress(Exception):
        loading_msg = await bot.send_message(chat_id=message.chat.id, text="🔍 Анализирую...", business_connection_id=conn_id)
    data = await get_gift_card_data(slug)
    result_text = format_gift_card(data)
    if loading_msg:
        with suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                text=result_text,
                parse_mode="HTML",
                business_connection_id=conn_id
            )

# ==========================================
# МАТЕМАТИКА И ВАЛЮТЫ
# ==========================================
@dp.business_message(
    F.text.regexp(AUTO_MATH_CURRENCY_PATTERN)
    | F.text.regexp(AUTO_MATH_PATTERN)
    | F.text.regexp(MIXED_CURRENCY_FULL)
)
async def auto_math_and_currency(message: Message):
    if not message.text or len(message.text) > 200:
        return
    if message.text.lstrip().startswith("."):
        return

    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    peer_id = message.chat.id
    is_owner = (message.from_user.id == owner_id)

    if not is_owner and await is_chat_muted(conn_id, peer_id):
        if message.from_user.id != core.BOT_ID:
            with suppress(Exception):
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return

    owner_name = owner_data.get("first_name", "owner")
    with suppress(Exception):
        await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)

    if is_owner:
        await check_and_save_protected_media(message, owner_id)
        await handle_owner_activity(owner_id, peer_id)
    else:
        if await is_bot_connected(message.from_user.id):
            return

    dedup_key = ("calc", frozenset({owner_id, peer_id}), message.text.strip().lower())
    if dedup_key in action_dedup:
        return
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
        math_expr = match_curr.group(1).strip()
        tail = match_curr.group(2).lower()
        code = CURRENCY_ALIASES.get(tail)
        if not code or not math_expr:
            return
        raw = normalize_math_input(math_expr.lower())
        parsed = parse_math_expression(raw)
        if not parsed:
            return
        try:
            amount = float(simple_eval(parsed))
            formatted_expr = format_math_expression(parsed)
            await send_currency_conversion(
                message, amount, code,
                original_expr=formatted_expr if len(formatted_expr.split()) > 1 else None,
                owner_id=owner_id
            )
        except Exception as e:
            print(f"currency eval error: {e}")
            return
        return

    raw = normalize_math_input(message.text.lower())
    parsed = parse_math_expression(raw)
    if not parsed:
        return
    formatted = format_math_expression(parsed)
    if len(formatted.split()) < 3:
        return
    try:
        result = simple_eval(parsed)
        if isinstance(result, float):
            result = int(result) if round(result, 4).is_integer() else round(result, 4)
    except Exception as e:
        print(f"math eval error: {e}")
        return

    if is_owner:
        with suppress(Exception):
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
    with suppress(Exception):
        await bot.send_message(chat_id=peer_id, text=f"{formatted} = {result}", business_connection_id=conn_id)

# ==========================================
# ГЛАВНЫЙ ОБРАБОТЧИК — С ФОРМАТИРОВАНИЕМ
# ==========================================
@dp.business_message()
async def handle_messages(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return

    owner_id = owner_data["user_id"]
    owner_name = owner_data.get("first_name", "owner")
    peer_id = message.chat.id
    is_owner = (message.from_user.id == owner_id)

    if is_owner:
        await check_and_save_protected_media(message, owner_id)

        owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
        mode = owner_settings.get("format_mode", [])
        doc = await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)

        should_format = (
            isinstance(mode, list) and mode
            and message.text
            and not message.text.startswith(".")
            and not message.text.startswith("<")
        )

        if should_format:
            formatted = apply_format(message.text, mode)
            if formatted and formatted != message.text:
                try:
                    await bot.edit_message_text(
                        chat_id=peer_id,
                        message_id=message.message_id,
                        text=formatted,
                        parse_mode="HTML",
                        business_connection_id=conn_id
                    )
                    if doc:
                        await archive_collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"text": formatted}}
                        )
                except Exception as e:
                    print(f"❌ Ошибка форматирования: {e}")

        await handle_owner_activity(owner_id, peer_id)
        return

    # --- Собеседник ---
    with suppress(Exception):
        await save_message_quick(message, conn_id, peer_id, owner_id, owner_name, is_owner)

    if await is_chat_muted(conn_id, peer_id):
        if message.from_user.id != owner_id and message.from_user.id != core.BOT_ID:
            with suppress(Exception):
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        return

    owner_settings = await users_collection.find_one({"user_id": owner_id}) or {}
    manual_afk = owner_settings.get("is_afk", False)
    in_schedule = (
        check_auto_afk(
            owner_settings.get("afk_start", 23),
            owner_settings.get("afk_end", 7),
            owner_settings.get("tz_offset", 3),
        )
        if owner_settings.get("auto_afk", False) else False
    )

    if manual_afk or in_schedule:
        if (owner_id, peer_id) not in afk_cooldowns:
            afk_text = owner_settings.get("afk_text") or "Владелец сейчас занят. 💤"
            with suppress(Exception):
                await bot.send_message(chat_id=peer_id, text=afk_text, business_connection_id=conn_id)
            afk_cooldowns[(owner_id, peer_id)] = True

# ==========================================
# РЕДАКТИРОВАНИЯ И УДАЛЕНИЯ
# ==========================================
@dp.edited_business_message()
async def catch_edits(message: Message):
    conn_id = message.business_connection_id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    peer_id = message.chat.id
    is_owner = (message.from_user.id == owner_id)

    new_text = message.text or message.caption or "[Без текста]"
    old_doc = await archive_collection.find_one({
        "conn_id": conn_id, "chat_id": peer_id, "message_id": message.message_id
    })
    if not old_doc:
        return

    old_text = old_doc.get("text", "")
    edited_at = datetime.now(timezone.utc)

    with suppress(Exception):
        await archive_collection.update_one(
            {"_id": old_doc["_id"]},
            {"$set": {
                "text": new_text,
                "old_text": old_text,
                "is_edited": True,
                "is_viewed": False,
                "edited_at": edited_at
            }}
        )

    if not is_owner:
        sender_name = old_doc.get("first_name") or message.from_user.first_name or "Неизвестный"
        sender_username = old_doc.get("username") or message.from_user.username or ""
        await notify_owner_about_edit(owner_id, sender_name, sender_username, old_text, new_text, edited_at)


@dp.deleted_business_messages()
async def catch_deletions(deleted: BusinessMessagesDeleted):
    conn_id = deleted.business_connection_id
    peer_id = deleted.chat.id
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]

    for msg_id in deleted.message_ids:
        if f"{conn_id}_{msg_id}" in self_destruct_ids:
            continue

        key = f"{conn_id}_{peer_id}"
        session = active_amute.get(key)
        if session and session.get("message_id") == msg_id:
            new_sent = None
            with suppress(Exception):
                new_sent = await bot.send_message(
                    chat_id=peer_id, text=session["text"], business_connection_id=conn_id
                )
            if new_sent:
                session["message_id"] = new_sent.message_id
            continue

        old_msg = await archive_collection.find_one({
            "conn_id": conn_id, "chat_id": peer_id, "message_id": msg_id
        })
        deleted_at = datetime.now(timezone.utc)

        if old_msg:
            with suppress(Exception):
                await archive_collection.update_one(
                    {"_id": old_msg["_id"]},
                    {"$set": {"is_deleted": True, "is_viewed": False, "deleted_at": deleted_at}}
                )
            if not old_msg.get("is_owner"):
                await notify_owner_about_deletion(
                    owner_id,
                    old_msg.get("first_name") or "Неизвестный",
                    old_msg.get("username", ""),
                    old_msg.get("text") or "",
                    old_msg.get("media_type"),
                    old_msg.get("drive_link"),
                    deleted_at,
                    file_id=old_msg.get("media_file_id")
                )

# ==========================================
# СНЯТИЕ МУТА В ЧАТЕ
# ==========================================
@dp.callback_query(F.data.startswith("unmute|"))
async def unmute_user(call: CallbackQuery):
    parts = call.data.split("|")
    if len(parts) != 2:
        return
    try:
        peer_id = int(parts[1])
    except ValueError:
        return
    conn_id = call.message.business_connection_id
    mute_key = f"{conn_id}_{peer_id}"
    owner_data = await connections_collection.find_one({"business_connection_id": conn_id})
    if not owner_data:
        return
    owner_id = owner_data["user_id"]
    if call.from_user.id != owner_id and call.from_user.id != SUPERADMIN_ID:
        with suppress(TelegramBadRequest):
            await call.answer("Нельзя", show_alert=True)
        return
    with suppress(Exception):
        await mutes_collection.delete_one({"mute_key": mute_key})
    muted_chats.pop(mute_key, None)
    with suppress(TelegramBadRequest):
        await call.message.edit_text("🔊 Мут снят.")
        await call.answer("Мут снят")

# ==========================================
# MAIN
# ==========================================
async def main():
    web_task = asyncio.create_task(start_web_server())

    def _web_done(t: asyncio.Task):
        try:
            t.result()
        except Exception as e:
            print(f"❌ Веб-сервер упал: {e}")

    web_task.add_done_callback(_web_done)

    try:
        await dp.start_polling(bot)
    finally:
        if core._web_runner:
            with suppress(Exception):
                await core._web_runner.cleanup()
        web_task.cancel()
        with suppress(asyncio.CancelledError):
            await web_task


if __name__ == "__main__":
    asyncio.run(main())
