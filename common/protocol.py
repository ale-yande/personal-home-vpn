"""
Custom framing and multiplexing protocol over TLS for Home VPN.
Handles authentication handshakes, status queries, and high-performance
asynchronous stream piping.
"""

import json
import struct
import asyncio
from typing import Dict, Any

PROTOCOL_VERSION = 1

STATUS_OK = "OK"
STATUS_ERROR = "ERROR"

# 4-byte big-endian length prefix for control messages
HEADER_STRUCT = struct.Struct("!I")


async def send_msg(writer: asyncio.StreamWriter, data: Dict[str, Any]) -> None:
    """Serializes a dictionary to JSON and sends it with a length prefix."""
    payload = json.dumps(data).encode("utf-8")
    header = HEADER_STRUCT.pack(len(payload))
    writer.write(header + payload)
    await writer.drain()


async def read_msg(reader: asyncio.StreamReader) -> Dict[str, Any]:
    """Reads a length-prefixed JSON message from the stream."""
    header = await reader.readexactly(HEADER_STRUCT.size)
    (length,) = HEADER_STRUCT.unpack(header)
    if length > 65536:
        raise ValueError("Control message exceeds maximum allowed buffer (64KB)")
    payload = await reader.readexactly(length)
    return json.loads(payload.decode("utf-8"))


async def forward_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, buffer_size: int = 65536) -> None:
    """Forwards bytes from an asyncio reader to a writer until EOF or socket closure."""
    try:
        while True:
            data = await reader.read(buffer_size)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()
        except Exception:
            pass


async def pipe_bidirectional(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter
) -> None:
    """Binds two bidirectional TCP streams together asynchronously."""
    task1 = asyncio.create_task(forward_stream(client_reader, remote_writer))
    task2 = asyncio.create_task(forward_stream(remote_reader, client_writer))
    done, pending = await asyncio.wait([task1, task2], return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
