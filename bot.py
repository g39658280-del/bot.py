from aiogram.types import BufferedInputFile

@dp.business_message(F.reply_to_message)
async def auto_save_replied_media(message: Message):
    if message.from_user.id == message.chat.id:
        return

    reply = message.reply_to_message
    if not reply:
        return

    file_id = None
    media_kind = None
    filename = "saved"

    if reply.photo:
        file_id, media_kind = reply.photo[-1].file_id, "photo"
        filename = "saved.jpg"
    elif reply.video:
        file_id, media_kind = reply.video.file_id, "video"
        filename = "saved.mp4"
    elif reply.video_note:
        file_id, media_kind = reply.video_note.file_id, "video_note"
        filename = "saved_note.mp4"
    elif reply.animation:
        file_id, media_kind = reply.animation.file_id, "animation"
        filename = "saved.mp4"
    elif reply.document:
        file_id, media_kind = reply.document.file_id, "document"
        filename = reply.document.file_name or "saved.bin"
    elif reply.audio:
        file_id, media_kind = reply.audio.file_id, "audio"
        filename = "saved.mp3"
    elif reply.voice:
        file_id, media_kind = reply.voice.file_id, "voice"
        filename = "saved.ogg"

    if not file_id:
        return

    owner_id = message.from_user.id
    sender_name = "Неизвестно"
    if reply.from_user:
        sender_name = reply.from_user.first_name or reply.from_user.username or "Без имени"

    caption = reply.caption or ""
    header = f"🕵️ <b>Сохранено от {sender_name}</b>"

    try:
        # Скачиваем и заново загружаем — обход запрета на SelfDestructingPhoto
        file = await bot.get_file(file_id)
        buffer = await bot.download_file(file.file_path)
        input_file = BufferedInputFile(buffer.read(), filename=filename)

        if media_kind == "photo":
            await bot.send_photo(chat_id=owner_id, photo=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "video":
            await bot.send_video(chat_id=owner_id, video=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "video_note":
            await bot.send_video_note(chat_id=owner_id, video_note=input_file)
            await bot.send_message(chat_id=owner_id, text=header, parse_mode="HTML")
        elif media_kind == "animation":
            await bot.send_animation(chat_id=owner_id, animation=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "document":
            await bot.send_document(chat_id=owner_id, document=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "audio":
            await bot.send_audio(chat_id=owner_id, audio=input_file, caption=header + (f"\n\n{caption}" if caption else ""), parse_mode="HTML")
        elif media_kind == "voice":
            await bot.send_voice(chat_id=owner_id, voice=input_file)
            await bot.send_message(chat_id=owner_id, text=header, parse_mode="HTML")

    except Exception as e:
        with suppress(Exception):
            await bot.send_message(chat_id=owner_id, text=f"❌ Не удалось сохранить: <code>{e}</code>", parse_mode="HTML")
        return

    log_text = f"🕵️ Авто-сейв медиа от {sender_name} ({media_kind})"
    with suppress(Exception):
        await history_collection.insert_one({"owner_id": owner_id, "text": log_text, "ts": datetime.now(timezone.utc)})
