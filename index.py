import base64
import json
import os
import struct
from fastapi import FastAPI, HTTPException, Query, Response, Depends
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityCustomEmoji

# Load credentials from Vercel Environment Variables
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION = os.getenv("SESSION")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


async def get_telegram_client():
    """Manages Telegram client connection per request."""
    if not API_ID or not API_HASH or not SESSION:
        raise HTTPException(
            status_code=500, detail="Telegram credentials missing in environment variables."
        )

    # Convert API_ID to integer safely
    try:
        api_id_int = int(API_ID)
    except ValueError:
        raise HTTPException(status_code=500, detail="API_ID must be an integer.")

    client = TelegramClient(StringSession(SESSION), api_id_int, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise HTTPException(
            status_code=401, detail="Invalid or expired Telegram StringSession."
        )

    try:
        yield client
    finally:
        await client.disconnect()


def rle_encode(data: bytes) -> bytes:
    """Telegram zero-byte run-length encoding (RLE)."""
    result = bytearray()
    zeros = 0
    for b in data:
        if b == 0:
            zeros += 1
        else:
            if zeros > 0:
                result.append(0)
                result.append(zeros)
                zeros = 0
            result.append(b)
    if zeros > 0:
        result.append(0)
        result.append(zeros)
    return bytes(result)


def to_bot_file_id(message):
    """Encodes Telegram media into standard Bot API Base64 file_id (AgAC...)."""
    if not message.media:
        return None, None

    try:
        # Handle Photos
        if message.photo:
            photo = message.photo
            file_ref = photo.file_reference or b""

            payload = struct.pack("<i", 2)
            payload += struct.pack("<i", photo.dc_id)
            payload += struct.pack("<q", photo.id)
            payload += struct.pack("<q", photo.access_hash)
            payload += struct.pack("<I", len(file_ref)) + file_ref

            size_type = b"x"
            if hasattr(photo, "sizes") and photo.sizes:
                for s in photo.sizes:
                    if hasattr(s, "type") and isinstance(s.type, str):
                        size_type = s.type.encode("utf-8")

            payload += struct.pack("<I", len(size_type)) + size_type

            bot_file_id = (
                base64.urlsafe_b64encode(rle_encode(payload))
                .decode("ascii")
                .rstrip("=")
            )
            return bot_file_id, "photo"

        # Handle Documents, Videos, Audios, Stickers
        elif message.document:
            doc = message.document
            file_ref = doc.file_reference or b""

            file_type = "document"
            if message.video:
                file_type = "video"
            elif message.audio or getattr(message, "voice", None):
                file_type = "audio"
            elif message.sticker:
                file_type = "sticker"

            payload = struct.pack("<i", 4)
            payload += struct.pack("<i", doc.dc_id)
            payload += struct.pack("<q", doc.id)
            payload += struct.pack("<q", doc.access_hash)
            payload += struct.pack("<I", len(file_ref)) + file_ref

            bot_file_id = (
                base64.urlsafe_b64encode(rle_encode(payload))
                .decode("ascii")
                .rstrip("=")
            )
            return bot_file_id, file_type

    except Exception as exc:
        print(f"Error encoding Bot File ID: {exc}")
        if message.photo:
            return str(message.photo.id), "photo"
        elif message.document:
            return str(message.document.id), "document"

    return None, None


@app.get("/saved")
async def get_saved_messages(
    limit: int = Query(default=10, ge=1, le=50),
    client: TelegramClient = Depends(get_telegram_client),
):
    """Fetch saved messages returning text, Bot API file_id, and custom emojis."""
    messages_list = []

    async for message in client.iter_messages("me", limit=limit):
        text = message.text or ""

        if not text and not message.media:
            continue

        file_id, file_type = to_bot_file_id(message)

        entities = []
        if message.entities:
            for entity in message.entities:
                if isinstance(entity, MessageEntityCustomEmoji):
                    entities.append(
                        {
                            "type": "custom_emoji",
                            "offset": entity.offset,
                            "length": entity.length,
                            "custom_emoji_id": str(entity.document_id),
                        }
                    )

        messages_list.append(
            {
                "text": text,
                "file_id": file_id,
                "file_type": file_type,
                "entities": entities,
            }
        )

    pretty_content = json.dumps(messages_list, indent=2, ensure_ascii=False)
    return Response(content=pretty_content, media_type="application/json")


# Local testing support
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("index:app", host="127.0.0.1", port=8000, reload=True)
