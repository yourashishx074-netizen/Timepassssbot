import os
import sys
import time
import asyncio
import psutil
import requests
import aiohttp
from aiohttp import web

# --- CRITICAL FIX: MONKEY PATCH BEFORE IMPORTING PYTGCALLS ---
# Yeh patch purane Pyrogram me missing GroupcallForbidden inject kar deta hai
import pyrogram.errors

for err_name in [
    "GroupcallForbidden",
    "GroupcallInvalid",
    "GroupcallAlreadyDiscarded"
]:
    if not hasattr(pyrogram.errors, err_name):
        setattr(
            pyrogram.errors,
            err_name,
            type(err_name, (Exception,), {})
        )

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


# ============================================================
# CONFIGURATION
# ============================================================

API_ID = int(os.getenv("API_ID", "YOUR_API_ID"))
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")

OWNER_ID = int(os.getenv("OWNER_ID", "YOUR_OWNER_ID"))

MONGO_URL = os.getenv(
    "MONGO_URL",
    "YOUR_MONGODB_URL"
)

LOGGER_ID = int(
    os.getenv(
        "LOGGER_ID",
        "0"
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
)


# ============================================================
# YOUTUBE API
# ============================================================

YOUTUBE_API_KEYS = [
    os.getenv("YOUTUBE_API_KEY_1", ""),
    os.getenv("YOUTUBE_API_KEY_2", "")
]


# ============================================================
# JIOSAAVN API
# ============================================================

# IMPORTANT:
# Render par 127.0.0.1:5100 tabhi chalega jab JioSaavn API
# isi Render service/container me chal rahi ho.
#
# Agar JioSaavn API alag Render service par deploy hai,
# Render Environment Variable me:
#
# JIOSAAVN_API_URL=https://your-jiosaavn-api.onrender.com
#
# set karo.

JIOSAAVN_API_URL = os.getenv(
    "JIOSAAVN_API_URL",
    "http://127.0.0.1:5100"
).rstrip("/")


START_TIME = time.time()


# ============================================================
# DATABASE SETUP
# ============================================================

mongo_client = AsyncIOMotorClient(
    MONGO_URL,
    serverSelectionTimeoutMS=5000
)

db = mongo_client["ShinobuMusicBot"]

config_collection = db["bot_config"]
session_collection = db["bot_sessions"]


# ============================================================
# CLIENTS
# ============================================================

bot = Client(
    "shinobu_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

user = None
call_py = None

login_state = {}


# ============================================================
# DEFAULT MEDIA
# ============================================================

default_media = {
    "start_photo": "https://telegra.ph/file/1ec1a95e0c8b66804a37b.jpg",
    "start_video": None,
    "play_photo": "https://telegra.ph/file/1ec1a95e0c8b66804a37b.jpg",
    "play_video": None
}


# ============================================================
# DATABASE MEDIA FUNCTIONS
# ============================================================

async def get_db_media():
    try:
        doc = await config_collection.find_one(
            {"_id": "media_settings"}
        )

        if doc and doc.get("data"):
            return doc.get("data")

    except Exception:
        pass

    return default_media


async def update_db_media(key, value):
    try:
        media = await get_db_media()

        media[key] = value

        await config_collection.update_one(
            {"_id": "media_settings"},
            {"$set": {"data": media}},
            upsert=True
        )

    except Exception:
        pass


# ============================================================
# SYSTEM STATS
# ============================================================

def get_system_stats():
    uptime = time.strftime(
        "%Hh %Mm %Ss",
        time.gmtime(time.time() - START_TIME)
    )

    try:
        storage = f"{psutil.disk_usage('/').percent}%"
        cpu = f"{psutil.cpu_percent()}%"
        ram = f"{psutil.virtual_memory().percent}%"

    except Exception:
        storage = "15%"
        cpu = "10%"
        ram = "30%"

    return uptime, storage, cpu, ram


# ============================================================
# JIOSAAVN SEARCH
# ============================================================

async def search_jiosaavn(query: str):
    """
    JioSaavn ko primary source ke taur par use karta hai.

    Expected API:
        /result/?query=<song>

    API response list/dict dono handle kiye ja rahe hain.
    """

    try:
        endpoint = f"{JIOSAAVN_API_URL}/result/"

        timeout = aiohttp.ClientTimeout(
            total=20
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                endpoint,
                params={"query": query}
            ) as response:

                if response.status != 200:
                    print(
                        f">> JioSaavn HTTP Error: "
                        f"{response.status}"
                    )
                    return None

                data = await response.json(
                    content_type=None
                )

        if not data:
            return None

        # ----------------------------------------
        # RESPONSE NORMALIZATION
        # ----------------------------------------

        songs = []

        if isinstance(data, list):
            songs = data

        elif isinstance(data, dict):

            if isinstance(
                data.get("data"),
                list
            ):
                songs = data["data"]

            elif isinstance(
                data.get("results"),
                list
            ):
                songs = data["results"]

            elif isinstance(
                data.get("songs"),
                list
            ):
                songs = data["songs"]

            elif data.get("url"):
                songs = [data]

        # ----------------------------------------
        # FIND STREAM URL
        # ----------------------------------------

        for song in songs:

            if not isinstance(song, dict):
                continue

            audio_url = (
                song.get("url")
                or song.get("downloadUrl")
                or song.get("download_url")
            )

            if not audio_url:
                continue

            # Some APIs return downloadUrl as a list
            if isinstance(audio_url, list):

                selected_url = None

                for item in reversed(audio_url):

                    if isinstance(item, dict):
                        selected_url = (
                            item.get("url")
                            or item.get("link")
                        )

                    elif isinstance(item, str):
                        selected_url = item

                    if selected_url:
                        break

                audio_url = selected_url

            if not audio_url:
                continue

            title = (
                song.get("title")
                or song.get("song")
                or song.get("name")
                or query
            )

            singers = (
                song.get("singers")
                or song.get("artist")
                or song.get("artists")
                or ""
            )

            if isinstance(singers, list):
                singers = ", ".join(
                    str(x)
                    for x in singers
                )

            if singers:
                title = (
                    f"{title} • {singers}"
                )

            duration = (
                song.get("duration")
                or song.get("duration_sec")
                or song.get("durationSeconds")
            )

            duration_str = "Unknown"

            try:
                duration = int(
                    float(duration)
                )

                if duration > 0:
                    duration_str = time.strftime(
                        "%M:%S",
                        time.gmtime(duration)
                    )

            except (
                TypeError,
                ValueError
            ):
                pass

            return {
                "title": title,
                "stream_url": audio_url,
                "duration": duration_str,
                "source": "JioSaavn"
            }

        return None

    except Exception as e:

        print(
            f">> JioSaavn Error: {e}"
        )

        return None


# ============================================================
# YOUTUBE SEARCH PIPELINE
# ============================================================

def search_youtube(query: str):

    # ----------------------------------------
    # YOUTUBE DATA API
    # ----------------------------------------

    for api_key in YOUTUBE_API_KEYS:

        if not api_key:
            continue

        url = (
            "https://www.googleapis.com/"
            "youtube/v3/search"
        )

        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 1,
            "key": api_key
        }

        try:

            response = requests.get(
                url,
                params=params,
                timeout=6
            )

            res = response.json()

            items = res.get(
                "items",
                []
            )

            if items:

                vid = (
                    items[0]
                    ["id"]
                    ["videoId"]
                )

                title = (
                    items[0]
                    ["snippet"]
                    ["title"]
                )

                return (
                    f"https://www.youtube.com/watch?v={vid}",
                    title
                )

        except Exception as e:

            print(
                f">> YouTube API Error: {e}"
            )

            continue

    # ----------------------------------------
    # YT-DLP FALLBACK
    # ----------------------------------------

    ydl_opts = {
        "format": "bestaudio",
        "quiet": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "android",
                    "ios"
                ]
            }
        }
    }

    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                f"ytsearch:{query}",
                download=False
            )

            entries = info.get(
                "entries",
                []
            )

            if not entries:
                return None, None

            entry = entries[0]

            return (
                entry.get(
                    "webpage_url"
                ),
                entry.get(
                    "title",
                    query
                )
            )

    except Exception as e:

        print(
            f">> yt-dlp Search Error: {e}"
        )

        return None, None


