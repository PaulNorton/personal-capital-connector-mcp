"""Session persistence, login preferences, and interactive authentication."""

import getpass
import json
import logging
import os
from pathlib import Path
from typing import Optional

from personalcapital import PersonalCapital, RequireTwoFactorException, TwoFactorVerificationModeEnum

logger = logging.getLogger(__name__)

AUTH_DIR = Path.home() / ".config" / "personal-capital-connector"
SESSION_FILE = AUTH_DIR / "session.json"
PREFS_FILE = AUTH_DIR / "prefs.json"

# Delivery methods Empower can send a 2FA code through, in prompt order.
TWO_FACTOR_MODES = ("sms", "email")

# Typed at any prompt to forget the saved answer behind it.
FORGET = "-"


def _write_private(path: Path, payload: str) -> None:
    """Write payload to path, readable only by the owner."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create the file already restricted rather than widening then narrowing it:
    # write_text() would leave live session cookies world-readable until the
    # chmod landed. O_CREAT's mode is ignored when the file already exists, so
    # the chmod still has to run for the overwrite case.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)
    path.chmod(0o600)


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
    _write_private(SESSION_FILE, json.dumps({"session": session, "csrf": csrf}, indent=2))


def clear_session() -> None:
    """Remove the saved session file."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


# ---------------------------------------------------------------------------
# Login preferences
# ---------------------------------------------------------------------------

def load_prefs() -> dict:
    """Load saved login preferences. Unknown or invalid fields are dropped."""
    if not PREFS_FILE.exists():
        return {}
    try:
        data = json.loads(PREFS_FILE.read_text())
    except Exception as e:
        logger.warning("Failed to load preferences: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}

    prefs = {}
    email = data.get("email")
    if isinstance(email, str) and email.strip():
        prefs["email"] = email.strip()
    mode = data.get("two_factor_mode")
    if isinstance(mode, str) and mode.strip().lower() in TWO_FACTOR_MODES:
        prefs["two_factor_mode"] = mode.strip().lower()
    return prefs


def save_prefs(prefs: dict) -> None:
    """Persist login preferences (mode 600). An empty dict removes the file."""
    if not prefs:
        clear_prefs()
        return
    _write_private(PREFS_FILE, json.dumps(prefs, indent=2))


def update_prefs(**changes) -> dict:
    """Merge changes into the saved preferences. A None value forgets a field."""
    prefs = load_prefs()
    for field, value in changes.items():
        if value is None:
            prefs.pop(field, None)
        else:
            prefs[field] = value
    save_prefs(prefs)
    return prefs


def clear_prefs() -> None:
    """Remove the saved preferences file. Leaves the session alone."""
    if PREFS_FILE.exists():
        PREFS_FILE.unlink()


def format_prefs(prefs: dict) -> str:
    """Render preferences for the `prefs` command, with how to change them."""
    if not prefs:
        return (
            "No saved login preferences.\n"
            "They are saved when you run: personal-capital-connector auth"
        )
    lines = [
        f"Saved login preferences ({PREFS_FILE}):",
        f"  email      {prefs.get('email', '—')}",
        f"  2FA code   {prefs.get('two_factor_mode', '—')}",
        "",
        "Change:  personal-capital-connector prefs --email you@example.com --2fa sms",
        "Forget:  personal-capital-connector prefs --clear",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def prompt_with_default(label: str, saved: Optional[str]) -> tuple[str, bool]:
    """
    Ask for a value, offering `saved` as the default.

    Enter keeps the default. Typing FORGET drops it and asks again, and the
    answer given after that is used but not saved. Returns (value, remember).
    """
    remember = True
    while True:
        answer = input(f"{label}{f' [{saved}]' if saved else ''}: ").strip()
        if answer == FORGET:
            if saved:
                print(f"  Forgot the saved {label.lower()}.")
            saved = None
            remember = False
            continue
        if not answer:
            if saved:
                return saved, remember
            print(f"  {label} is required.")
            continue
        return answer, remember


def prompt_two_factor_mode(saved: Optional[str]) -> tuple[str, bool]:
    """Ask how to receive the 2FA code. Returns (mode, remember)."""
    print("\n2FA required. How do you want to receive the code?")
    print("  1) SMS")
    print("  2) Email")
    numbers = {"sms": "1", "email": "2"}
    remember = True
    while True:
        default = numbers.get(saved, "1")
        hint = f"[{default}, saved]" if saved else f"[{default}]"
        answer = input(f"Choice {hint}: ").strip().lower() or default
        if answer == FORGET:
            if saved:
                print("  Forgot the saved 2FA choice.")
            saved = None
            remember = False
            continue
        if answer in ("1", "sms"):
            return "sms", remember
        if answer in ("2", "email"):
            return "email", remember
        print("  Enter 1 or 2.")


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


def interactive_auth(
    email: str = "",
    two_factor_mode: str = "",
    remember: bool = True,
    accept_defaults: bool = False,
) -> PersonalCapital:
    """
    Run the interactive authentication flow including 2FA.

    Prompts for whatever is not passed in, defaulting to the saved preferences.
    Saves the session, and the answers used, on success. With remember=False the
    saved preferences are neither read nor written. With accept_defaults=True
    every saved answer is taken without asking; anything with no saved answer is
    still prompted for, and the password and 2FA code always are.
    """
    prefs = load_prefs() if remember else {}
    if prefs and not accept_defaults:
        print(f"Using saved preferences from {PREFS_FILE}.")
        print(f"Press Enter to accept a default, or type '{FORGET}' to forget it.\n")
    if accept_defaults and not prefs:
        print("Nothing saved to accept yet — asking for everything this time.\n")

    remember_email = remember
    if not email:
        saved_email = prefs.get("email")
        if accept_defaults and saved_email:
            email = saved_email
            print(f"Empower email: {email} (saved)")
        else:
            email, remember_email = prompt_with_default("Empower email", saved_email)
    password = getpass.getpass("Empower password: ")

    if not email or not password:
        raise ValueError("Email and password are required.")

    pc = PersonalCapital()
    # Stays None when 2FA never happens, so an unused preference is left alone.
    used_mode = None
    remember_mode = remember

    try:
        pc.login(email, password)
        print("✓ Logged in (no 2FA required)")
    except RequireTwoFactorException:
        saved_mode = prefs.get("two_factor_mode")
        if two_factor_mode:
            used_mode = two_factor_mode
        elif accept_defaults and saved_mode:
            used_mode = saved_mode
            print(f"\n2FA required. Using your saved choice: {used_mode}.")
        else:
            used_mode, remember_mode = prompt_two_factor_mode(saved_mode)

        if used_mode == "email":
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
        resp = pc.authenticate_password(password)
        data = resp.json()
        if not data.get("spHeader", {}).get("success"):
            err = data.get("spHeader", {}).get("errors") or data.get("spHeader", {}).get("SP_HEADER_KEY")
            raise RuntimeError(f"authenticate_password failed: {err}")
        print("✓ 2FA complete")

    save_session(pc.get_session(), pc.get_csrf())
    print(f"✓ Session saved to {SESSION_FILE}")

    # Validate the saved session is actually usable
    validated = create_authenticated_client()
    if validated is None:
        clear_session()
        raise RuntimeError("Session was saved but validation failed — please try again.")
    print("✓ Session validated successfully")

    if remember:
        changes = {"email": email if remember_email else None}
        if used_mode is not None:
            changes["two_factor_mode"] = used_mode if remember_mode else None
        if update_prefs(**changes):
            print(f"✓ Preferences saved to {PREFS_FILE}")
        else:
            print("✓ No preferences kept.")
    return pc
