from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client, StdioServerParameters


async def smoke_stdio() -> None:
    project_root = Path(__file__).resolve().parents[1]
    executable = project_root / ".venv" / "bin" / "gitlab-ci-mcp"
    server = StdioServerParameters(
        command=str(executable),
        cwd=project_root,
        env={"GITLAB_TOKEN": "stdio-smoke-token", "GITLAB_URL": "https://gitlab.example.com"},
    )
    async with Client(server) as client:
        tools = await client.list_tools()
        print(",".join(tool.name for tool in tools.tools))


if __name__ == "__main__":
    asyncio.run(smoke_stdio())