# ============================================================
# YOUTUBE AUDIO STREAM
# ============================================================

def get_audio_stream(video_url: str):

    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "android",
                    "ios"
                ]
            }
        }
    }

    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        info = ydl.extract_info(
            video_url,
            download=False
        )

        duration = info.get(
            "duration"
        )

        if duration:

            dur_str = time.strftime(
                "%M:%S",
                time.gmtime(duration)
            )

        else:
            dur_str = "Unknown"

        return (
            info["url"],
            dur_str
        )


# ============================================================
# AUTO POPUP COMMANDS MENU
# ============================================================

async def set_menu_commands():

    commands = [

        BotCommand(
            "start",
            "ꜱᴛᴀʀᴛꜱ ᴛʜᴇ ᴍᴜꜱɪᴄ ʙᴏᴛ."
        ),

        BotCommand(
            "help",
            "ɢᴇᴛ ʜᴇʟᴘ ᴍᴇɴᴜ ᴡɪᴛʜ ᴇxᴘʟᴀɴᴀᴛɪᴏɴ ᴏꜰ ᴄᴏᴍᴍᴀɴᴅꜱ."
        ),

        BotCommand(
            "ping",
            "ꜱʜᴏᴡꜱ ᴛʜᴇ ᴘɪɴɢ ᴀɴᴅ ꜱʏꜱᴛᴇᴍ ꜱᴛᴀᴛꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ."
        ),

        BotCommand(
            "play",
            "ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴛʀᴀᴄᴋ ᴏɴ ᴠɪᴅᴇᴏᴄʜᴀᴛ."
        ),

        BotCommand(
            "vplay",
            "ꜱᴛᴀʀᴛꜱ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴠɪᴅᴇᴏ ᴛʀᴀᴄᴋ ᴏɴ ᴠɪᴅᴇᴏᴄʜᴀᴛ."
        ),

        BotCommand(
            "pause",
            "ᴘᴀᴜꜱᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ."
        ),

        BotCommand(
            "resume",
            "ʀᴇꜱᴜᴍᴇ ᴛʜᴇ ᴘᴀᴜꜱᴇᴅ ꜱᴛʀᴇᴀᴍ."
        ),

        BotCommand(
            "skip",
            "ꜱᴋɪᴘ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ."
        ),

        BotCommand(
            "stop",
            "ᴄʟᴇᴀʀꜱ ᴛʜᴇ Qᴜᴇᴜᴇ ᴀɴᴅ ᴇɴᴅ ᴛʜᴇ ꜱᴛʀᴇᴀᴍ"
        )
    ]

    try:
        await bot.set_bot_commands(
            commands
        )

    except Exception:
        pass


# ============================================================
# HELP CONTENT
# ============================================================

