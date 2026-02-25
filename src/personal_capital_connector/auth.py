"""Session persistence and interactive authentication for Personal Capital."""

import getpass
import json
import logging
from pathlib import Path
from typing import Optional

from personalcapital import PersonalCapital, RequireTwoFactorException, TwoFactorVerificationModeEnum

logger = logging.getLogger(__name__)

AUTH_DIR = Path.home() / ".config" / "personal-capital-connector"
SESSION_FILE = AUTH_DIR / "session.json"


def load_session() -> Optional[tuple[dict, str]]:
    """Load saved session from disk. Returns (cookies, csrf) or None."""
    if not SESSION_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text())
        return data["session"], data["csrf"]
    except Exception as e:
        logger.warning("Failed to load session: %s", e)
        return None


def save_session(session: dict, csrf: str) -> None:
    """Persist session cookies and CSRF token to disk (mode 600)."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"session": session, "csrf": csrf}, indent=2))
    SESSION_FILE.chmod(0o600)


def clear_session() -> None:
    """Remove the saved session file."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def create_authenticated_client() -> Optional[PersonalCapital]:
    """
    Load the saved session and return a validated PersonalCapital client.
    Returns None if no session exists or the session has expired.
    """
    saved = load_session()
    if not saved:
        return None

    session, csrf = saved
    pc = PersonalCapital()
    pc.set_session(session)
    pc.set_csrf(csrf)

    try:
        response = pc.fetch("/newaccount/getAccounts")
        data = response.json()
        if data.get("spHeader", {}).get("success"):
            return pc
        auth_level = data.get("spHeader", {}).get("authLevel", "")
        logger.warning("Session invalid. authLevel=%s", auth_level)
        return None
    except Exception as e:
        logger.warning("Session validation failed: %s", e)
        return None


def interactive_auth(email: str = "") -> PersonalCapital:
    """
    Run the interactive authentication flow including 2FA.
    Prompts for credentials if not provided. Saves the session on success.
    """
    if not email:
        email = input("Empower email: ").strip()
    password = getpass.getpass("Empower password: ")

    if not email or not password:
        raise ValueError("Email and password are required.")

    pc = PersonalCapital()

    try:
        pc.login(email, password)
        print("✓ Logged in (no 2FA required)")
    except RequireTwoFactorException:
        print("\n2FA required. How do you want to receive the code?")
        print("  1) SMS")
        print("  2) Email")
        choice = input("Choice [1]: ").strip() or "1"

        if choice == "2":
            mode = TwoFactorVerificationModeEnum.EMAIL
            label = "email"
        else:
            mode = TwoFactorVerificationModeEnum.SMS
            label = "SMS"

        print(f"Sending 2FA code via {label}...")
        pc.two_factor_challenge(mode)

        code = input("Enter the 2FA code: ").strip()
        if not code:
            raise ValueError("2FA code is required.")

        pc.two_factor_authenticate(mode, code)
        pc.authenticate_password(password)
        print("✓ 2FA complete")

    save_session(pc.get_session(), pc.get_csrf())
    print(f"✓ Session saved to {SESSION_FILE}")
    return pc
