"""Call MCP tools in-process against a server bound to the test transaction."""

import asyncio
import json
from typing import Any

from fastmcp import Client, FastMCP


def call(server: FastMCP, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool and return its JSON result, or the structured error payload."""

    async def run() -> dict[str, Any]:
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
        if result.is_error:
            payload: dict[str, Any] = json.loads(result.content[0].text)
            return payload
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    return asyncio.run(run())
