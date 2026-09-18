import os
import sys
import time
import asyncio
import psutil
import requests
from aiohttp import web

# --- CRITICAL FIX: MONKEY PATCH BEFORE IMPORTING PYTGCALLS ---
# Yeh patch purane Pyrogram me missing GroupcallForbidden inject kar deta hai
import pyrogram.errors
for err_name in ["GroupcallForbidden", "GroupcallInvalid", "GroupcallAlreadyDiscarded"]:
    if not hasattr(pyrogram.errors, err_name):
        setattr(pyrogram.errors, err_name, type(err_name, (Exception,), {}))

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BotCommand,
)
from pyrogram.errors import SessionPasswordNeeded
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
import yt_dlp
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "36055068"))
API_HASH = os.getenv("API_HASH", "e62c399663de4721efb786f7cfc64022")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8836214965:AAHxuAaPPWPZi-GcTcPFRMRUidPJA5z1Dsk")
OWNER_ID = int(os.getenv("OWNER_ID", "8882297263"))
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://getochammarhuyarr18_db_user:JSuaAzEfIlkVJWMO@cluster0.oakct2r.mongodb.net/?appName=Cluster0")
LOGGER_ID = int(os.getenv("LOGGER_ID", "-1004380807747"))
PORT = int(os.getenv("PORT", "10000"))

YOUTUBE_API_KEYS = [
    os.getenv("YOUTUBE_API_KEY_1", "AIzaSyA-l__eBr2z2h3V1CNCraUO3S0MWyIvgBs"),
    os.getenv("YOUTUBE_API_KEY_2", "")
]

START_TIME = time.time()

# --- DATABASE SETUP ---
mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = mongo_client["ShinobuMusicBot"]
config_collection = db["bot_config"]
session_collection = db["bot_sessions"]