HELP_TEXTS = {
    "admin": """**ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅꜱ :**

ᴊᴜꜱᴛ ᴀᴅᴅ **ᴄ** ɪɴ ᴛʜᴇ ꜱᴛᴀʀᴛɪɴɢ ᴏꜰ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅꜱ ᴛᴏ ᴜꜱᴇ ᴛʜᴇᴍ ꜰᴏʀ ᴄʜᴀɴɴᴇʟ.

/pause : ᴘᴀᴜꜱᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ.
/resume : ʀᴇꜱᴜᴍᴇ ᴛʜᴇ ᴘᴀᴜꜱᴇᴅ ꜱᴛʀᴇᴀᴍ.
/skip : ꜱᴋɪᴘ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ ᴀɴᴅ ꜱᴛᴀʀᴛ ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ɴᴇxᴛ ᴛʀᴀᴄᴋ ɪɴ Qᴜᴇᴜᴇ.
/end or /stop : ᴄʟᴇᴀʀꜱ ᴛʜᴇ Qᴜᴇᴜᴇ ᴀɴᴅ ᴇɴᴅ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏɪɴɢ ꜱᴛʀᴇᴀᴍ.
/player : ɢᴇᴛ ᴀɴ ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ᴘʟᴀʏᴇʀ ᴘᴀɴᴇʟ.
/queue : ꜱʜᴏᴡꜱ ᴛʜᴇ Qᴜᴇᴜᴇᴅ ᴛʀᴀᴄᴋꜱ ʟɪꜱᴛ.""",

    "auth": """**ᴀᴜᴛʜ ᴜꜱᴇʀꜱ :**

ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴜꜱᴇ ᴀᴅᴍɪɴ ʀɪɢʜᴛꜱ ɪɴ ᴛʜᴇ ʙᴏᴛ ᴡɪᴛʜᴏᴜᴛ ᴀᴅᴍɪɴ ʀɪɢʜᴛꜱ ɪɴ ᴛʜᴇ ᴄʜᴀᴛ.

/auth [USERNAME/USER_ID] : ᴀᴅᴅ ᴀ ᴜꜱᴇʀ ᴛᴏ ᴀᴜᴛʜ ʟɪꜱᴛ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.
/unauth [USERNAME/USER_ID] : ʀᴇᴍᴏᴠᴇ ᴀ ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ꜰʀᴏᴍ ᴛʜᴇ ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.
/authusers : ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ᴀᴜᴛʜ ᴜꜱᴇʀꜱ ᴏꜰ ᴛʜᴇ ɢʀᴏᴜᴘ.""",

    "gcast": """**ʙʀᴏᴀᴅᴄᴀꜱᴛ ꜰᴇᴀᴛᴜʀᴇ [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ] :**

/broadcast [MESSAGE OR REPLY TO A MESSAGE] : ʙʀᴏᴀᴅᴄᴀꜱᴛ ᴀ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ꜱᴇʀᴠᴇᴅ ᴄʜᴀᴛꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.""",

    "blchat": """**ᴄʜᴀᴛ ʙʟᴀᴄᴋʟɪꜱᴛ ꜰᴇᴀᴛᴜʀᴇ : [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ]**

ʀᴇꜱᴛʀɪᴄᴛ ꜱʜɪᴛ ᴄʜᴀᴛꜱ ᴛᴏ ᴜꜱᴇ ᴏᴜʀ ᴘʀᴇᴄɪᴏᴜꜱ ʙᴏᴛ.

/blacklistchat [CHAT ID]
/whitelistchat [CHAT ID]
/blacklistedchat""",

    "blusers": """**ʙʟᴏᴄᴋ ᴜꜱᴇʀꜱ: [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ]**

/block [USERNAME OR REPLY TO A USER]
/unblock [USERNAME OR REPLY TO A USER]
/blockedusers""",

    "cplay": """**ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ᴄᴏᴍᴍᴀɴᴅꜱ:**

/cplay : ꜱᴛᴀʀᴛs ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴀᴜᴅɪᴏ ᴛʀᴀᴄᴋ ᴏɴ ᴄʜᴀɴɴᴇʟ'ꜱ ᴠɪᴅᴇᴏᴄʜᴀᴛ.
/cvplay : ꜱᴛᴀʀᴛs ꜱᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇꜱᴛᴇᴅ ᴠɪᴅᴇᴏ ᴛʀᴀᴄᴋ ᴏɴ ᴄʜᴀɴɴᴇʟ'ꜱ ᴠɪᴅᴇᴏᴄʜᴀᴛ.""",

    "gban": """**ɢʟᴏʙᴀʟ ʙᴀɴ ꜰᴇᴀᴛᴜʀᴇ [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ] :**

/gban [USERNAME OR REPLY TO A USER]
/ungban [USERNAME OR REPLY TO A USER]
/gbannedusers""",

    "loop": """**ʟᴏᴏᴘ ꜱᴛʀᴇᴀᴍ :**

/loop [enable/disable]
/loop [1, 2, 3, ...]""",

    "log": """**ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ [ᴏɴʟʏ ꜰᴏʀ ꜱᴜᴅᴏᴇʀꜱ] :**

/logs
/logger [ENABLE/DISABLE]
/maintenance [ENABLE/DISABLE]""",

    "ping": """**ᴘɪɴɢ & ꜱᴛᴀᴛs :**

/start
/help
/ping
/stats""",

    "play": """**ᴘʟᴀʏ ᴄᴏᴍᴍᴀɴᴅs :**

V : sᴛᴀɴᴅs ꜰᴏʀ ᴠɪᴅᴇᴏ ᴘʟᴀʏ.
force : sᴛᴀɴᴅs ꜰᴏʀ ꜰᴏʀᴄᴇ ᴘʟᴀʏ.

/play OR /vplay : sᴛᴀʀᴛs sᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇsᴛᴇᴅ ᴛʀᴀᴄᴋ ᴏɴ ᴠɪᴅᴇᴏᴄʜᴀᴛ.

/playforce OR /vplayforce : sᴛᴏᴘs ᴛʜᴇ ᴏɴɢᴏɪɴɢ sᴛʀᴇᴀᴍ ᴀɴᴅ sᴛᴀʀᴛs sᴛʀᴇᴀᴍɪɴɢ ᴛʜᴇ ʀᴇQᴜᴇsᴛᴇᴅ ᴛʀᴀᴄᴋ.""",

    "shuffle": """**ꜱʜᴜꜰꜰʟᴇ Qᴜᴇᴜᴇ :**

/shuffle
/queue""",

    "seek": """**ꜱᴇᴇᴋ ꜱᴛʀᴇᴀᴍ :**

/seek [DURATION IN SECONDS]
/seekback [DURATION IN SECONDS]""",

    "song": """**ꜱᴏɴɢ ᴅᴏᴡɴʟᴏᴀᴅ**

/song [SONG NAME/YT URL] : ᴅᴏᴡɴʟᴏᴀᴅ ᴀɴʏ ᴛʀᴀᴄᴋ ꜰʀᴏᴍ ʏᴏᴜᴛᴜʙᴇ ɪɴ ᴍᴘ3 ᴏʀ ᴍᴘ4 ꜰᴏʀᴍᴀᴛs.""",

    "speed": """**ꜱᴘᴇᴇᴅ ᴄᴏᴍᴍᴀɴᴅs :**

/speed or /playback
/cspeed or /cplayback"""
}


