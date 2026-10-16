"""Drain both child pipes without retaining output beyond their independent caps."""
import asyncio
import sys

from src.sandbox.bwrap import STREAM_CAP, _capture_stream


def test_large_stdout_and_stderr_drain_without_deadlock_and_preserve_prefixes():
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c',
            "import os\nfor _ in range(200):\n os.write(1,b'x'*65536)\n os.write(2,b'y'*65536)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout = asyncio.create_task(_capture_stream(process.stdout))
        stderr = asyncio.create_task(_capture_stream(process.stderr))
        await asyncio.wait_for(process.wait(), timeout=10)
        assert process.returncode == 0
        assert await stdout == (b'x' * STREAM_CAP, True)
        assert await stderr == (b'y' * STREAM_CAP, True)
    asyncio.run(run())


def test_output_at_cap_is_not_marked_truncated():
    async def run():
        stream = asyncio.StreamReader()
        stream.feed_data(b'x' * STREAM_CAP)
        stream.feed_eof()
        assert await _capture_stream(stream) == (b'x' * STREAM_CAP, False)
    asyncio.run(run())
