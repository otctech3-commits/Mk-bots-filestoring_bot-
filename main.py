import os
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import FloodWait, UserNotParticipant
from pymongo import MongoClient
from datetime import datetime, timedelta
import string
import random
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CONFIG FROM ENV
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")
FILE_CHANNEL = int(os.environ.get("FILE_CHANNEL")) # -100xxx
ADMINS = list(map(int, os.environ.get("ADMINS", "").split()))
PORT = int(os.environ.get("PORT", 8080))

# INIT
app = Client("file_store", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = MongoClient(MONGO_URL)
db = mongo["filestore"]
settings_db = db["settings"]
files_db = db["files"]
special_links_db = db["special_links"]

# DEFAULT SETTINGS
DEFAULT_SETTINGS = {
    "_id": "bot_settings",
    "start_pic": "https://telegra.ph/file/8b42f9d4f6e4e6d4f4e4e.jpg",
    "start_text": "**🔥 Welcome to {bot_name}**\n\nI can store your files and give you shareable links.\n\n**Join our channels for updates**",
    "main_btn_text": "🌐 Our Website",
    "main_btn_url": "https://mk-bots.blogspot.com",
    "small_btns": [
        {"text": "📢 MK-BOTS TG", "url": "https://t.me/mkbots0"},
        {"text": "♥️ MK-BOTS WEB", "url": "https://mk-bots.blogspot.com"},
        {"text": "💬 Support", "url": "https://t.me/"},
        {"text": "ℹ️ About", "callback": "about"}
    ],
    "auto_delete": 0, # 0 = no delete, else minutes
    "shortener": "off",
    "shortener_api": "",
    "shortener_url": "",
    "watermark": "Powered by @mkbots0",
    "force_sub": [], # channel ids
    "no_forward": True
}

async def get_settings():
    settings = settings_db.find_one({"_id": "bot_settings"})
    if not settings:
        settings_db.insert_one(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS
    return settings

async def is_admin(user_id):
    return user_id in ADMINS

def gen_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def check_force_sub(client, user_id):
    settings = await get_settings()
    if not settings["force_sub"]:
        return True
    for channel in settings["force_sub"]:
        try:
            await client.get_chat_member(channel, user_id)
        except UserNotParticipant:
            return False
    return True

# KEEP ALIVE FOR RENDER FREE
async def keep_alive():
    while True:
        await asyncio.sleep(300)
        try:
            await app.get_me()
            logger.info("Keep-alive ping sent")
        except:
            pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    settings = await get_settings()

    # Force sub check
    if not await check_force_sub(client, message.from_user.id):
        btns = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/c/{str(abs(ch))[4:]}")] for ch in settings["force_sub"]]
        btns.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_sub")])
        return await message.reply("**You must join our channels to use this bot**", reply_markup=InlineKeyboardMarkup(btns))

    # Check if file link
    if len(message.command) > 1:
        data = message.command[1]
        if data.startswith("file_"):
            return await send_file(client, message, data.split("_")[1])
        elif data.startswith("batch_"):
            return await send_batch(client, message, data.split("_")[1])
        elif data.startswith("special_"):
            return await send_special(client, message, data.split("_")[1])

    # Normal start
    btns = [[InlineKeyboardButton(settings["main_btn_text"], url=settings["main_btn_url"])]]
    row = []
    for i, btn in enumerate(settings["small_btns"]):
        if "url" in btn:
            row.append(InlineKeyboardButton(btn["text"], url=btn["url"]))
        else:
            row.append(InlineKeyboardButton(btn["text"], callback_data=btn["callback"]))
        if len(row) == 2:
            btns.append(row)
            row = []
    if row:
        btns.append(row)

    await message.reply_photo(
        photo=settings["start_pic"],
        caption=settings["start_text"].format(bot_name=(await client.get_me()).first_name),
        reply_markup=InlineKeyboardMarkup(btns)
    )

@app.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo))
async def save_file(client, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("**Only admins can upload files**")

    settings = await get_settings()
    code = gen_code()

    # Copy to file channel without forward tag
    try:
        if settings["no_forward"]:
            msg = await message.copy(FILE_CHANNEL)
        else:
            msg = await message.forward(FILE_CHANNEL)

        # Add watermark if text caption
        if msg.caption and settings["watermark"]:
            await msg.edit_caption(f"{msg.caption}\n\n__{settings['watermark']}__")

        files_db.insert_one({
            "_id": code,
            "msg_id": msg.id,
            "user_id": message.from_user.id,
            "date": datetime.now()
        })

        link = f"https://t.me/{(await client.get_me()).username}?start=file_{code}"

        # Shorten if enabled
        if settings["shortener"] == "on" and settings["shortener_api"]:
            link = await shorten_url(link, settings)

        btn = [[InlineKeyboardButton("🔗 Share Link", url=f"https://t.me/share/url?url={link}")]]
        await message.reply(f"**✅ File Saved**\n\n**Link:** `{link}`", reply_markup=InlineKeyboardMarkup(btn))

    except Exception as e:
        await message.reply(f"**Error:** {str(e)}")

async def send_file(client, message, code):
    file_data = files_db.find_one({"_id": code})
    if not file_data:
        return await message.reply("**File not found or deleted**")

    settings = await get_settings()
    try:
        msg = await client.copy_message(
            message.chat.id,
            FILE_CHANNEL,
            file_data["msg_id"]
        )

        # Auto delete
        if settings["auto_delete"] > 0:
            await asyncio.sleep(settings["auto_delete"] * 60)
            await msg.delete()
            await message.reply("**File deleted due to auto-delete timer**")

    except Exception as e:
        await message.reply("**Failed to send file**")

@app.on_message(filters.command("batch") & filters.private)
async def batch_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        return

    if len(message.command)!= 3:
        return await message.reply("**Usage:** `/batch start_msg_id end_msg_id`")

    try:
        start_id = int(message.command[1])
        end_id = int(message.command[2])
        code = gen_code()

        ids = []
        for msg_id in range(start_id, end_id + 1):
            ids.append(msg_id)

        special_links_db.insert_one({
            "_id": code,
            "type": "batch",
            "msg_ids": ids,
            "user_id": message.from_user.id
        })

        link = f"https://t.me/{(await client.get_me()).username}?start=batch_{code}"
        await message.reply(f"**✅ Batch Link Created**\n\n**Link:** `{link}`\n**Total Files:** {len(ids)}")

    except Exception as e:
        await message.reply(f"**Error:** {str(e)}")

async def send_batch(client, message, code):
    batch = special_links_db.find_one({"_id": code})
    if not batch:
        return await message.reply("**Batch not found**")

    await message.reply(f"**Sending {len(batch['msg_ids'])} files...**")
    for msg_id in batch["msg_ids"]:
        try:
            await client.copy_message(message.chat.id, FILE_CHANNEL, msg_id)
            await asyncio.sleep(0.5)
        except:
            continue

@app.on_message(filters.command("special") & filters.private)
async def special_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        return

    try:
        name = message.text.split(" ", 1)[1]
        code = gen_code()
        special_links_db.insert_one({
            "_id": code,
            "type": "special",
            "name": name,
            "user_id": message.from_user.id,
            "files": []
        })
        link = f"https://t.me/{(await client.get_me()).username}?start=special_{code}"
        await message.reply(f"**✅ Special Link Created**\n\n**Name:** {name}\n**Link:** `{link}`\n\nNow forward files to me to add to this collection")
    except:
        await message.reply("**Usage:** `/special MovieName`")

async def send_special(client, message, code):
    special = special_links_db.find_one({"_id": code})
    if not special:
        return await message.reply("**Collection not found**")

    if not special["files"]:
        return await message.reply(f"**{special['name']}**\n\nNo files in this collection yet")

    await message.reply(f"**{special['name']}**\n\n**Total Files:** {len(special['files'])}")
    for file_code in special["files"]:
        await send_file(client, message, file_code)
        await asyncio.sleep(0.5)

@app.on_message(filters.command("admin") & filters.private)
async def admin_panel(client, message: Message):
    if not await is_admin(message.from_user.id):
        return

    settings = await get_settings()
    text = f"**🔧 Admin Panel**\n\n**Current Settings:**\n• Auto Delete: {settings['auto_delete']} min\n• Shortener: {settings['shortener']}\n• Watermark: {settings['watermark']}\n• Force Sub: {len(settings['force_sub'])} channels"

    btns = [
        [InlineKeyboardButton("📝 Edit Start Message", callback_data="edit_start")],
        [InlineKeyboardButton("🔗 Manage Buttons", callback_data="edit_btns")],
        [InlineKeyboardButton("⏱️ Auto Delete", callback_data="edit_delete")],
        [InlineKeyboardButton("🔐 Shortener", callback_data="edit_short")],
        [InlineKeyboardButton("💧 Watermark", callback_data="edit_wm")],
        [InlineKeyboardButton("👥 Force Sub", callback_data="edit_fsub")]
    ]
    await message.reply(text, reply_markup=InlineKeyboardMarkup(btns))

@app.on_callback_query()
async def callbacks(client, query):
    data = query.data
    settings = await get_settings()

    if data == "about":
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]
        await query.message.edit_text("**About This Bot**\n\nFile store bot with batch + special links", reply_markup=InlineKeyboardMarkup(btn))

    elif data == "back_start":
        btns = [[InlineKeyboardButton(settings["main_btn_text"], url=settings["main_btn_url"])]]
        row = []
        for i, btn in enumerate(settings["small_btns"]):
            if "url" in btn:
                row.append(InlineKeyboardButton(btn["text"], url=btn["url"]))
            else:
                row.append(InlineKeyboardButton(btn["text"], callback_data=btn["callback"]))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await query.message.edit_caption(settings["start_text"], reply_markup=InlineKeyboardMarkup(btns))

    elif data == "check_sub":
        if await check_force_sub(client, query.from_user.id):
            await query.message.delete()
            await start_cmd(client, query.message)
        else:
            await query.answer("You haven't joined all channels yet!", show_alert=True)

    await query.answer()

async def shorten_url(url, settings):
    # Add your shortener API logic here
    return url

# WEB SERVER FOR RENDER
from aiohttp import web

async def health(request):
    return web.Response(text="Bot is alive!")

async def start_web():
    app_web = web.Application()
    app_web.router.add_get("/", health)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    await app.start()
    logger.info("Bot started")
    asyncio.create_task(keep_alive())
    asyncio.create_task(start_web())
    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(main())
