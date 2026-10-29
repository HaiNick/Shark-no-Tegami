"""Exercise send_email through the real FastMCP server object."""

import importlib

import pytest
from fastmcp import Client


@pytest.fixture
def server(monkeypatch):
    for var in ("OIDC_ENABLED", "MCP_API_KEY", "TEGAMI_REF_ALLOWLIST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MAIL_FROM", "tegami@snowy-burbot.com")
    monkeypatch.setenv("TEGAMI_ALLOWED_RECIPIENTS", "01nicklas07@gmail.com")
    import server as mod

    return importlib.reload(mod)


async def test_tool_schema_and_dry_run(server, digest_md):
    async with Client(server.mcp) as client:
        tool = next(t for t in await client.list_tools() if t.name == "send_email")
        assert set(tool.input_schema["properties"]) == {
            "to", "subject", "body", "body_ref", "content_type", "theme", "cc", "bcc", "dry_run",
        }
        assert sorted(tool.input_schema["required"]) == ["subject", "to"]
        res = await client.call_tool(
            "send_email",
            {"to": "01nicklas07@gmail.com", "subject": "s", "body": digest_md,
             "content_type": "markdown", "theme": "shimbun", "dry_run": True},
        )
        assert res.structured_content["status"] == "validated"
        assert res.structured_content["items"] == {"Security": 1, "Releases": 1}


async def test_errors_are_results_not_exceptions(server):
    async with Client(server.mcp) as client:
        res = await client.call_tool("send_email", {"to": "evil@example.com", "subject": "s", "body": "x"})
        assert res.is_error is False
        assert res.structured_content["code"] == "E_RECIPIENT"
        res = await client.call_tool("send_email", {"to": "01nicklas07@gmail.com", "subject": "s", "body_ref": f"HaiNick/shimbun-archive@{'a' * 40}:digests/x.md"})
        assert res.structured_content["code"] == "E_REF_NOT_ALLOWED"


def test_startup_check_reports_problems_without_raising(tmp_path):
    from tegami.config import DEFAULT_THEMES_DEFAULT_DIR
    from tegami.themes import startup_check

    themes, problems = startup_check(tmp_path, DEFAULT_THEMES_DEFAULT_DIR)
    assert themes == ["alert", "default", "shimbun"] and problems == []
    (tmp_path / "shimbun").mkdir()
    (tmp_path / "shimbun" / "email.html.j2").write_text("{% for %}")
    themes, problems = startup_check(tmp_path, DEFAULT_THEMES_DEFAULT_DIR)
    assert len(problems) == 1 and "E_THEME_TEMPLATE" in problems[0] and "(override) line 1" in problems[0]


async def test_json_alert_through_mcp(server):
    import json

    async with Client(server.mcp) as client:
        tool = next(t for t in await client.list_tools() if t.name == "send_email")
        assert "E_THEME_TEMPLATE" in tool.description and "E_DATA_INVALID" in tool.description
        res = await client.call_tool("send_email", {
            "to": "01nicklas07@gmail.com", "subject": "alert", "content_type": "json", "theme": "alert", "dry_run": True,
            "body": json.dumps({"title": "t", "severity": "info", "summary": "s", "fields": []}),
        })
        assert res.structured_content["status"] == "validated", res.structured_content
