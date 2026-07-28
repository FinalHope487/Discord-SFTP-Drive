import aiohttp
import asyncio
import logging
from src.config import DISCORD_BOT_TOKEN, DISCORD_USER_ID, DISCORD_CHANNEL_ID

logger = logging.getLogger(__name__)

class DiscordAPI:
    def __init__(self):
        self.session = None
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}"
        }
        self.dm_channel_id = None

    async def get_target_channel_id(self):
        if self.dm_channel_id:
            return self.dm_channel_id
        
        if DISCORD_USER_ID:
            endpoint = "/users/@me/channels"
            data = {"recipient_id": DISCORD_USER_ID}
            result = await self._request("POST", endpoint, json=data)
            self.dm_channel_id = result["id"]
            return self.dm_channel_id
            
        return DISCORD_CHANNEL_ID

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method, endpoint, *, data_factory=None, **kwargs):
        """`data_factory`, if given, is called to build a fresh `data=` value
        for every attempt. aiohttp.FormData is single-use — retrying a 429
        with the same instance raises "Form data has been processed already"
        instead of resending, which is why a plain `data=` kwarg won't do for
        multipart bodies. `json=`/`params=` etc. are plain values aiohttp
        re-serializes per call, so they pass straight through in `kwargs`.
        """
        session = await self.get_session()
        url = f"{self.base_url}{endpoint}"

        retries = 5
        for attempt in range(retries):
            request_kwargs = dict(kwargs)
            if data_factory is not None:
                request_kwargs["data"] = data_factory()

            async with session.request(method, url, **request_kwargs) as response:
                if response.status == 429:
                    # Rate limited
                    data = await response.json()
                    retry_after = data.get("retry_after", 1.0)
                    logger.warning(f"Rate limited by Discord. Retrying after {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    continue

                if not response.ok:
                    text = await response.text()
                    logger.error(f"Discord API Error {response.status}: {text}")
                    response.raise_for_status()

                if response.status == 204: # No content
                    return None
                return await response.json()
        raise Exception("Max retries exceeded for Discord API")

    async def upload_chunk(self, file_bytes: bytes, filename: str):
        def build_form_data():
            data = aiohttp.FormData()
            data.add_field("file", file_bytes, filename=filename, content_type="application/octet-stream")
            return data

        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages"
        result = await self._request("POST", endpoint, data_factory=build_form_data)

        message_id = result["id"]
        attachment = result["attachments"][0]
        url = attachment["url"]
        size = attachment["size"]
        
        return message_id, url, size

    async def get_attachment_url(self, message_id: str):
        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        result = await self._request("GET", endpoint)
        if not result.get("attachments"):
            raise Exception("No attachments found on the message")
        return result["attachments"][0]["url"]

    async def delete_message(self, message_id: str):
        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        await self._request("DELETE", endpoint)

    async def download_chunk(self, url: str) -> bytes:
        session = await self.get_session()
        async with session.get(url) as response:
            if not response.ok:
                response.raise_for_status()
            return await response.read()

discord_api = DiscordAPI()
