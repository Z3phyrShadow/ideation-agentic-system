"""
cli.py (mcp_server)
-------------------
Stdio MCP entry point — Antigravity spawns this as a subprocess.

Communication happens via stdin/stdout (stdio transport), so there are no
HTTP servers, no ports, and no SSE routing issues.

Antigravity's mcp_config.json:
    {
        "mcpServers": {
            "ideation-system": {
                "command": "uv",
                "args": [
                    "--directory", "<absolute-path-to-project>",
                    "run", "python", "-m", "mcp_server.cli"
                ]
            }
        }
    }
"""

from mcp_server.server import mcp

if __name__ == "__main__":
    mcp.run()  # stdio transport — reads from stdin, writes to stdout
