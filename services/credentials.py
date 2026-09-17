"""Credential access backed by the OS keychain, with environment override."""
from __future__ import annotations

import os
import hashlib

SERVICE = "Ty Work Hub"
ACCOUNT = "mirax.backlog.com API key"
ENVIRONMENT_SERVICE = "Ty Work Hub Environment"

def get_backlog_key() -> str:
    value = os.getenv("BACKLOG_API_KEY", "").strip()
    if value:
        return value
    try:
        import keyring
        return (keyring.get_password(SERVICE, ACCOUNT) or "").strip()
    except Exception:
        return ""

def save_backlog_key(value: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Install the 'keyring' package or set BACKLOG_API_KEY in your environment.") from exc
    value = value.strip()
    if value:
        try:
            keyring.set_password(SERVICE, ACCOUNT, value)
        except Exception as exc:
            raise RuntimeError("Could not access the system keyring. On Linux, unlock your desktop Secret Service keyring or set BACKLOG_API_KEY.") from exc
    else:
        try: keyring.delete_password(SERVICE, ACCOUNT)
        except Exception: pass

def backlog_key_configured() -> bool:
    return bool(get_backlog_key())


def _environment_account(project: str, environment: str) -> str:
    value = f"{str(project).strip()}\0{str(environment).strip()}".encode("utf-8")
    return "environment-" + hashlib.sha256(value).hexdigest()


def get_environment_password(project: str, environment: str) -> str:
    try:
        import keyring
        return (keyring.get_password(ENVIRONMENT_SERVICE, _environment_account(project, environment)) or "").strip()
    except Exception:
        return ""


def save_environment_password(project: str, environment: str, value: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Install the 'keyring' package to securely save environment passwords.") from exc
    account = _environment_account(project, environment)
    value = str(value or "").strip()
    if value:
        try:
            keyring.set_password(ENVIRONMENT_SERVICE, account, value)
        except Exception as exc:
            raise RuntimeError("Could not access the system keyring for environment passwords. On Linux, unlock your desktop Secret Service keyring.") from exc
    else:
        try:
            keyring.delete_password(ENVIRONMENT_SERVICE, account)
        except Exception:
            pass


def delete_environment_password(project: str, environment: str) -> None:
    save_environment_password(project, environment, "")
