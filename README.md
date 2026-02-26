## Personal Capital MCP Connector

### File structure
```
personal-capital-connector/
├── pyproject.toml
└── src/personal_capital_connector/
    ├── __init__.py
    ├── __main__.py
    ├── auth.py      # session persistence + 2FA flow
    ├── client.py    # API wrapper + data formatters
    ├── server.py    # FastMCP server with 5 tools
    └── cli.py       # CLI entry point
```

### 5 tools exposed to Claude

| Tool | What it answers |
|------|----------------|
| `list_accounts` | "What's my Chase credit card balance?" / "Show my savings accounts" |
| `get_net_worth` | "What's my net worth?" / "How much do I owe vs own?" |
| `get_transactions` | "What did I spend at restaurants last month?" |
| `get_asset_allocation` | "What's my asset allocation in my 401k?" |
| `check_auth_status` | "Is my Empower session still valid?" |

### Prerequisites
Install uv: https://docs.astral.sh/uv/

### To get started

**Step 1 — Authenticate once (interactive 2FA):**
```bash
uv run --directory {full path to this directory} personal-capital-connector auth
```
Your session is saved to `~/.config/personal-capital-connector/session.json` (chmod 600). Re-run this any time your session expires.

**Step 2 — Add to Claude Desktop's MCP settings** (e.g. `~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "personal-capital": {
      "command": "{full path to uv}",
      "args": [
        "run",
        "--directory",
        "{full path to this directory}",
        "personal-capital-connector"
      ]
    }
  }
}
```

**Step 3 — Restart Claude** and start asking questions.
