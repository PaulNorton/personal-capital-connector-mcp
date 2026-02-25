"""CLI entry point: auth subcommand and default MCP server mode."""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="personal-capital-connector",
        description="Personal Capital MCP connector for Claude",
    )
    sub = parser.add_subparsers(dest="command")

    auth_cmd = sub.add_parser("auth", help="Authenticate with Personal Capital (interactive 2FA)")
    auth_cmd.add_argument("--email", default="", help="Empower account email")

    sub.add_parser("status", help="Check whether the saved session is valid")
    sub.add_parser("serve", help="Start the MCP server (same as default)")

    args = parser.parse_args()

    if args.command == "auth":
        from .auth import interactive_auth
        print("=" * 52)
        print("  Personal Capital — Authentication")
        print("=" * 52)
        print()
        try:
            interactive_auth(email=args.email)
            print("\nAll done! Start the MCP server with: personal-capital-connector")
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(1)
        except Exception as e:
            print(f"\nAuthentication failed: {e}")
            sys.exit(1)

    elif args.command == "status":
        from .auth import SESSION_FILE, create_authenticated_client
        if not SESSION_FILE.exists():
            print("Not authenticated. Run: personal-capital-connector auth")
            sys.exit(1)
        pc = create_authenticated_client()
        if pc:
            print("✓ Session is valid.")
        else:
            print("✗ Session is expired. Run: personal-capital-connector auth")
            sys.exit(1)

    else:
        # Default (no subcommand, or "serve"): start the MCP server
        from .server import main as serve
        serve()
