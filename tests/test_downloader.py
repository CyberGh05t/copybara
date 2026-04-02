import asyncio
import json
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from downloader import (
    download_comment_files,
    download_post_media,
    extract_links,
    format_post_md,
    get_media_filename,
    make_slug,
    safe_filename,
    update_index,
)


class TestMakeSlug:
    def test_basic_text(self):
        assert make_slug("Hello World") == "hello-world"

    def test_cyrillic_text(self):
        result = make_slug("Привет мир")
        assert result == "привет-мир"

    def test_long_text_truncated_at_word_boundary(self):
        text = "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 extra"
        result = make_slug(text)
        assert len(result) <= 40
        assert not result.endswith("-")

    def test_empty_text_returns_no_text(self):
        assert make_slug("") == "no-text"
        assert make_slug(None) == "no-text"

    def test_special_chars_replaced(self):
        result = make_slug("Hello! @World #2024")
        assert "!" not in result
        assert "@" not in result
        assert "#" not in result

    def test_multiple_dashes_collapsed(self):
        result = make_slug("hello   world")
        assert "--" not in result


class TestSafeFilename:
    def test_unique_name_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            assert safe_filename("file.pdf", folder, 999) == "file.pdf"

    def test_duplicate_gets_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "file.pdf").touch()
            result = safe_filename("file.pdf", folder, 42)
            assert result == "file_42.pdf"

    def test_no_extension_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "archive").touch()
            result = safe_filename("archive", folder, 7)
            assert result == "archive_7"


class TestFormatPostMd:
    def test_full_post(self):
        result = format_post_md(
            index=1,
            post_id=123456,
            date=datetime(2024, 3, 15, 12, 21),
            views=316,
            text="Привет мир",
            links=["https://example.com"],
            files=[{"name": "file.pdf", "size": 96870, "source": "@user"}],
        )
        assert "# Post #0001" in result
        assert "2024-03-15" in result
        assert "**ID:** 123456" in result
        assert "**Views:** 316" in result
        assert "Привет мир" in result
        assert "https://example.com" in result
        assert "file.pdf" in result
        assert "94.6 KB" in result
        assert "@user" in result

    def test_post_without_links(self):
        result = format_post_md(
            index=2,
            post_id=2,
            date=datetime(2024, 1, 1),
            views=0,
            text="Текст",
            links=[],
            files=[],
        )
        assert "## Links" not in result

    def test_post_without_files(self):
        result = format_post_md(
            index=3,
            post_id=3,
            date=datetime(2024, 1, 1),
            views=0,
            text="Текст",
            links=[],
            files=[],
        )
        assert "## Files" not in result

    def test_size_formatting(self):
        result = format_post_md(1, 1, datetime(2024, 1, 1), 0, "x", [],
                                [{"name": "a.zip", "size": 8_820_736, "source": "@u"}])
        assert "8.4 MB" in result


class TestUpdateIndex:
    def test_creates_index_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            entry = {"index": 1, "post_id": 123, "date": "2024-01-01",
                     "slug": "test", "folder": "downloads/0001", "file_count": 2, "has_text": True}
            update_index(path, entry)
            data = json.loads(path.read_text())
            assert len(data) == 1
            assert data[0]["post_id"] == 123

    def test_appends_to_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            entry1 = {"index": 1, "post_id": 1, "date": "2024-01-01",
                      "slug": "a", "folder": "d/1", "file_count": 0, "has_text": True}
            entry2 = {"index": 2, "post_id": 2, "date": "2024-01-02",
                      "slug": "b", "folder": "d/2", "file_count": 1, "has_text": False}
            update_index(path, entry1)
            update_index(path, entry2)
            data = json.loads(path.read_text())
            assert len(data) == 2


class TestExtractLinks:
    def test_no_entities(self):
        msg = MagicMock()
        msg.entities = None
        assert extract_links(msg) == []

    def test_url_entity(self):
        from telethon.tl.types import MessageEntityUrl
        entity = MagicMock(spec=MessageEntityUrl)
        entity.offset = 0
        entity.length = 19
        msg = MagicMock()
        msg.text = "https://example.com и текст"
        msg.entities = [entity]
        links = extract_links(msg)
        assert links == ["https://example.com"]

    def test_text_url_entity(self):
        from telethon.tl.types import MessageEntityTextUrl
        entity = MagicMock(spec=MessageEntityTextUrl)
        entity.url = "https://t.me/channel"
        msg = MagicMock()
        msg.text = "ссылка"
        msg.entities = [entity]
        links = extract_links(msg)
        assert links == ["https://t.me/channel"]


class TestGetMediaFilename:
    def test_document_with_filename(self):
        from telethon.tl.types import DocumentAttributeFilename
        attr = MagicMock(spec=DocumentAttributeFilename)
        attr.file_name = "archive.tar.gz"
        doc = MagicMock()
        doc.attributes = [attr]
        msg = MagicMock()
        msg.document = doc
        msg.photo = None
        assert get_media_filename(msg) == "archive.tar.gz"

    def test_photo_returns_none(self):
        msg = MagicMock()
        msg.document = None
        msg.photo = MagicMock()
        assert get_media_filename(msg) is None

    def test_no_media_returns_none(self):
        msg = MagicMock()
        msg.document = None
        msg.photo = None
        assert get_media_filename(msg) is None


class TestDownloadPostMedia:
    def test_message_without_media_returns_none(self):
        async def run():
            client = AsyncMock()
            msg = MagicMock()
            msg.media = None
            msg.document = None
            msg.photo = None
            msg.id = 1
            with tempfile.TemporaryDirectory() as tmp:
                result = await download_post_media(client, msg, Path(tmp))
                assert result is None
                client.download_media.assert_not_called()

        asyncio.run(run())

    def test_document_downloaded_to_folder(self):
        async def run():
            client = AsyncMock()
            client.download_media = AsyncMock(return_value="/tmp/file.pdf")
            msg = MagicMock()
            msg.media = MagicMock()
            msg.document = MagicMock()
            msg.document.mime_type = "application/pdf"
            msg.document.size = 1024
            msg.photo = None
            msg.video = None
            msg.id = 42
            from telethon.tl.types import DocumentAttributeFilename
            attr = MagicMock(spec=DocumentAttributeFilename)
            attr.file_name = "report.pdf"
            msg.document.attributes = [attr]
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                result = await download_post_media(client, msg, folder)
                assert result == "report.pdf"
                client.download_media.assert_called_once()

        asyncio.run(run())


class TestDownloadCommentFiles:
    def test_no_discussion_group_returns_empty(self):
        async def run():
            client = AsyncMock()
            with tempfile.TemporaryDirectory() as tmp:
                result = await download_comment_files(client, None, 1, Path(tmp))
                assert result == []
                client.iter_messages.assert_not_called()

        asyncio.run(run())

    def test_comment_without_media_skipped(self):
        async def run():
            client = AsyncMock()
            comment = MagicMock()
            comment.media = None
            comment.document = None
            comment.photo = None
            comment.id = 10
            comment.sender = MagicMock()
            comment.sender.username = "user1"

            async def fake_iter(*args, **kwargs):
                yield comment

            client.iter_messages = fake_iter
            with tempfile.TemporaryDirectory() as tmp:
                result = await download_comment_files(client, 999, 1, Path(tmp))
                assert result == []

        asyncio.run(run())
