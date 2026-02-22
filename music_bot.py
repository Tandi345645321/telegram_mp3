import logging
import asyncio
import os
import uuid
import re
import json
from datetime import timedelta

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import config

logger = logging.getLogger(__name__)

# Количество результатов на странице
RESULTS_PER_PAGE = 10

# Путь к ffmpeg внутри проекта (распаковывается при сборке)
FFMPEG_PATH = os.path.join(os.path.dirname(__file__), 'ffmpeg_bin', 'ffmpeg')

# ---------- Вспомогательные функции ----------
def format_duration(seconds):
    """Форматирует длительность (секунды) в мм:сс или чч:мм:сс"""
    if not seconds:
        return "??:??"
    return str(timedelta(seconds=int(seconds))).lstrip("0:")

def clean_filename(title: str) -> str:
    """Удаляет недопустимые символы из имени файла"""
    return re.sub(r'[\\/*?:"<>|]', "", title)

# ---------- Поиск музыки через yt-dlp ----------
async def search_youtube(query: str, max_results=20):
    """
    Ищет видео на YouTube через yt-dlp (без API ключей)
    Возвращает список треков в том же формате, что и раньше
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,  # не скачиваем, только получаем информацию
        'force_generic_extractor': False,
    }

    search_query = f"ytsearch{max_results}:{query}"

    loop = asyncio.get_event_loop()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # yt-dlp синхронный, запускаем в executor
            info = await loop.run_in_executor(
                None,
                lambda: ydl.extract_info(search_query, download=False)
            )

        if not info or 'entries' not in info:
            return []

        tracks = []
        for entry in info['entries']:
            if not entry:
                continue

            # Длительность может быть None для некоторых видео
            duration = entry.get('duration', 0)

            # Исполнитель и название — пытаемся получить из title
            title = entry.get('title', 'Unknown Title')
            channel = entry.get('channel', 'Unknown Artist')

            # Иногда channel может быть пустым, тогда используем uploader
            if not channel:
                channel = entry.get('uploader', 'Unknown Artist')

            tracks.append({
                'id': entry['id'],
                'title': title,
                'artist': channel,
                'full_name': f"{channel} - {title}",
                'source': 'youtube',
                'duration': duration,
                'video_id': entry['id'],
            })

        return tracks
    except Exception as e:
        logger.exception(f"Ошибка поиска через yt-dlp: {e}")
        return []

# ---------- Скачивание трека в MP3 ----------
async def download_track_as_mp3(video_id: str, track_info: dict):
    """Скачивает аудио с YouTube, конвертирует в MP3, возвращает путь к файлу"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    filename_base = clean_filename(track_info['full_name'])
    output_template = f"/tmp/{filename_base}_{uuid.uuid4().hex[:8]}.%(ext)s"

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'max_filesize': 50 * 1024 * 1024,      # 50 MB
        'ffmpeg_location': FFMPEG_PATH,        # указываем путь к локальному ffmpeg
    }

    loop = asyncio.get_event_loop()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None,
                lambda: ydl.extract_info(url, download=True)
            )
        if info:
            downloaded_file = output_template.replace('.%(ext)s', '.mp3')
            if os.path.exists(downloaded_file):
                return downloaded_file
    except Exception as e:
        logger.error(f"Ошибка скачивания {video_id}: {e}")
    return None

# ---------- Обработчики команд ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Привет! Я музыкальный бот.\n\n"
        "Просто отправь мне название песни и исполнителя, например:\n"
        "`Imagine Dragons - Believer`\n\n"
        "Или используй команду /search <запрос>"
    )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи запрос. Например: /search Imagine Dragons - Believer")
        return

    query = ' '.join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Ищу: {query}...")

    try:
        tracks = await search_youtube(query, max_results=20)

        if not tracks:
            await status_msg.edit_text("😕 Ничего не найдено. Попробуй другой запрос.")
            return

        context.user_data['search_results'] = tracks
        context.user_data['page'] = 0
        await send_results_page(update, context, status_msg)

    except Exception as e:
        logger.exception("Ошибка поиска")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")

async def send_results_page(update: Update, context: ContextTypes.DEFAULT_TYPE, message_to_edit=None):
    tracks = context.user_data.get('search_results', [])
    page = context.user_data.get('page', 0)
    per_page = RESULTS_PER_PAGE

    if not tracks:
        return

    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(tracks))
    page_tracks = tracks[start_idx:end_idx]

    text = f"📋 Результаты поиска (стр. {page+1}/{(len(tracks)-1)//per_page + 1}):\n\n"
    keyboard = []

    for i, track in enumerate(page_tracks, start=start_idx+1):
        duration_str = format_duration(track.get('duration', 0))
        text += f"{i}. {track['full_name']} [{duration_str}]\n"
        keyboard.append([
            InlineKeyboardButton(
                f"{i}. {track['full_name'][:40]}",
                callback_data=f"dl_{i-1}"
            )
        ])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page_{page-1}"))
    if end_idx < len(tracks):
        nav_buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if message_to_edit:
        await message_to_edit.edit_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith('page_'):
        page = int(data.split('_')[1])
        context.user_data['page'] = page
        await send_results_page(update, context, query.message)

    elif data.startswith('dl_'):
        idx = int(data.split('_')[1])
        tracks = context.user_data.get('search_results', [])
        if idx < 0 or idx >= len(tracks):
            await query.edit_message_text("❌ Трек не найден.")
            return

        track = tracks[idx]
        status_msg = await query.message.reply_text(f"⬇️ Скачиваю: {track['full_name']}...")

        try:
            file_path = await download_track_as_mp3(track['video_id'], track)

            if file_path and os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    await query.message.reply_audio(
                        audio=f,
                        title=track['title'][:200],
                        performer=track['artist'][:200],
                        caption=f"🎵 {track['full_name']}",
                        duration=track.get('duration')
                    )
                os.remove(file_path)
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Не удалось скачать трек. Попробуй другой.")
        except Exception as e:
            logger.exception(f"Ошибка скачивания: {e}")
            await status_msg.edit_text(f"❌ Ошибка скачивания: {str(e)}")

def run_bot():
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("🎵 Бот запущен...")
    app.run_polling()