# ============================================================
# HELP BUTTONS
# ============================================================

HELP_GRID_MARKUP = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "˹ᴀᴅᴍɪɴ˼",
            callback_data="help_admin"
        ),
        InlineKeyboardButton(
            "˹ᴀᴜᴛʜ˼",
            callback_data="help_auth"
        ),
        InlineKeyboardButton(
            "˹ɢ-ᴄᴀsᴛ˼",
            callback_data="help_gcast"
        )
    ],
    [
        InlineKeyboardButton(
            "˹ʙʟ-ᴄʜᴀᴛ˼",
            callback_data="help_blchat"
        ),
        InlineKeyboardButton(
            "˹ʙʟ-ᴜsᴇʀs˼",
            callback_data="help_blusers"
        ),
        InlineKeyboardButton(
            "˹ᴄ-ᴘʟᴀʏ˼",
            callback_data="help_cplay"
        )
    ],
    [
        InlineKeyboardButton(
            "˹ɢ-ʙᴀɴ˼",
            callback_data="help_gban"
        ),
        InlineKeyboardButton(
            "˹ʟᴏᴏᴘ˼",
            callback_data="help_loop"
        ),
        InlineKeyboardButton(
            "˹ʟᴏɢ˼",
            callback_data="help_log"
        )
    ],
    [
        InlineKeyboardButton(
            "˹ᴘɪɴɢ˼",
            callback_data="help_ping"
        ),
        InlineKeyboardButton(
            "˹ᴘʟᴀʏ˼",
            callback_data="help_play"
        ),
        InlineKeyboardButton(
            "˹sʜᴜғғʟᴇ˼",
            callback_data="help_shuffle"
        )
    ],
    [
        InlineKeyboardButton(
            "˹sᴇᴇᴋ˼",
            callback_data="help_seek"
        ),
        InlineKeyboardButton(
            "˹sᴏɴɢ˼",
            callback_data="help_song"
        ),
        InlineKeyboardButton(
            "˹sᴘᴇᴇᴅ˼",
            callback_data="help_speed"
        )
    ],
    [
        InlineKeyboardButton(
            "◀️",
            callback_data="dummy"
        ),
        InlineKeyboardButton(
            "✧ ʙᴀᴄᴋ ✧",
            callback_data="open_start"
        ),
        InlineKeyboardButton(
            "▶️",
            callback_data="dummy"
        )
    ]
])


BACK_BUTTON = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "🗑 ✧ ʙᴀᴄᴋ ✧",
            callback_data="help_menu"
        )
    ]
])


# ============================================================
# DYNAMIC ASSISTANT LOGIN SYSTEM
# ============================================================

@bot.on_message(
    filters.command("addassistant")
    & filters.private
    & filters.user(OWNER_ID)
)
async def add_assistant_handler(_, message: Message):

    login_state[OWNER_ID] = {
        "step": "phone"
    }

    await message.reply_text(
        "📞 **Assistant Login:**\n"
        "Kripya apna phone number country code ke sath bhejein:\n"
        "(Example: `+919876543210`)"
    )


