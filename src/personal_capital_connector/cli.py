"""CLI entry point: auth and prefs subcommands, and default MCP server mode."""

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
    auth_cmd.add_argument(
        "--2fa",
        dest="two_factor",
        choices=("sms", "email"),
        default="",
        help="How to receive the 2FA code, skipping that prompt",
    )
    auth_cmd.add_argument(
        "-y",
        "--accept-defaults",
        action="store_true",
        help="Take every saved preference without asking (still prompts for the password)",
    )
    auth_cmd.add_argument(
        "--no-remember",
        action="store_true",
        help="Ignore the saved preferences and save none this run",
    )

    prefs_cmd = sub.add_parser("prefs", help="Show or change the saved login preferences")
    prefs_cmd.add_argument("--email", help="Save this email as the login default")
    prefs_cmd.add_argument(
        "--2fa",
        dest="two_factor",
        choices=("sms", "email"),
        help="Save this 2FA delivery method as the default",
    )
    prefs_cmd.add_argument(
        "--clear",
        nargs="?",
        const="all",
        choices=("all", "email", "2fa"),
        help="Forget a saved preference, or all of them (the default)",
    )

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
            interactive_auth(
                email=args.email,
                two_factor_mode=args.two_factor,
                remember=not args.no_remember,
                accept_defaults=args.accept_defaults,
            )
            print("\nAll done! Start the MCP server with: personal-capital-connector")
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(1)
        except Exception as e:
            print(f"\nAuthentication failed: {e}")
            sys.exit(1)

    elif args.command == "prefs":
        from .auth import clear_prefs, format_prefs, load_prefs, update_prefs
        if args.clear == "all":
            clear_prefs()
        else:
            changes = {}
            if args.clear == "email":
                changes["email"] = None
            if args.clear == "2fa":
                changes["two_factor_mode"] = None
            if args.email:
                changes["email"] = args.email.strip()
            if args.two_factor:
                changes["two_factor_mode"] = args.two_factor
            if changes:
                update_prefs(**changes)
        print(format_prefs(load_prefs()))

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
