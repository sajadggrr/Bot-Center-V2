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
database_url = settings.database_url
# Neon supplies a standard postgresql:// URL; use the psycopg 3 SQLAlchemy driver.
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
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



class PlatformSetting(Base):
    __tablename__ = 'platform_settings'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default='')

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
    allow_origins=["*"] if settings.app_env == "development" else ["https://sajadggrr.github.io"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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
        "banned_users": len(db.scalars(select(User).where(User.is_banned.is_(True))).all()),
    }

class PlanUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    price: str = Field(min_length=1, max_length=32)
    duration_days: int = Field(ge=1, le=3650)

class PaymentDecision(BaseModel):
    approve: bool
    plan_name: str = Field(default='Premium', min_length=1, max_length=80)
    duration_days: int = Field(default=30, ge=1, le=3650)

class BanUpdate(BaseModel):
    banned: bool

def require_owner(user: User):
    if user.telegram_id != settings.owner_id:
        raise HTTPException(403, 'Developer access only')

@app.get('/api/admin/users')
def admin_users(user: User = Depends(current_user), db: Session = Depends(get_db), limit: int = 50, offset: int = 0):
    require_owner(user)
    limit = max(1, min(limit, 100))
    rows = db.scalars(select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)).all()
    return [{'id': u.telegram_id, 'username': u.username, 'first_name': u.first_name, 'is_banned': u.is_banned, 'created_at': u.created_at} for u in rows]

@app.post('/api/admin/users/{telegram_id}/ban')
def admin_ban_user(telegram_id: int, payload: BanUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    if telegram_id == settings.owner_id: raise HTTPException(400, 'Cannot ban platform owner')
    target = db.get(User, telegram_id)
    if not target: raise HTTPException(404, 'User not found')
    target.is_banned = payload.banned
    db.commit()
    return {'user_id': telegram_id, 'is_banned': target.is_banned}

@app.get('/api/admin/payments')
def admin_payments(user: User = Depends(current_user), db: Session = Depends(get_db), status: str = 'pending'):
    require_owner(user)
    if status not in {'pending', 'approved', 'rejected', 'all'}: raise HTTPException(400, 'Invalid status')
    query = select(PaymentRequest).order_by(PaymentRequest.created_at.desc()).limit(100)
    if status != 'all': query = query.where(PaymentRequest.status == status)
    rows = db.scalars(query).all()
    return [{'id': p.id, 'user_id': p.user_id, 'method': p.method, 'amount': p.amount, 'reference': p.reference, 'status': p.status, 'created_at': p.created_at} for p in rows]

@app.post('/api/admin/payments/{payment_id}/decision')
def admin_payment_decision(payment_id: int, payload: PaymentDecision, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    payment = db.get(PaymentRequest, payment_id)
    if not payment: raise HTTPException(404, 'Payment not found')
    if payment.status != 'pending': raise HTTPException(409, 'Already reviewed')
    if payload.approve:
        payment.status = 'approved'
        db.add(Subscription(user_id=payment.user_id, plan_name=payload.plan_name, status='active', expires_at=int(time.time()) + payload.duration_days * 86400))
    else: payment.status = 'rejected'
    db.commit()
    return {'id': payment.id, 'status': payment.status}

@app.put('/api/admin/plan')
def admin_update_plan(payload: PlanUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    for key, value in {'plan_name': payload.name, 'plan_price': payload.price, 'plan_duration_days': str(payload.duration_days)}.items():
        row = db.get(PlatformSetting, key)
        if row is None: db.add(PlatformSetting(key=key, value=value))
        else: row.value = value
    db.commit()
    return {'saved': True}

@app.get('/api/admin/plan')
def admin_get_plan(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    rows = db.scalars(select(PlatformSetting)).all()
    values = {r.key: r.value for r in rows}
    return {'name': values.get('plan_name', 'Premium'), 'price': values.get('plan_price', '0'), 'duration_days': int(values.get('plan_duration_days', '30'))}