@bot.on_message(
    filters.private
    & filters.user(OWNER_ID)
)
async def dynamic_login_steps(_, message: Message):

    user_id = message.from_user.id

    if (
        user_id not in login_state
        or not message.text
        or message.text.startswith("/")
    ):
        message.continue_propagation()
        return

    state = login_state[user_id]
    step = state.get("step")

    # ----------------------------------------
    # PHONE
    # ----------------------------------------

    if step == "phone":

        phone = message.text.strip()

        temp_client = Client(
            ":memory:",
            api_id=API_ID,
            api_hash=API_HASH
        )

        await temp_client.connect()

        try:

            code_hash = await temp_client.send_code(
                phone
            )

            state["client"] = temp_client
            state["phone"] = phone
            state["phone_code_hash"] = (
                code_hash.phone_code_hash
            )
            state["step"] = "otp"

            await message.reply_text(
                "📩 **Telegram OTP Code Bhejein:**\n"
                "Kripya code space dekar bhejein "
                "(Example: `1 2 3 4 5`)."
            )

        except Exception as e:

            await message.reply_text(
                f"❌ **Failed to send code:** `{str(e)}`"
            )

            try:
                await temp_client.disconnect()
            except Exception:
                pass

            login_state.pop(
                user_id,
                None
            )

    # ----------------------------------------
    # OTP
    # ----------------------------------------

    elif step == "otp":

        otp = (
            message.text
            .replace(" ", "")
            .strip()
        )

        temp_client = state["client"]

        try:

            await temp_client.sign_in(
                state["phone"],
                state["phone_code_hash"],
                otp
            )

            session_string = (
                await temp_client.export_session_string()
            )

            await session_collection.update_one(
                {"_id": "assistant"},
                {
                    "$set": {
                        "session": session_string
                    }
                },
                upsert=True
            )

            await message.reply_text(
                "✅ **Assistant Session Saved in MongoDB!**\n"
                "Render restart hone par assistant connect ho jayega."
            )

            try:
                await temp_client.disconnect()
            except Exception:
                pass

            login_state.pop(
                user_id,
                None
            )

        except SessionPasswordNeeded:

            state["step"] = "2fa"

            await message.reply_text(
                "🔐 **2-Step Verification Password Bhejein:**"
            )

        except Exception as e:

            await message.reply_text(
                f"❌ **OTP Error:** `{str(e)}`"
            )

            try:
                await temp_client.disconnect()
            except Exception:
                pass

            login_state.pop(
                user_id,
                None
            )

    # ----------------------------------------
    # 2FA
    # ----------------------------------------

    elif step == "2fa":

        pwd = message.text.strip()

        temp_client = state["client"]

        try:

            await temp_client.check_password(
                pwd
            )

            session_string = (
                await temp_client.export_session_string()
            )

            await session_collection.update_one(
                {"_id": "assistant"},
                {
                    "$set": {
                        "session": session_string
                    }
                },
                upsert=True
            )

            await message.reply_text(
                "✅ **Assistant 2FA Verified & Saved in MongoDB!**"
            )

            try:
                await temp_client.disconnect()
            except Exception:
                pass

            login_state.pop(
                user_id,
                None
            )

        except Exception as e:

            await message.reply_text(
                f"❌ **Password Error:** `{str(e)}`"
            )

            try:
                await temp_client.disconnect()
            except Exception:
                pass

            login_state.pop(
                user_id,
                None
            )


# ============================================================
# START MESSAGE
# ============================================================

async def send_start_message(
    client,
    message_or_chat_id,
    first
):

    uptime, storage, cpu, ram = (
        get_system_stats()
    )

    bot_username = (
        client.me.username
        if client.me
        else (await client.get_me()).username
    )

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
        [
            InlineKeyboardButton(
                "🧚🏻‍♀️  ˹ᴛᴀᴘ ᴛᴏ sᴇᴇ ᴍᴀɢɪᴄ˼ ➕",
                url=(
                    f"https://t.me/"
                    f"{bot_username}?startgroup=true"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "🎵 ˹ᴀʙᴏᴜᴛ˼ ↗️",
                url="https://t.me/ll_NAGUMO_lll"
            ),
            InlineKeyboardButton(
                "✈️ ˹ᴀsʜɪsʜ ᴛᴜɴᴇs♪˼ ◻️",
                callback_data="dummy"
            )
        ],
        [
            InlineKeyboardButton(
                "˹ɴᴇᴛᴡᴏʀᴋ˼ ↗️",
                url="https://t.me/+x1aoxt_OJNBmMmI1"
            ),
            InlineKeyboardButton(
                "˹ᴍʏ ʜᴏᴍє˼ ↗️",
                url="https://t.me/Rinnegan_anime_group"
            )
        ],
        [
            InlineKeyboardButton(
                "˹ʜᴇʟᴘ ᴀɴᴅ ᴄᴏᴍᴍᴀɴᴅs˼",
                callback_data="help_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "🧚🏻‍♀️  ˹ᴍʏ ᴍᴀsᴛᴇʀ˼ 👑 ↗️",
                url="https://t.me/ll_NAGUMO_ll"
            )
        ]
    ])

    media = await get_db_media()

    try:

        if isinstance(
            message_or_chat_id,
            Message
        ):

            message = message_or_chat_id

            if media.get("start_video"):

                await message.reply_video(
                    media["start_video"],
                    caption=text,
                    reply_markup=buttons
                )

            elif media.get("start_photo"):

                await message.reply_photo(
                    media["start_photo"],
                    caption=text,
                    reply_markup=buttons
                )

            else:

                await message.reply_text(
                    text,
                    reply_markup=buttons,
                    disable_web_page_preview=True
                )

        else:

            chat_id = message_or_chat_id

            if media.get("start_video"):

                await bot.send_video(
                    chat_id,
                    media["start_video"],
                    caption=text,
                    reply_markup=buttons
                )

            elif media.get("start_photo"):

                await bot.send_photo(
                    chat_id,
                    media["start_photo"],
                    caption=text,
                    reply_markup=buttons
                )

            else:

                await bot.send_message(
                    chat_id,
                    text,
                    reply_markup=buttons,
                    disable_web_page_preview=True
                )

    except Exception as e:

        print(
            f"Start media error: {e}"
        )

        if isinstance(
            message_or_chat_id,
            Message
        ):

            await message_or_chat_id.reply_text(
                text,
                reply_markup=buttons,
                disable_web_page_preview=True
            )


@bot.on_message(
    filters.command("start")
)
async def start_cmd(
    client: Client,
    message: Message
):

    first = (
        message.from_user.first_name
        if message.from_user
        else "User"
    )

    await send_start_message(
        client,
        message,
        first
    )


# ============================================================
# MEDIA SETTERS
# ============================================================

