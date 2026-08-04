import json
import os
from fastapi import FastAPI, HTTPException, Query, Response, Depends
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
from telethon.tl.types import (
    DocumentAttributeCustomEmoji,
    MessageEntityCustomEmoji,
)

# Load Credentials strictly from Environment Variables
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION = os.getenv("TELEGRAM_SESSION", "")

app = FastAPI(
    title="Telegram Saved Messages API",
    version="1.0",
)


async def get_telegram_client():
    """Serverless Telegram client provider (connects & disconnects per request)."""
    if not API_ID or not API_HASH or not SESSION:
        raise HTTPException(
            status_code=500,
            detail="Telegram environment variables missing in deployment.",
        )

    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired Telegram StringSession.",
        )

    try:
        yield client
    finally:
        await client.disconnect()


def get_utf16_substring(text: str, offset: int, length: int) -> str:
    """Safely extracts substrings using Telegram's UTF-16 offset indexing."""
    if not text:
        return ""
    encoded = text.encode("utf-16-le")
    start = offset * 2
    end = (offset + length) * 2
    return encoded[start:end].decode("utf-16-le", errors="ignore")


async def fetch_emoji_details(client: TelegramClient, emoji_ids: list[int]):
    """Fetches complete document metadata for custom emoji IDs."""
    if not emoji_ids:
        return {}

    try:
        res = await client(
            GetCustomEmojiDocumentsRequest(document_id=emoji_ids)
        )
        emoji_map = {}

        for doc in res:
            alt_emoji = "?"
            stickerset_id = "N/A"
            free_with_premium = False

            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeCustomEmoji):
                    alt_emoji = attr.alt or "?"
                    free_with_premium = getattr(
                        attr, "free_with_premium", False
                    )
                    if attr.stickerset:
                        stickerset_id = getattr(attr.stickerset, "id", "N/A")

            if doc.mime_type == "application/x-tgsticker":
                emoji_format = "Animated Vector (TGS)"
            elif doc.mime_type == "video/webm":
                emoji_format = "Video Emoji (WEBM)"
            else:
                emoji_format = "Static Image (WEBP)"

            emoji_map[doc.id] = {
                "alt_emoji": alt_emoji,
                "format": emoji_format,
                "mime_type": doc.mime_type,
                "size_kb": round(doc.size / 1024, 2),
                "is_premium_only": not free_with_premium,
                "stickerset_id": str(stickerset_id),
            }

        return emoji_map
    except Exception as exc:
        print(f"Error fetching emoji metadata: {exc}")
        return {}


def pretty_json_response(data: dict, status_code: int = 200) -> Response:
    """Returns a formatted, human-readable (pretty-printed) JSON HTTP response."""
    pretty_content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return Response(
        content=pretty_content,
        media_type="application/json",
        status_code=status_code,
    )


@app.get("/saved-messages")
async def get_saved_messages(
    limit: int = Query(
        default=10, ge=1, le=50, description="Number of messages to fetch"
    ),
    client: TelegramClient = Depends(get_telegram_client),
):
    """Fetch saved messages including custom/premium emoji details."""
    messages_list = []

    async for message in client.iter_messages("me", limit=limit):
        if not message.text and not message.entities:
            continue

        emoji_entities = []
        if message.entities:
            for entity in message.entities:
                if isinstance(entity, MessageEntityCustomEmoji):
                    emoji_entities.append(entity)

        custom_emojis_data = []
        if emoji_entities:
            unique_ids = list({e.document_id for e in emoji_entities})
            details_map = await fetch_emoji_details(client, unique_ids)

            for entity in emoji_entities:
                placeholder = get_utf16_substring(
                    message.text, entity.offset, entity.length
                )
                details = details_map.get(entity.document_id, {})

                custom_emojis_data.append(
                    {
                        "placeholder": placeholder,
                        "offset": entity.offset,
                        "length": entity.length,
                        "custom_emoji_id": entity.document_id,
                        "alt_fallback_emoji": details.get("alt_emoji", "?"),
                        "format": details.get("format", "Unknown"),
                        "mime_type": details.get("mime_type", "Unknown"),
                        "size_kb": details.get("size_kb", 0),
                        "is_premium_only": details.get(
                            "is_premium_only", False
                        ),
                        "sticker_pack_id": details.get("stickerset_id", "N/A"),
                    }
                )

        messages_list.append(
            {
                "message_id": message.id,
                "date": message.date.isoformat() if message.date else None,
                "text": message.text or "",
                "has_custom_emojis": len(custom_emojis_data) > 0,
                "custom_emojis_count": len(custom_emojis_data),
                "custom_emojis": custom_emojis_data,
            }
        )

    payload = {
        "status": "success",
        "total_messages": len(messages_list),
        "messages": messages_list,
    }

    return pretty_json_response(payload)
