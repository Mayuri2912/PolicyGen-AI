"""Individual accounts; no open registration or browser-stored passwords."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.core.identity import principal
from app.database.session import get_db, session_scope
from app.models.entities import Account, AccountSession, new_id, utcnow

router = APIRouter(prefix="/auth", tags=["accounts"])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class Setup(Credentials):
    name: str = Field(min_length=2, max_length=100)


class NewAccount(Setup):
    role: Literal["admin", "editor", "reviewer", "viewer"] = "viewer"
    new_workspace: bool = False


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 600_000
    ).hex()
    return f"{salt}:{value}"


def account_info(account):
    return {
        "id": account.id,
        "name": account.name,
        "email": account.email,
        "role": account.role,
        "workspace": account.workspace_id,
    }


def authenticate(token: str):
    if not token:
        return None
    with session_scope() as db:
        row = db.scalar(
            select(AccountSession).where(
                AccountSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        )
        if row is None:
            return None
        expiry = (
            row.expires_at.replace(tzinfo=timezone.utc)
            if row.expires_at.tzinfo is None
            else row.expires_at
        )
        if expiry <= utcnow():
            return None
        account = db.get(Account, row.account_id)
        return account_info(account) if account and account.active else None


def require_role(*roles):
    user = principal.get()
    if not user or user["role"] not in roles:
        raise AppError(
            "An authenticated account with the required permission is needed.",
            status_code=403,
            code="forbidden",
        )
    return user


def issue_session(db, account):
    db.execute(delete(AccountSession).where(AccountSession.expires_at < utcnow()))
    token = secrets.token_urlsafe(48)
    db.add(
        AccountSession(
            account_id=account.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(hours=8),
        )
    )
    db.commit()
    return {"token": token, "user": account_info(account)}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@router.post('/password')
def change_password(body: PasswordChange, db: Session = Depends(get_db)):
    user = require_role('admin', 'editor', 'reviewer', 'viewer')
    account = db.get(Account, user['id'])
    if not hmac.compare_digest(password_hash(body.current_password, account.password_hash.split(':')[0]), account.password_hash):
        raise AppError('Current password was not accepted.', status_code=403)
    account.password_hash = password_hash(body.new_password)
    db.execute(delete(AccountSession).where(AccountSession.account_id == account.id))
    return issue_session(db, account)


@router.get("/status")
def status(db: Session = Depends(get_db)):
    if get_settings().public_access:
        return {"accounts_enabled": False, "setup_available": False, "public_access": True}
    return {
        "accounts_enabled": bool(db.scalar(select(func.count(Account.id)))),
        "setup_available": bool(get_settings().api_access_token),
    }


@router.post("/setup")
def setup(body: Setup, request: Request, db: Session = Depends(get_db)):
    # Shared administrator token is accepted only for the initial, explicit setup.
    token = get_settings().api_access_token
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not token or not hmac.compare_digest(token, supplied):
        raise AppError(
            "Use the existing administrator access token to set up the first account.",
            status_code=403,
        )
    # A fixed id makes concurrent initial setup attempts conflict safely.
    if db.scalar(select(func.count(Account.id))):
        raise ConflictError(
            "Accounts are already configured. Ask an administrator to add you."
        )
    account = Account(
        id="initial-administrator",
        email=body.email.strip().lower(),
        name=body.name.strip(),
        password_hash=password_hash(body.password),
        role="admin",
        workspace_id="default",
    )
    db.add(account)
    db.flush()
    return issue_session(db, account)


@router.post("/login")
def login(body: Credentials, db: Session = Depends(get_db)):
    account = db.scalar(
        select(Account).where(Account.email == body.email.strip().lower())
    )
    salt = account.password_hash.split(":")[0] if account else "00" * 16
    candidate = password_hash(body.password, salt)
    if (
        not account
        or not account.active
        or not hmac.compare_digest(candidate, account.password_hash)
    ):
        raise AppError(
            "Email or password was not accepted.", status_code=401, code="unauthorized"
        )
    return issue_session(db, account)


@router.get("/me")
def me():
    return {"user": principal.get()}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    row = db.scalar(
        select(AccountSession).where(
            AccountSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if row:
        db.delete(row)
        db.commit()
    return {"logged_out": True}


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)):
    user = require_role("admin")
    return {
        "accounts": [
            account_info(a) | {"active": a.active}
            for a in db.scalars(
                select(Account).where(Account.workspace_id == user["workspace"])
            )
        ]
    }


@router.post("/accounts")
def create_account(body: NewAccount, db: Session = Depends(get_db)):
    user = require_role("admin")
    if db.scalar(select(Account).where(Account.email == body.email.strip().lower())):
        raise ConflictError("This email is already registered.")
    # A new workspace starts empty. Existing records remain in the original workspace.
    account = Account(
        email=body.email.strip().lower(),
        name=body.name.strip(),
        password_hash=password_hash(body.password),
        role=body.role,
        workspace_id=new_id() if body.new_workspace else user["workspace"],
    )
    db.add(account)
    db.commit()
    return {"user": account_info(account)}


@router.post("/accounts/{account_id}/disable")
def disable(account_id: str, db: Session = Depends(get_db)):
    user = require_role("admin")
    account = db.get(Account, account_id)
    if (
        not account
        or account.workspace_id != user["workspace"]
        or account.id == user["id"]
    ):
        raise AppError("Cannot disable this account.", status_code=403)
    account.active = False
    db.commit()
    return {"disabled": True}
