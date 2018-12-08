'''
WebSocket-based streaming kernel interaction APIs.
'''

import asyncio
import logging

import aiohttp
from aiohttp import web

from ai.backend.common.logging import BraceStyleAdapter

log = BraceStyleAdapter(logging.getLogger('ai.backend.gateway.wsproxy'))


class WebSocketProxy:
    __slots__ = (
        'up_conn', 'down_conn',
        'upstream_buffer', 'upstream_buffer_task',
    )

    def __init__(self, up_conn: aiohttp.ClientWebSocketResponse,
                 down_conn: web.WebSocketResponse):
        self.up_conn = up_conn
        self.down_conn = down_conn
        self.upstream_buffer = asyncio.Queue()
        self.upstream_buffer_task = None

    async def proxy(self):
        asyncio.ensure_future(self.downstream())
        await self.upstream()

    async def upstream(self):
        try:
            async for msg in self.down_conn:
                if msg.type in (web.WSMsgType.TEXT, web.WSMsgType.binary):
                    await self.write(msg.data, msg.type)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    log.error("ws connection closed with exception {}",
                              self.up_conn.exception())
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    break
            # here, client gracefully disconnected
        except asyncio.CancelledError:
            # here, client forcibly disconnected
            pass
        finally:
            await self.close_downstream()

    async def downstream(self):
        try:
            self.upstream_buffer_task = \
                    asyncio.ensure_future(self.consume_upstream_buffer())
            async for msg in self.up_conn:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self.down_conn.send_str(msg.data)
                if msg.type == aiohttp.WSMsgType.binary:
                    await self.down_conn.send_bytes(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
            # here, server gracefully disconnected
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception('unexpected error')
        finally:
            await self.close_upstream()

    async def consume_upstream_buffer(self):
        while True:
            msg, tp = await self.upstream_buffer.get()
            if self.up_conn:
                if tp == aiohttp.WSMsgType.TEXT:
                    await self.up_conn.send_str(msg)
                elif tp == aiohttp.WSMsgType.binary:
                    await self.up_conn.send_bytes(msg)
            else:
                await self.close()

    async def write(self, msg: str, tp):
        await self.upstream_buffer.put((msg, tp))

    async def close_downstream(self):
        if not self.down_conn.closed:
            await self.down_conn.close()

    async def close_upstream(self):
        if self.upstream_buffer_task:
            self.upstream_buffer_task.cancel()
            await self.upstream_buffer_task
        if self.up_conn:
            await self.up_conn.close()
