import asyncio
import json
import logging
import os
import random
import re
import socks
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, FileReferenceExpiredError, FloodWaitError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    MessageEntityTextUrl,
    MessageEntityUrl,
)
from tqdm import tqdm

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

API_ID = int(os.environ.get("TG_API_ID", 0))
API_HASH = os.environ.get("TG_API_HASH", "")
PHONE = os.environ.get("TG_PHONE", "")
CHANNEL = os.environ.get("TG_CHANNEL", "")
PROXY_HOST = os.environ.get("TG_PROXY_HOST", "")
PROXY_PORT = int(os.environ.get("TG_PROXY_PORT", 0))
DOWNLOADS_DIR = Path("downloads")
INDEX_PATH = Path("index.json")
SKIP_VIDEO = True  # skip video and voice messages


def make_slug(text: str | None) -> str:
    """Convert post text to a folder name slug."""
    if not text:
        return "no-text"
    # replace non-alphanumeric characters with dashes
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    # truncate to 40 chars at word boundary
    if len(slug) > 40:
        slug = slug[:40].rsplit("-", 1)[0]
    return slug or "no-text"


def safe_filename(name: str, folder: Path, msg_id: int) -> str:
    """Return a conflict-free filename in the given folder."""
    if not (folder / name).exists():
        return name
    stem, _, ext = name.rpartition(".")
    if stem:
        return f"{stem}_{msg_id}.{ext}"
    return f"{name}_{msg_id}"


def _fmt_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    if size_bytes >= 1_024:
        return f"{size_bytes / 1_024:.1f} KB"
    return f"{size_bytes} B"


def format_post_md(
    index: int,
    post_id: int,
    date: datetime,
    views: int,
    text: str,
    links: list[str],
    files: list[dict],
) -> str:
    """Build post.md content for a single post."""
    lines = [
        f"# Post #{index:04d} — {date.strftime('%Y-%m-%d')}",
        "",
        f"**ID:** {post_id}",
        f"**Date:** {date.strftime('%Y-%m-%d %H:%M')}",
        f"**Views:** {views}",
        "",
        "## Text",
        text or "*(no text)*",
    ]
    if links:
        lines += ["", "## Links"]
        for url in links:
            lines.append(f"- {url}")
    if files:
        lines += ["", "## Files"]
        for f in files:
            size_str = _fmt_size(f["size"]) if f.get("size") else "?"
            lines.append(f"- {f['name']} ({size_str}) — from comment by {f['source']}")
    return "\n".join(lines) + "\n"


def update_index(index_path: Path, entry: dict) -> None:
    """Append a post entry to index.json."""
    if index_path.exists():
        data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        data = []
    data.append(entry)
    index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_links(message) -> list[str]:
    """Extract all URLs from message entities."""
    links = []
    if not message.entities:
        return links
    for entity in message.entities:
        if isinstance(entity, MessageEntityUrl):
            links.append(message.text[entity.offset : entity.offset + entity.length])
        elif isinstance(entity, MessageEntityTextUrl):
            links.append(entity.url)
    return links


def get_media_filename(message) -> str | None:
    """Return the original filename from a document, or None."""
    if not message.document:
        return None
    for attr in message.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def _is_skip_media(message) -> bool:
    """Return True if media should be skipped (video or voice)."""
    if message.video:
        return True
    if message.document:
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return True
            if isinstance(attr, DocumentAttributeAudio) and attr.voice:
                return True
        mime = getattr(message.document, "mime_type", "") or ""
        if mime.startswith("video/"):
            return True
    return False


_LOG_STEP = 5 * 1024 * 1024  # log progress every 5 MB


def _make_progress_bar(filename: str, total: int):
    """Create a tqdm progress bar for file download."""
    return tqdm(
        total=total,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=filename[:40],
        leave=False,
        bar_format="{desc}  {bar:30}  {percentage:3.0f}%  {n_fmt}/{total_fmt}  {rate_fmt}",
    )


def _make_progress_cb(filename: str, total: int, bar):
    """Return a progress_callback that logs every 5 MB."""
    last_downloaded = [0]
    last_logged = [0]

    def _progress(current, total_now):
        if bar:
            bar.total = total_now
            bar.update(current - last_downloaded[0])
        last_downloaded[0] = current
        if current - last_logged[0] >= _LOG_STEP or current == total_now:
            pct = int(current / total_now * 100) if total_now else 0
            log.info(
                "  ↓ %s  %s/%s  %d%%",
                filename,
                _fmt_size(current),
                _fmt_size(total_now) if total_now else "?",
                pct,
            )
            last_logged[0] = current

    return _progress


async def download_post_media(client: TelegramClient, message, folder: Path) -> str | None:
    """Download post media to folder. Returns filename or None."""
    if not message.media:
        return None
    if SKIP_VIDEO and _is_skip_media(message):
        log.info("Skipping video in post %d", message.id)
        return None
    filename = get_media_filename(message)
    if not filename:
        if message.photo:
            filename = f"photo_{message.id}.jpg"
        elif message.document:
            filename = f"document_{message.id}.bin"
        else:
            return None
    filename = safe_filename(filename, folder, message.id)
    dest = str(folder / filename)
    total = getattr(getattr(message, "document", None), "size", 0) or 0
    bar = _make_progress_bar(filename, total) if total else None
    _progress = _make_progress_cb(filename, total, bar)

    try:
        await client.download_media(message, file=dest, progress_callback=_progress)
        if bar:
            bar.close()
        log.info("Downloaded from post: %s", filename)
        return filename
    except FileReferenceExpiredError:
        if bar:
            bar.close()
        log.warning("FileReferenceExpired for post %d, retrying...", message.id)
        refreshed = await client.get_messages(message.peer_id, ids=message.id)
        await client.download_media(refreshed, file=dest)
        return filename


