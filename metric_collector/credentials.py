"""Credential resolution: macOS Keychain first, env vars as fallback.

Never hardcode, never persist the password ourselves. We read it on demand:

    1. macOS Keychain via `keyring`, service "toshiba-ac-menubar".
       Store with:  keyring set toshiba-ac-menubar <your-toshiba-email>
       (or set the username via TOSHIBA_USER and store under that account).
    2. Environment variables TOSHIBA_USER / TOSHIBA_PASS.

The Toshiba *account* password is what the cloud login needs. The long-lived
SAS token / device_id are a separate concern handled in config.DEVICE_STATE_PATH.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional


KEYRING_SERVICE = "toshiba-ac-menubar"


class CredentialsError(RuntimeError):
    """Raised when no usable credentials can be found."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def resolve_credentials(
    *, username_env: str = "TOSHIBA_USER", password_env: str = "TOSHIBA_PASS"
) -> Credentials:
    """Return Toshiba account credentials or raise CredentialsError.

    Username resolution: TOSHIBA_USER env var wins; otherwise we cannot guess
    the Keychain account, so the username must be provided somehow.

    Password resolution: Keychain (service=KEYRING_SERVICE, account=username)
    first, then TOSHIBA_PASS.
    """
    username = os.environ.get(username_env)

    password: Optional[str] = None
    if username:
        password = _keyring_get(username)
    if password is None:
        password = os.environ.get(password_env)

    if not username or not password:
        raise CredentialsError(
            "No Toshiba credentials found. Set TOSHIBA_USER and either store the "
            f"password in Keychain (`keyring set {KEYRING_SERVICE} <user>`) or set "
            "TOSHIBA_PASS."
        )
    return Credentials(username=username, password=password)


def _keyring_get(account: str) -> Optional[str]:
    """Fetch a password from Keychain, tolerating keyring being absent.

    `keyring` is an optional import path so the dummy-source workflow doesn't
    hard-require it; the real source pulls it in via requirements.
    """
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, account)
    except Exception as exc:  # keyring backend errors shouldn't crash startup
        print(f"[credentials] keyring lookup failed: {exc}")
        return None