# --- CLIENTS ---
bot = Client("shinobu_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = None
call_py = None
login_state = {}

default_media = {
    "start_photo": "https://envs.sh/X5z.jpg",
    "start_video": None,
    "play_photo": "https://envs.sh/X5z.jpg",
    "play_video": "https://envs.sh/X5v.mp4"
}

async def get_db_media():
    try:
        doc = await config_collection.find_one({"_id": "media_settings"})
        if doc:
            return doc.get("data", default_media)
    except Exception:
        pass
    return default_media

async def update_db_media(key, value):
    try:
        media = await get_db_media()
        media[key] = value
        await config_collection.update_one({"_id": "media_settings"}, {"$set": {"data": media}}, upsert=True)
    except Exception:
        pass

# --- SYSTEM STATS ---
def get_system_stats():
    uptime = time.strftime("%Hh %Mm %Ss", time.gmtime(time.time() - START_TIME))
    try:
        storage = f"{psutil.disk_usage('/').percent}%"
        cpu = f"{psutil.cpu_percent()}%"
        ram = f"{psutil.virtual_memory().percent}%"
    except Exception:
        storage, cpu, ram = "15%", "10%", "30%"
    return uptime, storage, cpu, ram

# --- YOUTUBE SEARCH PIPELINE ---
def search_youtube(query: str):
    for api_key in YOUTUBE_API_KEYS:
        if not api_key:
            continue
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": api_key}
        try:
            res = requests.get(url, params=params, timeout=4).json()
            items = res.get("items", [])
            if items:
                vid = items[0]["id"]["videoId"]
                return f"https://www.youtube.com/watch?v={vid}", items[0]["snippet"]["title"]
        except Exception:
            continue

    ydl_opts = {"format": "bestaudio", "quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)["entries"][0]
            return info["webpage_url"], info["title"]
        except Exception:
            return None, None

def get_audio_stream(video_url: str):
    ydl_opts = {"format": "bestaudio/best", "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        duration = info.get("duration")
        dur_str = time.strftime("%M:%S", time.gmtime(duration)) if duration else "03:45"
        return info["url"], dur_str

# --- AUTO POPUP COMMANDS MENU ---
async def set_menu_commands():
    commands = [
        BotCommand("start", "ꜱᴛᴀʀᴛꜱ ᴛʜᴇ ᴍᴜꜱɪᴄ ʙᴏᴛ."),
        BotCommand("help", "ɢᴇᴛ ʜᴇʟᴘ ᴍᴇɴᴜ ᴡɪᴛʜ ᴇxᴘʟᴀɴᴀᴛɪᴏɴ ᴏꜰ ᴄᴏᴍᴍᴀɴᴅꜱ."),
        BotCommand("ping", "ꜱʜᴏᴡꜱ ᴛʜᴇ ᴘɪɴɢ ᴀɴᴅ ꜱʏꜱᴛᴇᴍ ꜱᴛᴀᴛꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ."),
        BotCommand("play", "ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴛʀᴀᴄᴋ ᴏɴ ᴠɪᴅᴇᴏᴄʜᴀᴛ."),
        BotCommand("vplay", "ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴠɪᴅᴇᴏ ᴛʀᴀᴄᴋ ᴏɴ ᴠɪᴅᴇᴏᴄʜᴀᴛ."),
        BotCommand("pause", "ᴘᴀᴜꜱᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ."),
        BotCommand("resume", "ʀᴇꜱᴜᴍᴇ ᴛʜᴇ ᴘᴀᴜꜱᴇᴅ ꜱᴛʀᴇᴀᴍ."),
        BotCommand("skip", "ꜱᴋɪᴘ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ."),
        BotCommand("stop", "ᴄʟᴇᴀʀꜱ ᴛʜᴇ Qᴜᴇᴜᴇ ᴀɴᴅ ᴇɴᴅ ᴛʜᴇ ꜱᴛʀᴇᴀᴍ.")
    ]
    try:
        await bot.set_bot_commands(commands)
    except Exception:
        pass

# --- HELP CONTENT DICTIONARY ---
HELP_TEXTS = {
    "admin": """**ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅꜱ :**

ᴊᴜꜱᴛ ᴀᴅᴅ **ᴄ** ɪɴ ᴛʜᴇ ꜱᴛᴀʀᴛɪɴɢ ᴏꜰ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅꜱ ᴛᴏ ᴜꜱᴇ ᴛʜᴇᴍ ꜰᴏʀ ᴄʜᴀɴɴᴇʟ.

/pause : ᴘᴀᴜꜱᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ.
/resume : ʀᴇꜱᴜᴍᴇ ᴛʜᴇ ᴘᴀᴜꜱᴇᴅ ꜱᴛʀᴇᴀᴍ.
/skip : ꜱᴋɪᴘ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ ᴀɴᴅ ꜱᴛᴀʀᴛ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ɴᴇxᴛ ᴛʀᴀᴄᴋ ɪɴ Qᴜᴇᴜᴇ.
/end or /stop : ᴄʟᴇᴀʀꜱ ᴛʜᴇ Qᴜᴇᴜᴇ ᴀɴᴅ ᴇɴᴅ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ.
/player : ɢᴇᴛ ᴀ ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ᴘʟᴀʏᴇʀ ᴘᴀɴᴇʟ.
/queue : ꜱʜᴏᴡꜱ ᴛʜᴇ Qᴜᴇᴜᴇᴅ ᴛʀᴀᴄᴋꜱ ʟɪꜱᴛ.""",

    "auth": """**ᴀᴜᴛʜ ᴜꜱᴇʀꜱ :**

ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴜꜱᴇ ᴀᴅᴍɪɴ ʀɪɢʜᴛꜱ ɪɴ ᴛʜᴇ ʙᴏᴛ ᴡɪᴛʜᴏᴜᴛ ᴀᴅᴍɪɴ ʀɪɢʜᴛꜱ ɪɴ ᴛʜᴇ ᴄʜᴀᴛ.

/auth [USERNAME/USER_ID] : ᴀᴅᴅ ᴀ ᴜꜱᴇʀ ᴛᴏ ᴀᴜᴛʜ ʟɪꜱᴛ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.
/unauth [USERNAME/USER_ID] : ʀᴇᴍᴏᴠᴇ ᴀ ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ꜰʀᴏᴍ ᴛʜᴇ ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.
/authusers : ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ᴏꜰ ᴛʜᴇ ɢʀᴏᴜᴘ.""",

    "gcast": """**ʙʀᴏᴀᴅᴄᴀꜱᴛ ꜰᴇᴀᴛᴜʀᴇ [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ] :**

/broadcast [MESSAGE OR REPLY TO A MESSAGE] : ʙʀᴏᴀᴅᴄᴀꜱᴛ ᴀ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ꜱᴇʀᴠᴇᴅ ᴄʜᴀᴛꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.

**ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ᴍᴏᴅᴇꜱ :**
-pin : ᴘɪɴꜱ ʏᴏᴜʀ ʙʀᴏᴀᴅᴄᴀꜱᴛᴇᴅ ᴍᴇꜱꜱᴀɢᴇꜱ ɪɴ ꜱᴇʀᴠᴇᴅ ᴄʜᴀᴛꜱ.
-pinloud : ᴘɪɴꜱ ʏᴏᴜʀ ʙʀᴏᴀᴅᴄᴀꜱᴛᴇᴅ ᴍᴇꜱꜱᴀɢᴇ ɪɴ ꜱᴇʀᴠᴇᴅ ᴄʜᴀᴛꜱ ᴀɴᴅ ꜱᴇɴᴅ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴛᴏ ᴛʜᴇ ᴍᴇᴍʙᴇʀꜱ.
-user : ʙʀᴏᴀᴅᴄᴀꜱᴛꜱ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ᴛʜᴇ ᴜꜱᴇʀꜱ ᴡʜᴏ ʜᴀᴠᴇ ꜱᴛᴀʀᴛᴇᴅ ʏᴏᴜʀ ʙᴏᴛ.
-assistant : ʙʀᴏᴀᴅᴄᴀꜱᴛ ʏᴏᴜʀ ᴍᴇꜱꜱᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ᴀꜱꜱɪᴛᴀɴᴛ ᴀᴄᴄᴏᴜɴᴛ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.
-nobot : ꜰᴏʀᴄᴇꜱ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ɴᴏᴛ ʙʀᴏᴀᴅᴄᴀꜱᴛ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ..

**ᴇxᴀᴍᴘʟᴇ:** `/broadcast -user -assistant -pin TESTING BROADCAST`""",

    "blchat": """**ᴄʜᴀᴛ ʙʟᴀᴄᴋʟɪꜱᴛ ꜰᴇᴀᴛᴜʀᴇ : [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ]**

ʀᴇꜱᴛʀɪᴄᴛ ꜱʜɪᴛ ᴄʜᴀᴛꜱ ᴛᴏ ᴜꜱᴇ ᴏᴜʀ ᴘʀᴇᴄɪᴏᴜꜱ ʙᴏᴛ.

/blacklistchat [CHAT ID] : ʙʟᴀᴄᴋʟɪꜱᴛ ᴀ ᴄʜᴀᴛ ꜰʀᴏᴍ ᴜꜱɪɴɢ ᴛʜᴇ ʙᴏᴛ.
/whitelistchat [CHAT ID] : ᴡʜɪᴛᴇʟɪꜱᴛ ᴛʜᴇ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ ᴄʜᴀᴛ.
/blacklistedchat : ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ ᴄʜᴀᴛꜱ.""",

    "blusers": """**ʙʟᴏᴄᴋ ᴜꜱᴇʀꜱ: [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ]**

ꜱᴛᴀʀᴛꜱ ɪɢɴᴏʀɪɴɢ ᴛʜᴇ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ ᴜꜱᴇʀ, ꜱᴏ ᴛʜᴀᴛ ʜᴇ ᴄᴀɴ'ᴛ ᴜꜱᴇ ʙᴏᴛ ᴄᴏᴍᴍᴀɴᴅꜱ.

/block [USERNAME OR REPLY TO A USER] : ʙʟᴏᴄᴋ ᴛʜᴇ ᴜꜱᴇʀ ꜰʀᴏᴍ ᴏᴜʀ ʙᴏᴛ.
/unblock [USERNAME OR REPLY TO A USER] : ᴜɴʙʟᴏᴄᴋꜱ ᴛʜᴇ ʙʟᴏᴄᴋᴇᴅ ᴜꜱᴇʀ.
/blockedusers : ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ʙʟᴏᴄᴋᴇᴅ ᴜꜱᴇʀꜱ.""",

    "cplay": """**ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ᴄᴏᴍᴍᴀɴᴅꜱ:**

ʏᴏᴜ ᴄᴀɴ ꜱᴛʀᴇᴀᴍ ᴀᴜᴅɪᴏ/ᴠɪᴅᴇᴏ ɪɴ ᴄʜᴀɴɴᴇʟ.

/cplay : ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴀᴜᴅɪᴏ ᴛʀᴀᴄᴋ ᴏɴ ᴄʜᴀɴɴᴇʟ'ꜱ ᴠɪᴅᴇᴏᴄʜᴀᴛ.
/cvplay : ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴠɪᴅᴇᴏ ᴛʀᴀᴄᴋ ᴏɴ ᴄʜᴀɴɴᴇʟ'ꜱ ᴠɪᴅᴇᴏᴄʜᴀᴛ.
/cplayforce or /cvplayforce : ꜱᴛᴏᴘꜱ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ ᴀɴᴅ ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴛʀᴀᴄᴋ.

/channelplay [CHAT USERNAME OR ID] OR [DISABLE] : ᴄᴏɴɴᴇᴄᴛ ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴀ ɢʀᴏᴜᴘ ᴀɴᴅ ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʀᴀᴄᴋꜱ ʙʏ ᴛʜᴇ ʜᴇʟᴘ ᴏꜰ ᴄᴏᴍᴍᴀɴᴅꜱ ꜱᴇɴᴛ ɪɴ ɢʀᴏᴜᴘ.""",

    "gban": """**ɢʟᴏʙᴀʟ ʙᴀɴ ꜰᴇᴀᴛᴜʀᴇ [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ] :**

/gban [USERNAME OR REPLY TO A USER] : ɢʟᴏʙᴀʟʟʏ ʙᴀɴꜱ ᴛʜᴇ ᴄʜᴜᴛɪʏᴀ ꜰʀᴏᴍ ᴀʟʟ ᴛʜᴇ ꜱᴇʀᴠᴇᴅ ᴄʜᴀᴛꜱ ᴀɴᴅ ʙʟᴀᴄᴋʟɪꜱᴛ ʜɪᴍ ꜰʀᴏᴍ ᴜꜱɪɴɢ ᴛʜᴇ ʙᴏᴛ.
/ungban [USERNAME OR REPLY TO A USER] : ɢʟᴏʙᴀʟʟʏ ᴜɴʙᴀɴꜱ ᴛʜᴇ ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ ᴜꜱᴇʀ.
/gbannedusers : ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ ᴜꜱᴇʀꜱ.""",

    "loop": """**ʟᴏᴏᴘ ꜱᴛʀᴇᴀᴍ :**

ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ ɪɴ ʟᴏᴏᴘ

/loop [enable/disable] : ᴇɴᴀʙʟᴇꜱ/ᴅɪꜱᴀʙʟᴇꜱ ʟᴏᴏᴘ ꜰᴏʀ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ
/loop [1, 2, 3, ...] : ᴇɴᴀʙʟᴇꜱ ᴛʜᴇ ʟᴏᴏᴘ ꜰᴏʀ ᴛʜᴇ ɢɪᴠᴇɴ ᴠᴀʟᴜᴇ.""",

    "log": """**ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ] :**

/logs : ɢᴇᴛ ʟᴏɢꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.

/logger [ENABLE/DISABLE] : ʙᴏᴛ ᴡɪʟʟ ꜱᴛᴀʀᴛ ʟᴏɢɢɪɴɢ ᴛʜᴇ ᴀᴄᴛɪᴠɪᴛɪᴇꜱ ʜᴀᴘᴘᴇɴ ᴏɴ ʙᴏᴛ.

/maintenance [ENABLE/DISABLE] : ᴇɴᴀʙʟᴇ ᴏʀ ᴅɪꜱᴀʙʟᴇ ᴛʜᴇ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ ᴏꜰ ʏᴏᴜʀ ʙᴏᴛ.""",

    "ping": """**ᴘɪɴɢ & ꜱᴛᴀᴛꜱ :**

/start : ꜱᴛᴀʀᴛꜱ ᴛʜᴇ ᴍᴜꜱɪᴄ ʙᴏᴛ.
/help : ɢᴇᴛ ʜᴇʟᴘ ᴍᴇɴᴜ ᴡɪᴛʜ ᴇxᴘʟᴀɴᴀᴛɪᴏɴ ᴏꜰ ᴄᴏᴍᴍᴀɴᴅꜱ.

/ping : ꜱʜᴏᴡꜱ ᴛʜᴇ ᴘɪɴɢ ᴀɴᴅ ꜱʏꜱᴛᴇᴍ ꜱᴛᴀᴛꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.

/stats : ꜱʜᴏᴡꜱ ᴛʜᴇ ᴏᴠᴇʀᴀʟʟ ꜱᴛᴀᴛꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.""",

    "play": """**ᴘʟᴀʏ ᴄᴏᴍᴍᴀɴᴅꜱ :**

V : ꜱᴛᴀɴᴅꜱ ꜰᴏʀ ᴠɪᴅᴇᴏ ᴘʟᴀʏ.
force : ꜱᴛᴀɴᴅꜱ ꜰᴏʀ ꜰᴏʀᴄᴇ ᴘʟᴀʏ.

/play OR /vplay : ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴛʀᴀᴄᴋ ᴏɴ ᴠɪᴅᴇᴏᴄʜᴀᴛ.

/playforce OR /vplayforce : ꜱᴛᴏᴘꜱ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ ᴀɴᴅ ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴛʀᴀᴄᴋ.""",

    "shuffle": """**ꜱʜᴜꜰꜰʟᴇ Qᴜᴇᴜᴇ :**

/shuffle : ꜱʜᴜꜰꜰʟᴇ'ꜱ ᴛʜᴇ Qᴜᴇᴜᴇ.
/queue : ꜱʜᴏᴡꜱ ᴛʜᴇ ꜱʜᴜꜰꜰʟᴇᴅ Qᴜᴇᴜᴇ.""",

    "seek": """**ꜱᴇᴇᴋ ꜱᴛʀᴇᴀᴍ :**

/seek [DURATION IN SECONDS] : ꜱᴇᴇᴋ ᴛʜᴇ ꜱᴛʀᴇᴀᴍ ᴛᴏ ᴛʜᴇ ɢɪᴠᴇɴ ᴅᴜʀᴀᴛɪᴏɴ.
/seekback [DURATION IN SECONDS] : ʙᴀᴄᴋᴡᴀʀᴅ ꜱᴇᴇᴋ ᴛʜᴇ ꜱᴛʀᴇᴀᴍ ᴛᴏ ᴛʜᴇ ɢɪᴠᴇɴ ᴅᴜʀᴀᴛɪᴏɴ.""",

    "song": """**ꜱᴏɴɢ ᴅᴏᴡɴʟᴏᴀᴅ**

/song [SONG NAME/YT URL] : ᴅᴏᴡɴʟᴏᴀᴅ ᴀɴʏ ᴛʀᴀᴄᴋ ꜰʀᴏᴍ ʏᴏᴜᴛᴜʙᴇ ɪɴ ᴍᴘ3 ᴏʀ ᴍᴘ4 ꜰᴏʀᴍᴀᴛꜱ.""",

    "speed": """**ꜱᴘᴇᴇᴅ ᴄᴏᴍᴍᴀɴᴅꜱ :**

ʏᴏᴜ ᴄᴀɴ ᴄᴏɴᴛʀᴏʟ ᴛʜᴇ ᴘʟᴀʏʙᴀᴄᴋ ꜱᴘᴇᴇᴅ ᴏꜰ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ. [ᴀᴅᴍɪɴꜱ ᴏɴʟʏ]

/speed or /playback : ꜰᴏʀ ᴀᴅᴊᴜꜱᴛɪɴɢ ᴛʜᴇ ᴀᴜᴅɪᴏ ᴘʟᴀʏʙᴀᴄᴋ ꜱᴘᴇᴇᴅ ɪɴ ɢʀᴏᴜᴘ.
/cspeed or /cplayback : ꜰᴏʀ ᴀᴅᴊᴜꜱᴛɪɴɢ ᴛʜᴇ ᴀᴜᴅɪᴏ ᴘʟᴀʏʙᴀᴄᴋ ꜱᴘᴇᴇᴅ ɪɴ ᴄʜᴀɴɴᴇʟ."""
}

HELP_GRID_MARKUP = InlineKeyboardMarkup([
    [InlineKeyboardButton("˹ᴀᴅᴍɪɴ˼", callback_data="help_admin"), InlineKeyboardButton("˹ᴀᴜᴛʜ˼", callback_data="help_auth"), InlineKeyboardButton("˹ɢ-ᴄᴀsᴛ˼", callback_data="help_gcast")],
    [InlineKeyboardButton("˹ʙʟ-ᴄʜᴀᴛ˼", callback_data="help_blchat"), InlineKeyboardButton("˹ʙʟ-ᴜsᴇʀs˼", callback_data="help_blusers"), InlineKeyboardButton("˹ᴄ-ᴘʟᴀʏ˼", callback_data="help_cplay")],
    [InlineKeyboardButton("˹ɢ-ʙᴀɴ˼", callback_data="help_gban"), InlineKeyboardButton("˹ʟᴏᴏᴘ˼", callback_data="help_loop"), InlineKeyboardButton("˹ʟᴏɢ˼", callback_data="help_log")],
    [InlineKeyboardButton("˹ᴘɪɴɢ˼", callback_data="help_ping"), InlineKeyboardButton("˹ᴘʟᴀʏ˼", callback_data="help_play"), InlineKeyboardButton("˹sʜᴜғғʟᴇ˼", callback_data="help_shuffle")],
    [InlineKeyboardButton("˹sᴇᴇᴋ˼", callback_data="help_seek"), InlineKeyboardButton("˹sᴏɴɢ˼", callback_data="help_song"), InlineKeyboardButton("˹sᴘᴇᴇᴅ˼", callback_data="help_speed")],
    [InlineKeyboardButton("◀️", callback_data="dummy"), InlineKeyboardButton("✧ ʙᴀᴄᴋ ✧", callback_data="open_start"), InlineKeyboardButton("▶️", callback_data="dummy")]
])

BACK_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🗑 ✧ ʙᴀᴄᴋ ✧", callback_data="help_menu")]
])

# --- DYNAMIC ASSISTANT LOGIN SYSTEM ---
@bot.on_message(filters.command("addassistant") & filters.private & filters.user(OWNER_ID))
async def add_assistant_handler(_, message: Message):
    login_state[OWNER_ID] = {"step": "phone"}
    await message.reply_text("📞 **Assistant Login:**\nKripya apna phone number country code ke sath bhejein:\n(Example: `+919876543210`)")

@bot.on_message(filters.private & filters.user(OWNER_ID))
async def dynamic_login_steps(_, message: Message):
    user_id = message.from_user.id
    if user_id not in login_state or message.text.startswith("/"):
        return

    state = login_state[user_id]
    step = state.get("step")

    if step == "phone":
        phone = message.text.strip()
        temp_client = Client(":memory:", api_id=API_ID, api_hash=API_HASH)
        await temp_client.connect()
        try:
            code_hash = await temp_client.send_code(phone)
            state["client"] = temp_client
            state["phone"] = phone
            state["phone_code_hash"] = code_hash.phone_code_hash
            state["step"] = "otp"
            await message.reply_text("📩 **Telegram OTP Code Bhejein:**\nKripya code space dekar bhejein (Example: `1 2 3 4 5`).")
        except Exception as e:
            await message.reply_text(f"❌ **Failed to send code:** `{str(e)}`")
            del login_state[user_id]

    elif step == "otp":
        otp = message.text.replace(" ", "").strip()
        temp_client = state["client"]
        try:
            await temp_client.sign_in(state["phone"], state["phone_code_hash"], otp)
            ss = await temp_client.export_session_string()
            await session_collection.update_one({"_id": "assistant"}, {"$set": {"session": ss}}, upsert=True)
            await message.reply_text("✅ **Assistant Session Saved in MongoDB!** Render restart hone par assistant connect ho jayega.")
            del login_state[user_id]
        except SessionPasswordNeeded:
            state["step"] = "2fa"
            await message.reply_text("🔐 **2-Step Verification Password Bhejein:**")
        except Exception as e:
            await message.reply_text(f"❌ **OTP Error:** `{str(e)}`")
            del login_state[user_id]

    elif step == "2fa":
        pwd = message.text.strip()
        temp_client = state["client"]
        try:
            await temp_client.check_password(pwd)
            ss = await temp_client.export_session_string()
            await session_collection.update_one({"_id": "assistant"}, {"$set": {"session": ss}}, upsert=True)
            await message.reply_text("✅ **Assistant 2FA Verified & Saved in MongoDB!**")
            del login_state[user_id]
        except Exception as e:
            await message.reply_text(f"❌ **Password Error:** `{str(e)}`")
            del login_state[user_id]

# --- COMMAND: /start ---
@bot.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    uptime, storage, cpu, ram = get_system_stats()
    first = message.from_user.first_name if message.from_user else "User"
    media = await get_db_media()

    text = f"""┌────── ˹ ɪɴғᴏʀᴍᴀᴛɪᴏɴ ˼─── ⏤‌‌●
┆ ʜᴇʏ, {first} !!
┆ ɪ ᴧϻ ˹ 𝐒ʜɪɴᴏʙᴜ ꭙ 𝐌ᴜsɪᴄ ˼ ♪ 🎶 
└──────────────────────●

> ᴍᴜsɪᴄ ɪs ʟɪғᴇ  🪐

> ✨ ᴜᴘᴛɪᴍᴇ: {uptime}
> ✨ sᴇʀᴠᴇʀ sᴛᴏʀᴀɢᴇ: {storage}
> ✨ ᴄᴘᴜ ʟᴏᴀᴅ: {cpu}
> ✨ ʀᴀᴍ ᴄᴏɴsᴜᴍᴘᴛɪᴏɴ: {ram}

> ᴇɴᴊᴏʏ ᴘʀᴇᴍɪᴜᴍ ʟɪsᴛᴇɴɪɴɢ
>    ᴇxᴘᴇʀɪᴇɴᴄᴇ  ❤️‍🔥❤️‍🔥

───────────────────•
  ᴘᴏᴡєʀєᴅ » [𝜜 𝐒 𝜢 𝜤 𝐒 𝜢](https://t.me/ll_NAGUMO_ll)
───────────────────•"""

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧚🏻‍♀️  ˹ᴛᴀᴘ ᴛᴏ sᴇᴇ ᴍᴀɢɪᴄ˼ ➕", url=f"https://t.me/{bot.me.username}?startgroup=true")],
        [InlineKeyboardButton("🎵 ˹ᴀʙᴏᴜᴛ˼ ↗️", url="https://t.me/ll_NAGUMO_lll"), InlineKeyboardButton("✈️ ˹ᴀsʜɪsʜ ᴛᴜɴᴇs♪˼ ◻️", callback_data="dummy")],
        [InlineKeyboardButton("˹ɴᴇᴛᴡᴏʀᴋ˼ ↗️", url="https://t.me/+x1aoxt_OJNBmMmI1"), InlineKeyboardButton("˹ᴍʏ ʜᴏᴍᴇ˼ ↗️", url="https://t.me/Rinnegan_anime_group")],
        [InlineKeyboardButton("˹ʜᴇʟᴘ ᴀɴᴅ ᴄᴏᴍᴍᴀɴᴅs˼", callback_data="help_menu")],
        [InlineKeyboardButton("🧚🏻‍♀️  ˹ᴍʏ ᴍᴀsᴛᴇʀ˼ 👑 ↗️", url="https://t.me/ll_NAGUMO_ll")]
    ])

    if media.get("start_video"):
        await message.reply_video(media["start_video"], caption=text, reply_markup=buttons)
    else:
        await message.reply_photo(media["start_photo"], caption=text, reply_markup=buttons)

