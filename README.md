## Personal Capital MCP Connector

### File structure
```
personal-capital-connector/
├── pyproject.toml
└── src/personal_capital_connector/
    ├── __init__.py
    ├── __main__.py
    ├── auth.py      # session + preference persistence, 2FA flow
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
| `get_asset_allocation` | "What am I holding in my 401k?" / "Which funds am I most concentrated in?" |
| `check_auth_status` | "Is my Empower session still valid?" |

### `get_transactions` parameters

Query a date range or a lookback window, filter, and page through results.

| Parameter | Default | Notes |
|---|---|---|
| `days` | 30 | Lookback from today. Ignored when `start_date` is set. |
| `start_date` / `end_date` | — | ISO `YYYY-MM-DD`. `start_date` alone runs through today. |
| `search` | — | Substring match on description, original description, or merchant. Comma-separated terms are ORed: `"avis, hertz, national"`. |
| `account` | — | Substring of the account name, e.g. `"delta"`. |
| `category` | — | Substring of the Personal Capital category, e.g. `"travel"`. |
| `min_amount` / `max_amount` | 0 | Absolute-value bounds. `max_amount=0` means no cap. |
| `limit` / `offset` | 100 / 0 | Page through results. The header always reports the full match count. |
| `oldest_first` | false | Sort ascending by date. |

Amounts are signed: **money out is negative, money in is positive.** The header shows the net,
gross in, and gross out for everything matched — not just the rows on the current page.

```
Transactions — 2026-03-01 to 2026-03-31
3 of 271 matched
Matched total: net $17,746.50  (in $377,693.19 / out $359,946.69)

2026-03-31      5,516.08  Brokeragelink - Contribution  [Amazon 401(k) Plan]  (Retirement Contributions)
...
268 more. Re-run with offset=3 to continue.
```

### Prerequisites
Install uv: https://docs.astral.sh/uv/

### To get started

**Step 1 — Authenticate once (interactive 2FA):**
```bash
uv run --directory {full path to this directory} personal-capital-connector auth
```
Your session is saved to `~/.config/personal-capital-connector/session.json` (chmod 600). Re-run this any time your session expires.

Your email and 2FA choice are saved next to it in `prefs.json`, so the next login is two keystrokes:

```
Using saved preferences from ~/.config/personal-capital-connector/prefs.json.
Press Enter to accept a default, or type '-' to forget it.

Empower email [paul@example.com]:
Empower password:

2FA required. How do you want to receive the code?
  1) SMS
  2) Email
Choice [2, saved]:
```

Press Enter to accept a default, type a new value to replace it, or type `-` to forget it — the answer you give after `-` is used for that login but not saved.

Manage the same preferences without logging in:

```bash
personal-capital-connector prefs                        # show what is saved
personal-capital-connector prefs --email you@email.com  # change the email
personal-capital-connector prefs --2fa sms              # change the 2FA method
personal-capital-connector prefs --clear email          # forget one (email or 2fa)
personal-capital-connector prefs --clear                # forget both
```

Clearing preferences never touches your session.

Once your preferences are saved, `-y` takes them all without asking:

```bash
personal-capital-connector auth -y
```

That leaves only the password, and the 2FA code when one is needed. Anything with no saved value is still prompted for. `--email` and `--2fa` override a saved value for one run, and `--no-remember` logs in without reading or writing preferences at all.

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
