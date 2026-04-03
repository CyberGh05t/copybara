# copybara

Downloads posts and files from private Telegram channels, including files from comments.

## Features

- Downloads all channel posts with text, media, and links
- Downloads files from comments via linked discussion group
- Structure: one post = one folder `NNNN_YYYY-MM-DD_slug/`
- Post metadata saved to `post.md`
- Full index in `index.json`
- Resumable: skips already downloaded posts on restart
- Skips video and voice messages (configurable)

## Requirements

- Python 3.11+
- Telegram Desktop (for session conversion)
- SOCKS5 proxy (optional)

## Installation

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**opentele** must be installed manually with patches (required for tdata → session conversion):

```bash
pip install git+https://github.com/thedemons/opentele.git@main
```

After installation apply 3 patches for Telegram Desktop 6.7.0+ compatibility (see [PATCHES.md](PATCHES.md)).

## Configuration

```bash
cp .env.example .env
```

Fill in `.env`:

```
TG_PHONE=+79001234567
TG_CHANNEL=channel_username_or_id
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
TG_PROXY_HOST=127.0.0.1      # optional, SOCKS5
TG_PROXY_PORT=10815           # optional
TG_TDATA_PATH=/path/to/TelegramDesktop/tdata  # for convert_session.py
```

Get `TG_API_ID` and `TG_API_HASH` at [my.telegram.org](https://my.telegram.org).

## Session Conversion (first run)

If `session.session` does not exist, convert from Telegram Desktop tdata:

```bash
python convert_session.py
```

Reads tdata from `TG_TDATA_PATH`, creates `session.session`.

## Usage

```bash
python copybara.py
```

Progress is logged to `downloads/copybara.log`.

## Output Structure

```
downloads/
  0001_2026-01-01_post-slug/
    post.md       — metadata and text
    image.jpg
    document.pdf
  0002_2026-01-02_another-post/
    post.md
    ...
index.json        — full post index
```

## Known Limitations

**CDN files:** Telegram serves some files via CDN data centers. Telethon may fail to download these (`GetCdnFileRequest` blocked). Workaround: download manually from Telegram Desktop.

## License

MIT
