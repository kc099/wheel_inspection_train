"""User profiles + password authentication.

Stored in users.json next to settings.json. Passwords are NEVER stored in plain
text — we keep a salted PBKDF2-HMAC-SHA256 hash (Python stdlib, no extra deps).

Roles:
  developer — manage user profiles, edit/rename/delete models, change Modbus
              settings, view the activity log, and train.
  operator  — train and use the app normally.

Honest scope note: this is an *operational* guard for a shop-floor PC — it stops
the wrong person casually retraining or deleting models. Anyone with filesystem
access to the machine could still edit users.json, so it is not protection
against a determined attacker.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from .paths import ROOT                         # frozen-aware (PyInstaller safe)

USERS_PATH = ROOT / "users.json"

ROLE_DEVELOPER = "developer"
ROLE_OPERATOR = "operator"

# Seeded on first run. Change the password from the User Profiles window.
_SEED_USERNAME = "Rba"
_SEED_PASSWORD = "Rba123"

_ITERATIONS = 200_000       # PBKDF2 rounds — slow enough to resist guessing


@dataclass
class User:
    """One profile. `password_hash` is a PBKDF2 hash of (password + salt)."""

    username: str
    salt: str
    password_hash: str
    role: str = ROLE_OPERATOR

    @property
    def is_developer(self) -> bool:
        """True if this profile may manage users/models/settings. Returns: bool."""
        return self.role == ROLE_DEVELOPER

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "salt": self.salt,
            "password_hash": self.password_hash,
            "role": self.role,
        }

    @staticmethod
    def from_dict(d: dict) -> "User":
        return User(
            username=str(d.get("username", "")),
            salt=str(d.get("salt", "")),
            password_hash=str(d.get("password_hash", "")),
            role=str(d.get("role", ROLE_OPERATOR)),
        )


def _hash_password(password: str, salt: str) -> str:
    """Derive the stored hash for a password + salt. Returns: hex digest str."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    ).hex()


def _new_salt() -> str:
    """Fresh random salt so two equal passwords hash differently. Returns: hex str."""
    return secrets.token_hex(16)


def make_user(username: str, password: str, role: str = ROLE_OPERATOR) -> User:
    """Build a User with a freshly salted+hashed password. Returns: User."""
    salt = _new_salt()
    return User(username.strip(), salt, _hash_password(password, salt), role)


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON via temp file + rename so a crash can't corrupt it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_users() -> list[User]:
    """All profiles, seeding the developer account on first run.

    Returns: list[User] (always contains at least one developer).
    """
    if not USERS_PATH.exists():
        users = [make_user(_SEED_USERNAME, _SEED_PASSWORD, ROLE_DEVELOPER)]
        save_users(users)
        return users
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        users = [User.from_dict(u) for u in data.get("users", [])]
    except Exception as e:
        print(f"[auth] could not read users.json ({e}); re-seeding developer")
        users = []
    if not users:                      # never leave the app with no way in
        users = [make_user(_SEED_USERNAME, _SEED_PASSWORD, ROLE_DEVELOPER)]
        save_users(users)
    return users


def save_users(users: list[User]) -> None:
    """Persist all profiles to users.json. Returns: None."""
    _atomic_write(USERS_PATH, {"users": [u.to_dict() for u in users]})


def find_user(username: str) -> User | None:
    """Look up a profile by name. Returns: User | None."""
    for u in load_users():
        if u.username == username:
            return u
    return None


def verify(username: str, password: str) -> User | None:
    """Check credentials.

    Returns: the User on success, None if the name is unknown OR the password is
    wrong (deliberately the same answer, so we don't leak which one it was).
    """
    user = find_user((username or "").strip())
    if user is None:
        return None
    candidate = _hash_password(password or "", user.salt)
    # compare_digest avoids leaking timing information about the hash.
    if secrets.compare_digest(candidate, user.password_hash):
        return user
    return None


def add_user(username: str, password: str, role: str, max_profiles: int) -> User:
    """Create a new profile.

    Returns: the created User. Raises ValueError if the name is blank/taken or
    the profile cap is reached.
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    if not password:
        raise ValueError("Password cannot be empty.")
    users = load_users()
    if any(u.username == username for u in users):
        raise ValueError(f"A profile named '{username}' already exists.")
    if len(users) >= max_profiles:
        raise ValueError(f"Profile limit reached ({max_profiles}).")
    user = make_user(username, password, role)
    users.append(user)
    save_users(users)
    return user


def delete_user(username: str) -> None:
    """Remove a profile. Returns: None.

    Raises ValueError if it would leave no developer (that would lock you out).
    """
    users = load_users()
    remaining = [u for u in users if u.username != username]
    if not any(u.is_developer for u in remaining):
        raise ValueError("Cannot delete the last developer profile.")
    save_users(remaining)


def change_password(username: str, new_password: str) -> None:
    """Set a new password for a profile. Returns: None. Raises if unknown/blank."""
    if not new_password:
        raise ValueError("Password cannot be empty.")
    users = load_users()
    for i, u in enumerate(users):
        if u.username == username:
            users[i] = make_user(username, new_password, u.role)
            save_users(users)
            return
    raise ValueError(f"No profile named '{username}'.")
