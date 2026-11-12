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
from sqlalchemy import create_engine, String, Integer, BigInteger, Text, Boolean, ForeignKey, select, text
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
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


class ManagedBot(Base):
    __tablename__ = "managed_bots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    bot_username: Mapped[str] = mapped_column(String(64))
    token_encrypted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
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
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    method: Mapped[str] = mapped_column(String(32))
    amount: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


Base.metadata.create_all(engine)

# Telegram user IDs can exceed PostgreSQL's 32-bit INTEGER limit.
# Upgrade existing ID columns safely while preserving all stored rows.
if engine.dialect.name == "postgresql":
    with engine.begin() as conn:
        id_type = conn.execute(text("""
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'users' AND column_name = 'telegram_id'
        """)).scalar()
        if id_type == "integer":
            conn.execute(text("ALTER TABLE managed_bots DROP CONSTRAINT IF EXISTS managed_bots_owner_id_fkey"))
            conn.execute(text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_user_id_fkey"))
            conn.execute(text("ALTER TABLE payment_requests DROP CONSTRAINT IF EXISTS payment_requests_user_id_fkey"))
            conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT"))
            conn.execute(text("ALTER TABLE managed_bots ALTER COLUMN owner_id TYPE BIGINT"))
            conn.execute(text("ALTER TABLE subscriptions ALTER COLUMN user_id TYPE BIGINT"))
            conn.execute(text("ALTER TABLE payment_requests ALTER COLUMN user_id TYPE BIGINT"))
            conn.execute(text("ALTER TABLE managed_bots ADD CONSTRAINT managed_bots_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES users (telegram_id)"))
            conn.execute(text("ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (telegram_id)"))
            conn.execute(text("ALTER TABLE payment_requests ADD CONSTRAINT payment_requests_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (telegram_id)"))
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
    enabled = db.get(PlatformSetting, f"payment_{payload.method}_enabled")
    if not enabled or enabled.value.lower() != "true":
        raise HTTPException(400, "طريقة الدفع غير متاحة حالياً")
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


class PaymentMethodConfig(BaseModel):
    enabled: bool = False
    instructions: str = Field(default="", max_length=1500)


class PaymentMethodsUpdate(BaseModel):
    methods: dict[str, PaymentMethodConfig]


PAYMENT_METHODS = {
    "stars": "⭐ نجوم تيليجرام",
    "asiacell": "📱 آسياسيل",
    "bank_transfer": "🏦 التحويل البنكي",
    "card": "💳 الكارت",
}


def read_payment_methods(db: Session):
    values = {row.key: row.value for row in db.scalars(select(PlatformSetting)).all()}
    return {
        key: {
            "label": label,
            "enabled": values.get(f"payment_{key}_enabled", "false").lower() == "true",
            "instructions": values.get(f"payment_{key}_instructions", ""),
        }
        for key, label in PAYMENT_METHODS.items()
    }


@app.get("/api/payment-methods")
def public_payment_methods(db: Session = Depends(get_db)):
    methods = read_payment_methods(db)
    return {
        "methods": {key: {"label": item["label"], "enabled": item["enabled"], "instructions": item["instructions"]}
                    for key, item in methods.items() if item["enabled"]},
        "plan": {
            "name": db.get(PlatformSetting, "plan_name").value if db.get(PlatformSetting, "plan_name") else "Premium",
            "price": db.get(PlatformSetting, "plan_price").value if db.get(PlatformSetting, "plan_price") else "0",
        },
    }


@app.get("/api/admin/payment-methods")
def admin_get_payment_methods(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    return {"methods": read_payment_methods(db)}


@app.put("/api/admin/payment-methods")
def admin_update_payment_methods(payload: PaymentMethodsUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    unknown = set(payload.methods) - set(PAYMENT_METHODS)
    if unknown:
        raise HTTPException(400, "Unknown payment method")
    for key, config in payload.methods.items():
        for suffix, value in (("enabled", str(config.enabled).lower()), ("instructions", config.instructions.strip())):
            setting_key = f"payment_{key}_{suffix}"
            row = db.get(PlatformSetting, setting_key)
            if row is None:
                db.add(PlatformSetting(key=setting_key, value=value))
            else:
                row.value = value
    db.commit()
    return {"saved": True}


class ContactMessage(BaseModel):
    message: str = Field(min_length=1, max_length=3000)


@app.post("/api/message")
async def receive_contact_message(
    payload: ContactMessage,
    user: User = Depends(current_user),
):
    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "Message cannot be empty")
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Contact bot is not configured")

    full_name = " ".join(part for part in [user.first_name] if part) or "مستخدم تيليجرام"
    username = f"@{user.username}" if user.username else "بدون يوزر"
    notice = (
        "📩 رسالة جديدة من منصة Bot Center\n"
        f"👤 الاسم: {full_name}\n"
        f"🔗 الحساب: {username}\n"
        f"🆔 ID: {user.telegram_id}\n\n"
        f"📝 الرسالة:\n{message}"
    )
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.owner_id, "text": notice},
            )
            result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram delivery failed"))
    except Exception:
        raise HTTPException(
            502,
            "تعذر إيصال الرسالة. تأكد من أن المطوّر بدأ محادثة مع البوت.",
        )
    return {"ok": True, "message": "تم إرسال رسالتك بنجاح"}