@bot.on_message(
    filters.command("setphoto")
    & filters.user(OWNER_ID)
)
async def set_start_photo(
    _,
    message: Message
):

    if (
        message.reply_to_message
        and message.reply_to_message.photo
    ):

        await update_db_media(
            "start_photo",
            message.reply_to_message.photo.file_id
        )

        await message.reply_text(
            "✨ **Start photo updated in MongoDB!**"
        )

    else:

        await message.reply_text(
            "❗ **Photo ko reply karke `/setphoto` likho.**"
        )


@bot.on_message(
    filters.command("setvideo")
    & filters.user(OWNER_ID)
)
async def set_start_video(
    _,
    message: Message
):

    if (
        message.reply_to_message
        and message.reply_to_message.video
    ):

        await update_db_media(
            "start_video",
            message.reply_to_message.video.file_id
        )

        await message.reply_text(
            "✨ **Start video updated in MongoDB!**"
        )

    else:

        await message.reply_text(
            "❗ **Video ko reply karke `/setvideo` likho.**"
        )


@bot.on_message(
    filters.command("setplayphoto")
    & filters.user(OWNER_ID)
)
async def set_play_photo(
    _,
    message: Message
):

    if (
        message.reply_to_message
        and message.reply_to_message.photo
    ):

        await update_db_media(
            "play_photo",
            message.reply_to_message.photo.file_id
        )

        await message.reply_text(
            "✨ **Usage photo updated in MongoDB!**"
        )

    else:

        await message.reply_text(
            "❗ **Photo ko reply karke `/setplayphoto` likho.**"
        )


@bot.on_message(
    filters.command("setplayvideo")
    & filters.user(OWNER_ID)
)
async def set_play_video(
    _,
    message: Message
):

    if (
        message.reply_to_message
        and message.reply_to_message.video
    ):

        await update_db_media(
            "play_video",
            message.reply_to_message.video.file_id
        )

        await message.reply_text(
            "✨ **Player card video updated in MongoDB!**"
        )

    else:

        await message.reply_text(
            "❗ **Video ko reply karke `/setplayvideo` likho.**"
        )


# ============================================================
# PLAY COMMAND
# JIOSAAVN FIRST -> YOUTUBE FALLBACK
# ============================================================

@bot.on_message(
    filters.command(
        ["play", "vplay"]
    )
    & filters.group
)
async def play_handler(
    _,
    message: Message
):

    media = await get_db_media()

    # ----------------------------------------
    # USAGE
    # ----------------------------------------

    if (
        len(message.command) < 2
        and not message.reply_to_message
    ):

        usage_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "ꜱᴜᴘᴘᴏʀᴛ ↗️",
                    url="https://t.me/ll_NAGUMO_lll"
                ),
                InlineKeyboardButton(
                    "✧ ᴄʟᴏsᴇ ✧",
                    callback_data="close_panel"
                )
            ]
        ])

        usage_text = (
            "ᴜꜱᴀɢᴇ : /play "
            "[ꜱᴏɴɢ ɴᴀᴍᴇ/ʏᴏᴜᴛᴜʙᴇ ᴜʀʟ/"
            "ʀᴇᴘʟʏ ᴛᴏ ᴀᴜᴅɪᴏ/ᴠɪᴅᴇᴏ]"
        )

        if media.get("play_photo"):

            await message.reply_photo(
                media["play_photo"],
                caption=usage_text,
                reply_markup=usage_buttons
            )

        else:

            await message.reply_text(
                usage_text,
                reply_markup=usage_buttons
            )

        return

    # ----------------------------------------
    # DELETE COMMAND
    # ----------------------------------------

    try:
        await message.delete()
    except Exception:
        pass

    # ----------------------------------------
    # PREPARING
    # ----------------------------------------

    prep_msg = await message.reply_text(
        "> ᴘʀєᴘᴧʀɪηɢ ʏσᴜʀ ᴛʀᴧᴄᴋ... ❤️‍🔥❤️‍🔥"
    )

    # ----------------------------------------
    # QUERY
    # ----------------------------------------

    if len(message.command) >= 2:

        query = message.text.split(
            None,
            1
        )[1].strip()

    else:

        query = "Audio Track"

    # ----------------------------------------
    # CHECK PYTG CALLS
    # ----------------------------------------

    if not call_py:

        await prep_msg.edit(
            "⚠️ **Assistant/PyTgCalls active nahi hai.**"
        )

        return

    # ========================================================
    # 1. JIOSAAVN FIRST
    # ========================================================

    jiosaavn_result = await search_jiosaavn(
        query
    )

    stream_url = None
    title = None
    duration = "Unknown"
    source = "JioSaavn"

    if jiosaavn_result:

        stream_url = jiosaavn_result.get(
            "stream_url"
        )

        title = jiosaavn_result.get(
            "title",
            query
        )

        duration = jiosaavn_result.get(
            "duration",
            "Unknown"
        )

        source = "JioSaavn"

        print(
            f">> JioSaavn track found: {title}"
        )

    # ========================================================
    # 2. YOUTUBE FALLBACK
    # ========================================================

    if not stream_url:

        print(
            ">> JioSaavn failed, "
            "trying YouTube..."
        )

        try:

            video_url, yt_title = (
                await asyncio.to_thread(
                    search_youtube,
                    query
                )
            )

        except Exception as e:

            print(
                f">> YouTube Search Error: {e}"
            )

            video_url = None
            yt_title = None

        if video_url:

            try:

                stream_url, duration = (
                    await asyncio.to_thread(
                        get_audio_stream,
                        video_url
                    )
                )

                title = (
                    yt_title
                    or query
                )

                source = "YouTube"

                print(
                    f">> YouTube track found: {title}"
                )

            except Exception as e:

                print(
                    f">> YouTube Stream Error: {e}"
                )

                stream_url = None

    # ========================================================
    # NO TRACK FOUND
    # ========================================================

    if not stream_url:

        await prep_msg.edit(
            "❌ **Song nahi mila!**\n\n"
            "JioSaavn aur YouTube dono se "
            "track nahi mila."
        )

        return

    # ========================================================
    # PLAY STREAM
    # ========================================================

    try:

        chat_id = message.chat.id

        mention = (
            message.from_user.mention
            if message.from_user
            else "Admin"
        )

        await call_py.play(
            chat_id,
            MediaStream(stream_url)
        )

        await prep_msg.delete()

        caption = f""" ᴛιᴛʟє: {title}
ᴅᴜʀᴧᴛιση: {duration}
sᴏᴜʀᴄᴇ: {source}
ʀєǫᴜєsᴛєᴅ: {mention}

🥂 ᴘᴏᴡєʀєᴅ» [𝗔sʜɪsʜ](https://t.me/ll_NAGUMO_ll)"""

        player_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "▷",
                    callback_data="resume_stream"
                ),
                InlineKeyboardButton(
                    "⏸",
                    callback_data="pause_stream"
                ),
                InlineKeyboardButton(
                    "⏭",
                    callback_data="skip_stream"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎵 ᴛᴜηєs ◻️",
                    callback_data="dummy"
                ),
                InlineKeyboardButton(
                    "🏠 ʜᴏᴍє ↗️",
                    url="https://t.me/Rinnegan_anime_group"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 CLOSE ◻",
                    callback_data="close_panel"
                )
            ]
        ])

        if media.get("play_video"):

            await message.reply_video(
                media["play_video"],
                caption=caption,
                reply_markup=player_buttons,
                supports_streaming=True
            )

        elif media.get("play_photo"):

            await message.reply_photo(
                media["play_photo"],
                caption=caption,
                reply_markup=player_buttons
            )

        else:

            await message.reply_text(
                caption,
                reply_markup=player_buttons,
                disable_web_page_preview=True
            )

    except Exception as e:

        print(
            f">> Playback Error: {e}"
        )

        try:

            await prep_msg.edit(
                f"⚠️ **Error:** `{str(e)}`"
            )

        except Exception:
            pass


