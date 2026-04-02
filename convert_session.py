import asyncio
import os
import socks
from dotenv import load_dotenv
from opentele.td import TDesktop
from opentele.api import UseCurrentSession

load_dotenv()

TDATA = os.environ.get("TG_TDATA_PATH", "")
_proxy_host = os.environ.get("TG_PROXY_HOST", "")
_proxy_port = int(os.environ.get("TG_PROXY_PORT", 0))
PROXY = (socks.SOCKS5, _proxy_host, _proxy_port) if _proxy_host and _proxy_port else None


async def convert():
    print("Loading tdata...")
    tdesk = TDesktop(TDATA)
    print(f"Accounts found: {len(tdesk.accounts)}")

    print("Converting to Telethon session...")
    client = await tdesk.ToTelethon(
        "session.session",
        flag=UseCurrentSession,
        proxy=PROXY,
    )

    print("Connecting...")
    await client.connect()

    me = await client.get_me()
    if me:
        print(f"Authorized: {me.first_name} (@{me.username}), id={me.id}")
    else:
        print("get_me() returned None — not authorized")

    await client.disconnect()
    print("Done. session.session created.")


asyncio.run(convert())