# --- MEDIA SETTERS (OWNER) ---
@bot.on_message(filters.command("setphoto") & filters.user(OWNER_ID))
async def set_start_photo(_, message: Message):
    if message.reply_to_message and message.reply_to_message.photo:
        await update_db_media("start_photo", message.reply_to_message.photo.file_id)
        await message.reply_text("✨ **Start photo updated in MongoDB!**")
    else:
        await message.reply_text("❗ **Photo ko reply karke `/setphoto` likho.**")

@bot.on_message(filters.command("setvideo") & filters.user(OWNER_ID))
async def set_start_video(_, message: Message):
    if message.reply_to_message and message.reply_to_message.video:
        await update_db_media("start_video", message.reply_to_message.video.file_id)
        await message.reply_text("✨ **Start video updated in MongoDB!**")
    else:
        await message.reply_text("❗ **Video ko reply karke `/setvideo` likho.**")

@bot.on_message(filters.command("setplayphoto") & filters.user(OWNER_ID))
async def set_play_photo(_, message: Message):
    if message.reply_to_message and message.reply_to_message.photo:
        await update_db_media("play_photo", message.reply_to_message.photo.file_id)
        await message.reply_text("✨ **Usage photo updated in MongoDB!**")
    else:
        await message.reply_text("❗ **Photo ko reply karke `/setplayphoto` likho.**")