# ============================================================
# STOP
# ============================================================

@bot.on_message(
    filters.command(
        ["stop", "end"]
    )
    & filters.group
)
async def stop_music(
    _,
    message: Message
):

    if call_py:

        try:

            await call_py.leave_call(
                message.chat.id
            )

            await message.reply_text(
                "⏹️ **Voice Chat se disconnect ho gaya "
                "aur stream end ho gayi.**"
            )

        except Exception as e:

            await message.reply_text(
                f"⚠️ **Error:** `{str(e)}`"
            )


# ============================================================
# PAUSE
# ============================================================

@bot.on_message(
    filters.command("pause")
    & filters.group
)
async def pause_music(
    _,
    message: Message
):

    if call_py:

        try:

            await call_py.pause(
                message.chat.id
            )

            await message.reply_text(
                "⏸️ **Stream pause kar di gayi.**"
            )

        except Exception as e:

            await message.reply_text(
                f"⚠️ **Error:** `{str(e)}`"
            )


# ============================================================
# RESUME
# ============================================================

@bot.on_message(
    filters.command("resume")
    & filters.group
)
async def resume_music(
    _,
    message: Message
):

    if call_py:

        try:

            await call_py.resume(
                message.chat.id
            )

            await message.reply_text(
                "▶️ **Stream dobara shuru ho gayi.**"
            )

        except Exception as e:

            await message.reply_text(
                f"⚠️ **Error:** `{str(e)}`"
            )


# ============================================================
# CALLBACK ROUTER
# ============================================================

@bot.on_callback_query()
async def callback_handler(
    client: Client,
    query: CallbackQuery
):

    data = query.data

    media = await get_db_media()

    # ----------------------------------------
    # CLOSE
    # ----------------------------------------

    if data == "close_panel":

        try:
            await query.message.delete()
        except Exception:
            pass

        return

    # ----------------------------------------
    # RESUME
    # ----------------------------------------

    elif data == "resume_stream":

        if call_py:

            try:

                await call_py.resume(
                    query.message.chat.id
                )

                await query.answer(
                    "Stream Resumed! ▶️"
                )

            except Exception:

                await query.answer(
                    "Stream active nahi hai."
                )

        else:

            await query.answer(
                "Assistant active nahi hai.",
                show_alert=True
            )

    # ----------------------------------------
    # PAUSE
    # ----------------------------------------

    elif data == "pause_stream":

        if call_py:

            try:

                await call_py.pause(
                    query.message.chat.id
                )

                await query.answer(
                    "Stream Paused! ⏸️"
                )

            except Exception:

                await query.answer(
                    "Error pausing stream."
                )

        else:

            await query.answer(
                "Assistant active nahi hai.",
                show_alert=True
            )

    # ----------------------------------------
    # SKIP
    # ----------------------------------------

    elif data == "skip_stream":

        await query.answer(
            "Track skipped! ⏭️"
        )

    # ----------------------------------------
    # HELP MENU
    # ----------------------------------------

    elif data == "help_menu":

        try:

            if (
                query.message.photo
                or query.message.video
            ):

                await query.message.edit_caption(
                    caption=(
                        "✨ **Shinobu Music "
                        "Help Menu & Commands**"
                    ),
                    reply_markup=HELP_GRID_MARKUP
                )

            else:

                await query.message.edit_text(
                    (
                        "✨ **Shinobu Music "
                        "Help Menu & Commands**"
                    ),
                    reply_markup=HELP_GRID_MARKUP
                )

            await query.answer()

        except Exception as e:

            await query.answer(
                f"Error: {str(e)[:150]}",
                show_alert=True
            )

    # ----------------------------------------
    # HELP CATEGORY
    # ----------------------------------------

    elif data.startswith("help_"):

        key = data.split(
            "_",
            1
        )[1]

        help_text = HELP_TEXTS.get(
            key
        )

        if help_text:

            try:

                if (
                    query.message.photo
                    or query.message.video
                ):

                    await query.message.edit_caption(
                        caption=help_text,
                        reply_markup=BACK_BUTTON
                    )

                else:

                    await query.message.edit_text(
                        text=help_text,
                        reply_markup=BACK_BUTTON
                    )

                await query.answer()

            except Exception as e:

                await query.answer(
                    f"Error: {str(e)[:150]}",
                    show_alert=True
                )

    # ----------------------------------------
    # OPEN START
    # ----------------------------------------

    elif data == "open_start":

        try:
            await query.message.delete()
        except Exception:
            pass

        first = (
            query.from_user.first_name
            if query.from_user
            else "User"
        )

        await send_start_message(
            client,
            query.message.chat.id,
            first
        )

        try:
            await query.answer()
        except Exception:
            pass

    # ----------------------------------------
    # DUMMY
    # ----------------------------------------

    elif data == "dummy":

        await query.answer(
            "⚡ Shinobu x Music",
            show_alert=False
        )


