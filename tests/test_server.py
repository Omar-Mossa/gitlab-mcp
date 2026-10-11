from __future__ import annotations

from mcp import Client

from gitlab_ci_mcp.config import Settings
from gitlab_ci_mcp.server import create_server


async def test_read_only_server_does_not_register_mutation_tools() -> None:
    server = create_server(Settings(gitlab_url="https://gitlab.example.com", token="test"))

    tool_names = {tool.name for tool in await server.list_tools()}

    assert "diagnose_pipeline" in tool_names
    assert "run_pipeline" not in tool_names


async def test_write_enabled_server_registers_guarded_mutation_tools() -> None:
    settings = Settings(gitlab_url="https://gitlab.example.com", token="test", enable_writes=True)
    server = create_server(settings)

    tool_names = {tool.name for tool in await server.list_tools()}

    assert {
        "run_pipeline",
        "retry_pipeline",
        "cancel_pipeline",
        "retry_job",
        "cancel_job",
        "play_manual_job",
    } <= tool_names


async def test_all_tools_publish_object_input_and_output_schemas() -> None:
    settings = Settings(gitlab_url="https://gitlab.example.com", token="test", enable_writes=True)

    tools = await create_server(settings).list_tools()

    assert all(tool.input_schema.get("type") == "object" for tool in tools)
    assert all(tool.output_schema and tool.output_schema.get("type") == "object" for tool in tools)
    assert all(tool.description for tool in tools)


async def test_tool_errors_are_returned_as_structured_content() -> None:
    server = create_server(Settings(gitlab_url="https://gitlab.example.com", token="test"))

    async with Client(server) as client:
        response = await client.call_tool(
            "read_artifact_text",
            {
                "project": "group/project",
                "job_id": 9,
                "request": {"artifact_path": "../secret"},
            },
        )

    assert response.structured_content == {
        "ok": False,
        "error": {
            "code": "invalid_input",
            "message": "artifact_path must be a relative path without parent traversal",
        },
    }