@bot.on_message(filters.command("setplayvideo") & filters.user(OWNER_ID))
async def set_play_video(_, message: Message):
    if message.reply_to_message and message.reply_to_message.video:
        await update_db_media("play_video", message.reply_to_message.video.file_id)
        await message.reply_text("✨ **Player card video updated in MongoDB!**")
    else:
        await message.reply_text("❗ **Video ko reply karke `/setplayvideo` likho.**")

# --- COMMAND: /play ---
@bot.on_message(filters.command(["play", "vplay"]) & filters.group)
async def play_handler(_, message: Message):
    media = await get_db_media()
    if len(message.command) < 2 and not message.reply_to_message:
        usage_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("ꜱᴜᴘᴘᴏʀᴛ ↗️", url="https://t.me/ll_NAGUMO_lll"), InlineKeyboardButton("✧ ᴄʟᴏꜱᴇ ✧", callback_data="close_panel")]
        ])
        await message.reply_photo(
            media["play_photo"],
            caption="ᴜꜱᴀɢᴇ : /play [ꜱᴏɴɢ ɴᴀᴍᴇ/ʏᴏᴜᴛᴜʙᴇ ᴜʀʟ/ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴀᴜᴅɪᴏ/ᴠɪᴅᴇᴏ ꜰɪʟᴇ]",
            reply_markup=usage_buttons
        )
        return

    try:
        await message.delete()
    except Exception:
        pass

    prep_msg = await message.reply_text("> ᴘʀєᴘᴧʀɪηɢ ʏσᴜʀ ᴛʀᴧᴄᴋ... ❤️‍🔥❤️‍🔥")

    query = message.text.split(None, 1)[1] if len(message.command) >= 2 else "Audio Track"
    video_url, title = search_youtube(query)

    if not video_url:
        await prep_msg.edit("❌ **YouTube se track nahi mila!**")
        return

    try:
        stream_url, duration = get_audio_stream(video_url)
        chat_id = message.chat.id
        mention = message.from_user.mention if message.from_user else "Admin"

        if call_py:
            await call_py.play(chat_id, MediaStream(stream_url))

        await prep_msg.delete()

        caption = f""" ᴛιᴛʟє: {title}
ᴅᴜʀᴧᴛιση: {duration}
ʀєǫᴜєsᴛєᴅ: {mention}

🥂 ᴘᴏᴡєʀєᴅ» [𝗔sʜɪsʜ](https://t.me/ll_NAGUMO_ll)"""

        player_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▷", callback_data="resume_stream"),
                InlineKeyboardButton("⏸", callback_data="pause_stream"),
                InlineKeyboardButton("⏭", callback_data="skip_stream")
            ],
            [
                InlineKeyboardButton("🎵 ᴛᴜηєs ◻️", callback_data="dummy"),
                InlineKeyboardButton("🏠 ʜᴏᴍє ↗️", url="https://t.me/Rinnegan_anime_group")
            ],
            [
                InlineKeyboardButton("🗑 CLOSE ◻", callback_data="close_panel")
            ]
        ])

        if media.get("play_video"):
            await message.reply_video(media["play_video"], caption=caption, reply_markup=player_buttons, supports_streaming=True)
        else:
            await message.reply_photo(media["play_photo"], caption=caption, reply_markup=player_buttons)

    except Exception as e:
        await prep_msg.edit(f"⚠️ **Error:** `{str(e)}`")