# ============================================================
# RENDER HEALTHCHECK
# ============================================================

async def web_health_check(
    request
):

    return web.Response(
        text="Shinobu x Music Bot is Alive!"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        web_health_check
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(
        f">> Web Healthcheck Server "
        f"started on Port {PORT}"
    )


# ============================================================
# MAIN RUNNER
# ============================================================

async def main():

    global user
    global call_py

    # ----------------------------------------
    # WEB SERVER
    # ----------------------------------------

    await start_web_server()

    # ----------------------------------------
    # START BOT
    # ----------------------------------------

    await bot.start()

    bot_info = await bot.get_me()

    print(
        f">> Bot Started Successfully "
        f"as @{bot_info.username}"
    )

    await set_menu_commands()

    # ----------------------------------------
    # LOAD ASSISTANT SESSION
    # ----------------------------------------

    try:

        sess_doc = await session_collection.find_one(
            {"_id": "assistant"}
        )

        if (
            sess_doc
            and sess_doc.get("session")
        ):

            user = Client(
                "shinobu_assistant",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=sess_doc["session"]
            )

            await user.start()

            call_py = PyTgCalls(
                user
            )

            await call_py.start()

            print(
                ">> Assistant Client and "
                "PyTgCalls Started!"
            )

        else:

            print(
                ">> No Assistant Session "
                "found in MongoDB."
            )

    except Exception as e:

        print(
            f">> Assistant Start Warning: {e}"
        )

    # ----------------------------------------
    # LOGGER ALERT
    # ----------------------------------------

    if LOGGER_ID:

        try:

            uptime, storage, cpu, ram = (
                get_system_stats()
            )

            logger_text = f"""🚀 **ꜱʜɪɴᴏʙᴜ x ᴍᴜꜱɪᴄ ʙᴏᴛ ᴏɴʟɪɴᴇ!**

🤖 **ʙᴏᴛ ɴᴀᴍᴇ:** @{bot_info.username}
🆔 **ʙᴏᴛ ɪᴅ:** `{bot_info.id}`
👑 **ᴏᴡɴᴇʀ ɪᴅ:** `{OWNER_ID}`
✨ **ᴜᴘᴛɪᴍᴇ:** `{uptime}`
⚡ **ᴄᴘᴜ ʟᴏᴀᴅ:** `{cpu}`
💾 **ʀᴀᴍ ᴜꜱᴀɢᴇ:** `{ram}`
🗄️ **ᴍᴏɴɢᴏᴅʙ:** `Connected Successfully`
🌐 **ʀᴇɴᴅᴇʀ ᴘᴏʀᴛ:** `{PORT}`
🎵 **JioSaavn:** `Enabled`
▶️ **YouTube:** `Fallback Enabled`
"""

            await bot.send_message(
                LOGGER_ID,
                logger_text
            )

            print(
                ">> Logger message sent!"
            )

        except Exception as ex:

            print(
                f"Logger Alert Failed: {ex}"
            )

    print(
        ">> Shinobu x Music Bot "
        "Successfully Online on Render!"
    )

    # ----------------------------------------
    # PYROGRAM IDLE
    # ----------------------------------------

    await idle()

    print(
        ">> Shutdown signal received."
    )

    # ----------------------------------------
    # STOP PYTGCALLS
    # ----------------------------------------

    if call_py:

        try:

            await call_py.stop()

            print(
                ">> PyTgCalls stopped!"
            )

        except Exception as e:

            print(
                f">> PyTgCalls stop warning: {e}"
            )

    # ----------------------------------------
    # STOP ASSISTANT
    # ----------------------------------------

    if user:

        try:

            await user.stop()

            print(
                ">> Assistant stopped!"
            )

        except Exception as e:

            print(
                f">> Assistant stop warning: {e}"
            )

    # ----------------------------------------
    # STOP BOT
    # ----------------------------------------

    try:

        await bot.stop()

        print(
            ">> Bot stopped!"
        )

    except Exception as e:

        print(
            f">> Bot stop warning: {e}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    loop = asyncio.get_event_loop()

    loop.run_until_complete(
        main()
    )