async def download_comment_files(
    client: TelegramClient, discussion_id: int | None, post_id: int, folder: Path
) -> list[dict]:
    """Download all files from post comments. Returns list of file metadata dicts."""
    if not discussion_id:
        return []
    downloaded = []
    try:
        async for comment in client.iter_messages(discussion_id, reply_to=post_id):
            if not comment.media:
                continue
            if SKIP_VIDEO and _is_skip_media(comment):
                log.info("Skipping video in comment %d", comment.id)
                continue
            filename = get_media_filename(comment)
            if not filename:
                if comment.photo:
                    filename = f"photo_{comment.id}.jpg"
                elif comment.document:
                    filename = f"document_{comment.id}.bin"
                else:
                    continue
            filename = safe_filename(filename, folder, comment.id)
            dest = str(folder / filename)
            total_c = getattr(getattr(comment, "document", None), "size", 0) or 0
            bar_c = _make_progress_bar(filename, total_c) if total_c else None
            _prog_c = _make_progress_cb(filename, total_c, bar_c)

            try:
                await client.download_media(comment, file=dest, progress_callback=_prog_c)
                if bar_c:
                    bar_c.close()
            except FileReferenceExpiredError:
                if bar_c:
                    bar_c.close()
                refreshed = await client.get_messages(discussion_id, ids=comment.id)
                await client.download_media(refreshed, file=dest)
            size = (folder / filename).stat().st_size if (folder / filename).exists() else 0
            sender = comment.sender
            source = f"@{sender.username}" if sender and getattr(sender, "username", None) else "unknown"
            downloaded.append({"name": filename, "size": size, "source": source})
            log.info("Downloaded from comment: %s (%s)", filename, source)
    except Exception as e:
        log.error("Failed to fetch comments for post %d: %s", post_id, e)
    return downloaded


async def process_post(
    client: TelegramClient,
    message,
    index: int,
    discussion_id: int | None,
    downloads_dir: Path,
    index_path: Path,
) -> None:
    """Process one post: create folder, download media, save post.md."""
    slug = make_slug(message.text)
    date_str = message.date.strftime("%Y-%m-%d")
    folder_name = f"{index:04d}_{date_str}_{slug}"
    folder = downloads_dir / folder_name

    if (folder / "post.md").exists():
        log.info("Post #%04d already downloaded, skipping: %s", index, folder_name)
        return

    folder.mkdir(parents=True, exist_ok=True)
    log.info("Processing post #%04d: %s", index, folder_name)

    # download post media
    post_file = await download_post_media(client, message, folder)

    # download comment attachments
    comment_files = await download_comment_files(client, discussion_id, message.id, folder)

    # extract links
    links = extract_links(message)

    # build file list for post.md
    all_files = []
    if post_file:
        size = (folder / post_file).stat().st_size if (folder / post_file).exists() else 0
        all_files.append({"name": post_file, "size": size, "source": "post"})
    all_files.extend(comment_files)

    # save post.md
    md_content = format_post_md(
        index=index,
        post_id=message.id,
        date=message.date,
        views=getattr(message, "views", 0) or 0,
        text=message.text or "",
        links=links,
        files=all_files,
    )
    (folder / "post.md").write_text(md_content, encoding="utf-8")

    # update index.json
    update_index(index_path, {
        "index": index,
        "post_id": message.id,
        "date": date_str,
        "slug": slug,
        "folder": str(folder),
        "file_count": len(all_files),
        "has_text": bool(message.text),
    })


async def main() -> None:
    """Entry point: authenticate, iterate posts, download."""
    DOWNLOADS_DIR.mkdir(exist_ok=True)

    proxy = (socks.SOCKS5, PROXY_HOST, PROXY_PORT) if PROXY_HOST and PROXY_PORT else None
    client = TelegramClient("session", API_ID, API_HASH, device_model="Desktop",
                            proxy=proxy)
    await client.start(phone=PHONE)
    log.info("Authenticated successfully")
    try:

        # load dialogs and find channel by title or ID
        log.info("Loading dialogs...")
        dialogs = await client.get_dialogs(limit=None)
        channel = None
        for d in dialogs:
            title = getattr(d.entity, "title", None)
            eid = getattr(d.entity, "id", None)
            if str(eid) == str(CHANNEL).lstrip("-").replace("100", "", 1):
                channel = d.entity
                break
        if channel is None:
            channel = await client.get_entity(CHANNEL)
        log.info("Channel found: %s", getattr(channel, "title", CHANNEL))

        # get linked discussion group for comment fetching
        discussion_id = None
        try:
            full = await client(GetFullChannelRequest(channel))
            discussion_id = full.full_chat.linked_chat_id
            if discussion_id:
                log.info("Discussion group ID: %d", discussion_id)
            else:
                log.info("No linked discussion group — comment files will not be downloaded")
        except Exception as e:
            log.warning("Could not get linked chat: %s", e)

        # iterate posts oldest to newest
        index = 0
        async for message in client.iter_messages(channel, reverse=True):
            if not (message.text or message.media):
                continue  # skip service messages
            index += 1
            try:
                await process_post(client, message, index, discussion_id, DOWNLOADS_DIR, INDEX_PATH)
            except ChannelPrivateError:
                log.error("Channel access denied — subscription expired?")
                break
            except FloodWaitError as e:
                log.warning("FloodWait: sleeping %d seconds...", e.seconds)
                await asyncio.sleep(e.seconds)
                index -= 1  # retry this post
                continue
            except Exception as e:
                log.error("Error processing post %d: %s", message.id, e)

            # rate limiting pause
            await asyncio.sleep(random.uniform(0.5, 1.0))

        log.info("Done! Posts downloaded: %d", index)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