# --- PLAYBACK CONTROLS ---
@bot.on_message(filters.command(["stop", "end"]) & filters.group)
async def stop_music(_, message: Message):
    if call_py:
        try:
            await call_py.leave_call(message.chat.id)
            await message.reply_text("⏹️ **Voice Chat se disconnect ho gaya aur stream end ho gayi.**")
        except Exception as e:
            await message.reply_text(f"⚠️ **Error:** `{str(e)}`")

@bot.on_message(filters.command("pause") & filters.group)
async def pause_music(_, message: Message):
    if call_py:
        try:
            await call_py.pause(message.chat.id)
            await message.reply_text("⏸️ **Stream pause kar di gayi.**")
        except Exception as e:
            await message.reply_text(f"⚠️ **Error:** `{str(e)}`")

@bot.on_message(filters.command("resume") & filters.group)
async def resume_music(_, message: Message):
    if call_py:
        try:
            await call_py.resume(message.chat.id)
            await message.reply_text("▶️ **Stream dobara shuru ho gayi.**")
        except Exception as e:
            await message.reply_text(f"⚠️ **Error:** `{str(e)}`")

# --- CALLBACK ROUTER ---
@bot.on_callback_query()
async def callback_handler(_, query: CallbackQuery):
    data = query.data
    media = await get_db_media()

    if data == "close_panel":
        await query.message.delete()

    elif data == "resume_stream":
        if call_py:
            try:
                await call_py.resume(query.message.chat.id)
                await query.answer("Stream Resumed! ▶️")
            except Exception:
                await query.answer("Stream active nahi hai.")
        else:
            await query.answer("Assistant active nahi hai.", show_alert=True)

    elif data == "pause_stream":
        if call_py:
            try:
                await call_py.pause(query.message.chat.id)
                await query.answer("Stream Paused! ⏸️")
            except Exception:
                await query.answer("Error pausing stream.")
        else:
            await query.answer("Assistant active nahi hai.", show_alert=True)

    elif data == "skip_stream":
        await query.answer("Track skipped! ⏭️")

    elif data == "help_menu":
        if query.message.photo or query.message.video:
            await query.message.edit_caption(caption="✨ **Shinobu Music Help Menu & Commands**", reply_markup=HELP_GRID_MARKUP)
        else:
            await query.message.edit_text("✨ **Shinobu Music Help Menu & Commands**", reply_markup=HELP_GRID_MARKUP)
        await query.answer()

    elif data.startswith("help_"):
        key = data.split("_", 1)[1]
        text = HELP_TEXTS.get(key)
        if text:
            if query.message.photo or query.message.video:
                await query.message.edit_caption(caption=text, reply_markup=BACK_BUTTON)
            else:
                await query.message.edit_text(text=text, reply_markup=BACK_BUTTON)
        await query.answer()

    elif data == "open_start":
        await query.message.delete()
        uptime, storage, cpu, ram = get_system_stats()
        first = query.from_user.first_name if query.from_user else "User"

        text = f"""┌────── ˹ ɪɴғᴏʀᴍᴀᴛɪᴏɴ ˼─── ⏤‌‌●
┆ ʜᴇʏ, {first} !!
┆ ɪ ᴧϻ ˹ 𝐒ʜɪɴᴏʙᴜ ꭙ 𝐌ᴜsɪᴄ ˼ ♪ 🎶 
└──────────────────────●

> ᴍᴜsɪᴄ ɪs ʟɪғᴇ  🪐

> ✨ ᴜᴘᴛɪᴍᴇ: {uptime}
> ✨ sᴇʀᴠᴇʀ sᴛᴏʀᴀɢᴇ: {storage}
> ✨ ᴄᴘᴜ ʟᴏᴀᴅ: {cpu}
> ✨ ʀᴀᴍ ᴄᴏɴsᴜᴍᴘᴛɪᴏɴ: {ram}

> ᴇɴᴊᴏʏ ᴘʀᴇᴍɪᴜᴍ ʟɪsᴛᴇɴɪɴɢ
>    ᴇxᴘᴇʀɪᴇɴᴄᴇ  ❤️‍🔥❤️‍🔥

───────────────────•
  ᴘᴏᴡєʀєᴅ » [𝜜 𝐒 𝜢 𝜤 𝐒 𝜢](https://t.me/ll_NAGUMO_ll)
───────────────────•"""

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧚🏻‍♀️  ˹ᴛᴀᴘ ᴛᴏ sᴇᴇ ᴍᴀɢɪᴄ˼ ➕", url=f"https://t.me/{bot.me.username}?startgroup=true")],
            [InlineKeyboardButton("🎵 ˹ᴀʙᴏᴜᴛ˼ ↗️", url="https://t.me/ll_NAGUMO_lll"), InlineKeyboardButton("✈️ ˹ᴀsʜɪsʜ ᴛᴜɴᴇs♪˼ ◻️", callback_data="dummy")],
            [InlineKeyboardButton("˹ɴᴇᴛᴡᴏʀᴋ˼ ↗️", url="https://t.me/+x1aoxt_OJNBmMmI1"), InlineKeyboardButton("˹ᴍʏ ʜᴏᴍᴇ˼ ↗️", url="https://t.me/Rinnegan_anime_group")],
            [InlineKeyboardButton("˹ʜᴇʟᴘ ᴀɴᴅ ᴄᴏᴍᴍᴀɴᴅs˼", callback_data="help_menu")],
            [InlineKeyboardButton("🧚🏻‍♀️  ˹ᴍʏ ᴍᴀsᴛᴇʀ˼ 👑 ↗️", url="https://t.me/ll_NAGUMO_ll")]
        ])

        if media.get("start_video"):
            await bot.send_video(query.message.chat.id, media["start_video"], caption=text, reply_markup=buttons)
        else:
            await bot.send_photo(query.message.chat.id, media["start_photo"], caption=text, reply_markup=buttons)
        await query.answer()

    elif data == "dummy":
        await query.answer("⚡ Shinobu x Music", show_alert=False)

