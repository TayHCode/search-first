from __future__ import annotations

import os
import sys
from datetime import datetime

import httpx
from mcp import Client, types
from mcp.client.stdio import StdioServerParameters
from mcp.server.transport_security import TransportSecuritySettings

from search_first.discovery import Discovery
from search_first.github import GitHubClient
from search_first.server import create_server

IDEA = "a Chrome extension or script that closes idle browser tabs automatically"
QUERIES = ["chrome idle tab close", "auto close tabs", "inactive tabs extension", "tab auto closer"]


async def test_official_client_tool_contract_and_recorded_call(recorded_transport, recording):
    server = create_server(
        Discovery(
            GitHubClient(transport=recorded_transport),
            datetime.fromisoformat(recording["recorded_at"]),
        )
    )
    async with Client(server) as client:
        listing = await client.list_tools()
        assert {t.name for t in listing.tools} == {
            "search_existing",
            "inspect_repo",
            "compare_candidates",
        }
        search = next(t for t in listing.tools if t.name == "search_existing")
        assert "2 to 6" in search.description
        assert search.annotations.read_only_hint
        assert "idea" in search.input_schema["required"]
        result = await client.call_tool("search_existing", {"idea": IDEA, "queries": QUERIES})
        assert not result.is_error
        assert result.structured_content["verdict"]["closest_match"]
        bad = await client.call_tool("inspect_repo", {"repo": "https://evil.example/a/b"})
        assert bad.is_error


async def test_stdio_subprocess_initializes_and_lists_tools(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "search_first.server", "--cache-ttl", "0"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GITHUB_TOKEN": ""},
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 3
        bad = await client.call_tool("search_existing", {"idea": ""})
        assert bad.is_error


async def test_streamable_http_initialize_list_and_call(recorded_transport, recording):
    server = create_server(
        Discovery(
            GitHubClient(transport=recorded_transport),
            datetime.fromisoformat(recording["recorded_at"]),
        )
    )
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    # Exercise actual MCP JSON-RPC over the SDK ASGI HTTP transport, without live network.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
        ) as client,
    ):
        headers = {"Accept": "application/json, text/event-stream"}
        init = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "fixture-test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200
        assert init.json()["result"]["serverInfo"]["name"] == "search-first"
        headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
        response = await client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        assert len(response.json()["result"]["tools"]) == 3
        response = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_existing",
                    "arguments": {"idea": IDEA, "queries": QUERIES},
                },
            },
        )
        assert response.status_code == 200
        assert not response.json()["result"].get("isError", False)
        assert response.json()["result"]["structuredContent"]["candidates"]


async def test_http_rejects_unapproved_host():
    server = create_server(Discovery(GitHubClient()))
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["localhost:*"]),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://unapproved.example"
        ) as client,
    ):
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert response.status_code == 421
