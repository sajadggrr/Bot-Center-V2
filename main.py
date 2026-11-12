import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, String, Integer, Text, Boolean, ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session


class Settings(BaseSettings):
    app_name: str = "Bot Center"
    app_env: str = "development"
    secret_key: str = "CHANGE_ME"
    owner_id: int = 1916071119
    database_url: str = "sqlite:///./bot_center.db"
    bot_token_encryption_key: str = ""
    telegram_bot_username: str = "SS_FBot"
    telegram_bot_token: str = ""
    webapp_url: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


class ManagedBot(Base):
    __tablename__ = "managed_bots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), index=True)
    bot_username: Mapped[str] = mapped_column(String(64))
    token_encrypted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), index=True)
    plan_name: Mapped[str] = mapped_column(String(80), default="Free")
    status: Mapped[str] = mapped_column(String(24), default="active")
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), index=True)
    method: Mapped[str] = mapped_column(String(32))
    amount: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


Base.metadata.create_all(engine)
app = FastAPI(title=settings.app_name, version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env == "development" else [settings.webapp_url.rstrip("/")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_telegram_init_data(init_data: str) -> dict:
    if not settings.secret_key or settings.secret_key == "CHANGE_ME":
        raise HTTPException(503, "Server security is not configured")
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot token is not configured")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Missing Telegram signature")
    auth_date = int(values.get("auth_date", "0"))
    if auth_date < int(time.time()) - 86400:
        raise HTTPException(401, "Telegram login data expired")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(401, "Invalid Telegram signature")
    try:
        return json.loads(values["user"])
    except (KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram user data")


def current_user(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
    db: Session = Depends(get_db),
) -> User:
    if not x_telegram_init_data:
        raise HTTPException(401, "Open this app from Telegram")
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    user_id = int(tg_user["id"])
    user = db.get(User, user_id)
    if user is None:
        user = User(
            telegram_id=user_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    if user.is_banned:
        raise HTTPException(403, "Account is blocked")
    return user


def encryptor() -> Fernet:
    if not settings.bot_token_encryption_key:
        raise HTTPException(503, "Token encryption key is not configured")
    try:
        return Fernet(settings.bot_token_encryption_key.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "Invalid token encryption key")


class BotCreate(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class PaymentCreate(BaseModel):
    method: str = Field(pattern="^(stars|asiacell|bank_transfer|card)$")
    amount: str = Field(min_length=1, max_length=32)
    reference: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"name": settings.app_name, "status": "online", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "is_owner": user.telegram_id == settings.owner_id,
    }


@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    bots = db.scalars(select(ManagedBot).where(ManagedBot.owner_id == user.telegram_id)).all()
    subscription = db.scalar(
        select(Subscription).where(Subscription.user_id == user.telegram_id).order_by(Subscription.id.desc())
    )
    return {
        "bots_count": len(bots),
        "subscription": {
            "plan": subscription.plan_name,
            "status": subscription.status,
            "expires_at": subscription.expires_at,
        } if subscription else {"plan": "Free", "status": "active", "expires_at": None},
    }


@app.post("/api/bots")
def add_bot(payload: BotCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cipher = encryptor()
    # Validate token with Telegram before saving it.
    import httpx
    try:
        response = httpx.get(f"https://api.telegram.org/bot{payload.token}/getMe", timeout=12)
        data = response.json()
    except Exception:
        raise HTTPException(502, "Could not contact Telegram")
    if not data.get("ok"):
        raise HTTPException(400, "Telegram rejected this bot token")
    username = data["result"].get("username") or "unknown"
    exists = db.scalar(select(ManagedBot).where(ManagedBot.bot_username == username))
    if exists:
        raise HTTPException(409, "This bot is already connected")
    record = ManagedBot(
        owner_id=user.telegram_id,
        bot_username=username,
        token_encrypted=cipher.encrypt(payload.token.encode()).decode(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id": record.id, "username": username}


@app.get("/api/bots")
def list_bots(user: User = Depends(current_user), db: Session = Depends(get_db)):
    bots = db.scalars(select(ManagedBot).where(ManagedBot.owner_id == user.telegram_id)).all()
    return [{"id": b.id, "username": b.bot_username, "created_at": b.created_at} for b in bots]


@app.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    bot = db.scalar(
        select(ManagedBot).where(
            ManagedBot.id == bot_id,
            ManagedBot.owner_id == user.telegram_id,
        )
    )
    if not bot:
        raise HTTPException(404, "Bot not found")
    db.delete(bot)
    db.commit()
    return {"deleted": True}


@app.post("/api/payments")
def create_payment(payload: PaymentCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    request = PaymentRequest(
        user_id=user.telegram_id,
        method=payload.method,
        amount=payload.amount,
        reference=payload.reference,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return {"id": request.id, "status": request.status}


@app.get("/api/admin/stats")
def admin_stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.telegram_id != settings.owner_id:
        raise HTTPException(403, "Developer access only")
    return {
        "users": len(db.scalars(select(User)).all()),
        "bots": len(db.scalars(select(ManagedBot)).all()),
        "pending_payments": len(db.scalars(select(PaymentRequest).where(PaymentRequest.status == "pending")).all()),
    }