# --- RENDER HEALTHCHECK SERVER ---
async def web_health_check(request):
    return web.Response(text="Shinobu x Music Bot is Alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", web_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f">> Web Healthcheck Server started on Port {PORT}")

# --- BOT RUNNER & LOGGER ALERT ---
async def main():
    global user, call_py

    await start_web_server()
    await bot.start()
    bot_info = await bot.get_me()
    print(f">> Bot Started Successfully as @{bot_info.username}")
    await set_menu_commands()

    # Load Assistant Session from MongoDB
    try:
        sess_doc = await session_collection.find_one({"_id": "assistant"})
        if sess_doc and sess_doc.get("session"):
            user = Client("shinobu_assistant", api_id=API_ID, api_hash=API_HASH, session_string=sess_doc["session"])
            await user.start()
            call_py = PyTgCalls(user)
            await call_py.start()
            print(">> Assistant Client and PyTgCalls Started!")
    except Exception as e:
        print(f">> Assistant Start Warning: {e}")

    # Send Logger Alert to your channel (-1004380807747)
    if LOGGER_ID:
        try:
            uptime, storage, cpu, ram = get_system_stats()
            logger_text = f"""🚀 **ꜱʜɪɴᴏʙᴜ x ᴍᴜꜱɪᴄ ʙᴏᴛ ᴏɴʟɪɴᴇ!**

🤖 **ʙᴏᴛ ɴᴀᴍᴇ:** @{bot_info.username}
🆔 **ʙᴏᴛ ɪᴅ:** `{bot_info.id}`
👑 **ᴏᴡɴᴇʀ ɪᴅ:** `{OWNER_ID}`
✨ **ᴜᴘᴛɪᴍᴇ:** `{uptime}`
⚡ **ᴄᴘᴜ ʟᴏᴀᴅ:** `{cpu}`
💾 **ʀᴀᴍ ᴜꜱᴀɢᴇ:** `{ram}`
🗄️ **ᴍᴏɴɢᴏᴅʙ:** `Connected Successfully`
🌐 **ʀᴇɴᴅᴇʀ ᴘᴏʀᴛ:** `{PORT}`
"""
            await bot.send_message(LOGGER_ID, logger_text)
            print(">> Logger message sent!")
        except Exception as ex:
            print(f"Logger Alert Failed: {ex}")

    print(">> Shinobu x Music Bot Successfully Online on Render!")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
