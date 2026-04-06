#!/usr/bin/env python3
import os
import logging
import asyncio
from datetime import datetime, timedelta
from io import BytesIO

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient
from PIL import Image
import imagehash
import sqlite3

# ========== إعدادات ==========
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

DB_PATH = 'telesearch.db'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE,
            last_message_id INTEGER DEFAULT 0,
            indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS image_hashes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER,
            message_id INTEGER,
            phash TEXT,
            message_link TEXT,
            posted_date TIMESTAMP
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_phash ON image_hashes(phash)')
        conn.commit()
    logger.info("✅ DB ready")

def get_channel_id(channel_username):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute('SELECT id FROM channels WHERE channel_username = ?', (channel_username,)).fetchone()
        return row[0] if row else None

def add_channel(channel_username, last_message_id=0):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('INSERT OR IGNORE INTO channels (channel_username, last_message_id) VALUES (?, ?)',
                           (channel_username, last_message_id))
        conn.commit()
        return cur.lastrowid

def save_image_hash(channel_id, message_id, phash, message_link, posted_date):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('INSERT INTO image_hashes (channel_id, message_id, phash, message_link, posted_date) VALUES (?, ?, ?, ?, ?)',
                     (channel_id, message_id, phash, message_link, posted_date))
        conn.commit()

def search_similar_images(user_phash, threshold=5):
    results = []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute('''SELECT ih.message_link, ih.phash, c.channel_username 
                                FROM image_hashes ih JOIN channels c ON ih.channel_id = c.id''').fetchall()
        for link, db_phash, ch_name in rows:
            try:
                dist = imagehash.hex_to_hash(user_phash) - imagehash.hex_to_hash(db_phash)
                if dist <= threshold:
                    results.append((link, dist, ch_name))
            except:
                continue
    return sorted(results, key=lambda x: x[1])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🖼️ مرحباً بك في **TeleSearch Images**!\n\n"
        "أرسل رابط قناة Telegram ثم الصورة التي تريد البحث عنها.\n"
        "سأبحث خلال آخر 5 أيام."
    )

async def handle_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['channel_link'] = update.message.text.strip()
    await update.message.reply_text("✅ تم استلام الرابط. أرسل الصورة الآن.")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    channel_link = context.user_data.get('channel_link')
    if not channel_link:
        await update.message.reply_text("⚠️ أرسل رابط القناة أولاً.")
        return

    channel_username = channel_link.split('/')[-1].replace('@', '')
    await update.message.reply_text("🔍 جاري البحث...")

    client = TelegramClient(f'session_{user.id}', API_ID, API_HASH)
    await client.start()

    try:
        entity = await client.get_entity(channel_username)
        since_date = datetime.now() - timedelta(days=5)
        messages = await client.get_messages(entity, limit=200)

        for msg in messages:
            if msg.photo and msg.date >= since_date:
                photo_bytes = await client.download_media(msg, bytes)
                if photo_bytes:
                    img = Image.open(BytesIO(photo_bytes))
                    phash = str(imagehash.phash(img))
                    cid = get_channel_id(channel_username)
                    if not cid:
                        cid = add_channel(channel_username, msg.id)
                    link = f"https://t.me/{channel_username}/{msg.id}"
                    save_image_hash(cid, msg.id, phash, link, msg.date)

        photo = await update.message.photo[-1].get_file()
        user_bytes = await photo.download_as_bytearray()
        user_img = Image.open(BytesIO(user_bytes))
        user_phash = str(imagehash.phash(user_img))

        results = search_similar_images(user_phash)
        if results:
            reply = "🔍 **النتائج:**\n"
            for link, dist, _ in results[:10]:
                reply += f"• [رابط]({link}) (تشابه: {max(0, 100 - dist*2)}%)\n"
            await update.message.reply_text(reply, disable_web_page_preview=False)
        else:
            await update.message.reply_text("❌ لم يتم العثور على صور مشابهة.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطأ: {str(e)[:100]}")
    finally:
        await client.disconnect()

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_link))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
