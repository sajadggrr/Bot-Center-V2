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
    cron_secret: str = ""
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
    reminder_3d_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expired_notice_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)



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
# Add subscription reminder flags to existing databases.
if engine.dialect.name == "postgresql":
    with engine.begin() as conn:
        columns = {
            row[0] for row in conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'subscriptions'
            """)).all()
        }
        if "reminder_3d_sent" not in columns:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN reminder_3d_sent BOOLEAN NOT NULL DEFAULT FALSE"))
        if "expired_notice_sent" not in columns:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN expired_notice_sent BOOLEAN NOT NULL DEFAULT FALSE"))
elif engine.dialect.name == "sqlite":
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(subscriptions)")).all()}
        if "reminder_3d_sent" not in columns:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN reminder_3d_sent BOOLEAN NOT NULL DEFAULT 0"))
        if "expired_notice_sent" not in columns:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN expired_notice_sent BOOLEAN NOT NULL DEFAULT 0"))


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
    exists = db.scalar(select(ManagedBot).where(ManagedBot.owner_id == user.telegram_id, ManagedBot.bot_username == username))
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
    return [{"id": b.id, "username": b.bot_username, "created_at": b.created_at, "mode": "external_token", "status": "connected_only"} for b in bots]


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
async def create_payment(payload: PaymentCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
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

    method_label = PAYMENT_METHODS.get(payload.method, payload.method)
    plan_name_row = db.get(PlatformSetting, "plan_name")
    plan_price_row = db.get(PlatformSetting, "plan_price")
    duration_row = db.get(PlatformSetting, "plan_duration_days")
    plan_name = plan_name_row.value if plan_name_row else "Premium"
    plan_price = plan_price_row.value if plan_price_row else "0"
    duration_days = duration_row.value if duration_row else "30"
    full_name = " ".join(part for part in [user.first_name] if part) or "مستخدم تيليجرام"
    username = f"@{user.username}" if user.username else "بدون يوزر"
    notice = (
        "🔔 طلب اشتراك جديد - Bot Center\n\n"
        f"🧾 رقم الطلب: #{request.id}\n"
        f"👤 الاسم: {full_name}\n"
        f"🔗 الحساب: {username}\n"
        f"🆔 الآيدي: {user.telegram_id}\n\n"
        f"💎 الخطة: {plan_name}\n"
        f"💰 سعر الخطة المحدد: {plan_price}\n"
        f"📅 مدة الاشتراك: {duration_days} يوم\n"
        f"💳 طريقة الدفع: {method_label}\n"
        f"💵 المبلغ المدخل: {payload.amount}\n"
        f"🔖 رقم/مرجع العملية: {payload.reference or 'غير مرفق'}\n\n"
        "⏳ الحالة: بانتظار مراجعة المطوّر"
    )
    notification_sent = False
    if settings.telegram_bot_token:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": settings.owner_id, "text": notice},
                )
                result = response.json()
                notification_sent = bool(result.get("ok"))
        except Exception:
            notification_sent = False
    return {"id": request.id, "status": request.status, "notification_sent": notification_sent}


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
async def admin_payment_decision(payment_id: int, payload: PaymentDecision, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    payment = db.get(PaymentRequest, payment_id)
    if not payment:
        raise HTTPException(404, 'Payment not found')
    if payment.status != 'pending':
        raise HTTPException(409, 'Already reviewed')

    now = int(time.time())
    expires_at = None
    if payload.approve:
        payment.status = 'approved'
        active_subscriptions = db.scalars(
            select(Subscription).where(
                Subscription.user_id == payment.user_id,
                Subscription.status == 'active',
            )
        ).all()
        future_expiries = [
            item.expires_at for item in active_subscriptions
            if item.expires_at and item.expires_at > now
        ]
        subscription_start = max(future_expiries, default=now)
        # Mark previous active records as superseded so old subscriptions
        # cannot trigger reminders after a renewal has been approved.
        for item in active_subscriptions:
            item.status = 'renewed'
        expires_at = subscription_start + payload.duration_days * 86400
        db.add(Subscription(
            user_id=payment.user_id,
            plan_name=payload.plan_name,
            status='active',
            expires_at=expires_at,
        ))
    else:
        payment.status = 'rejected'
    db.commit()

    notification_sent = False
    notification_error = None
    if settings.telegram_bot_token:
        import httpx
        if payload.approve:
            from datetime import datetime, timezone
            expiry_text = datetime.fromtimestamp(expires_at, timezone.utc).strftime('%Y-%m-%d')
            user_notice = (
                "🎉 تم تفعيل اشتراكك في Bot Center بنجاح!\n\n"
                f"💎 الخطة: {payload.plan_name}\n"
                f"📅 مدة التجديد المضافة: {payload.duration_days} يوم\n"
                f"🗓️ تاريخ الانتهاء الجديد: {expiry_text}\n\n"
                "شكراً لثقتك بنا ❤️"
            )
        else:
            user_notice = (
                "❌ نعتذر، تم رفض طلب الاشتراك في Bot Center.\n\n"
                f"🧾 رقم الطلب: #{payment.id}\n"
                "يمكنك مراجعة بيانات الدفع والتواصل مع الدعم إذا كنت تعتقد أن هذا خطأ."
            )
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": payment.user_id, "text": user_notice},
                )
                result = response.json()
                notification_sent = bool(result.get('ok'))
                if not notification_sent:
                    notification_error = result.get('description', 'Telegram delivery failed')
        except Exception as exc:
            notification_error = str(exc)[:300]

    return {
        'id': payment.id,
        'status': payment.status,
        'notification_sent': notification_sent,
        'notification_error': notification_error,
    }

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


@app.get("/api/cron/subscription-reminders")
async def subscription_reminders(
    authorization: str = Header(default="", alias="Authorization"),
    db: Session = Depends(get_db),
):
    if not settings.cron_secret:
        raise HTTPException(503, "CRON_SECRET is not configured")
    expected = f"Bearer {settings.cron_secret}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Unauthorized")
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot is not configured")

    now = int(time.time())
    candidates = db.scalars(
        select(Subscription).where(
            Subscription.status.in_(["active", "expired"]),
            Subscription.expires_at.is_not(None),
        )
    ).all()
    sent_3d = 0
    sent_expired = 0
    failed = 0

    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        for subscription in candidates:
            expiry = subscription.expires_at
            if expiry is None:
                continue
            if expiry <= now:
                if subscription.expired_notice_sent:
                    continue
                notice = (
                    "⛔ انتهى اشتراكك في Bot Center\n\n"
                    f"💎 الخطة: {subscription.plan_name}\n"
                    "يمكنك تقديم طلب تجديد من داخل المنصة."
                )
                subscription.status = "expired"
                endpoint_flag = "expired_notice_sent"
            elif expiry <= now + 3 * 86400 and not subscription.reminder_3d_sent:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                expiry_text = datetime.fromtimestamp(expiry, ZoneInfo("Asia/Baghdad")).strftime("%Y-%m-%d")
                notice = (
                    "🔔 تذكير: اشتراكك في Bot Center سينتهي خلال 3 أيام\n\n"
                    f"💎 الخطة: {subscription.plan_name}\n"
                    f"🗓️ تاريخ الانتهاء: {expiry_text}\n"
                    "جدد اشتراكك قبل انتهائه للاستمرار باستخدام المزايا."
                )
                endpoint_flag = "reminder_3d_sent"
            else:
                continue

            try:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": subscription.user_id, "text": notice},
                )
                result = response.json()
                if result.get("ok"):
                    setattr(subscription, endpoint_flag, True)
                    if endpoint_flag == "reminder_3d_sent":
                        sent_3d += 1
                    else:
                        sent_expired += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        db.commit()

    return {
        "ok": True,
        "reminders_sent": sent_3d,
        "expired_notices_sent": sent_expired,
        "failed": failed,
    }


# Telegram in-chat developer control panel.
def telegram_webhook_secret() -> str:
    return hashlib.sha256(settings.secret_key.encode()).hexdigest()


async def telegram_call(method: str, payload: dict):
    import httpx
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot token is not configured")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}",
            json=payload,
        )
        data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))
    return data.get("result")


def owner_panel_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🤖 إدارة البوتات", "callback_data": "bc:bots"},
             {"text": "📊 الإحصائيات", "callback_data": "bc:stats"}],
            [{"text": "➕ إضافة بوت", "callback_data": "bc:add"},
             {"text": "💳 طلبات الاشتراك", "callback_data": "bc:payments"}],
            [{"text": "👥 المستخدمون", "callback_data": "bc:users"},
             {"text": "💎 إعداد الاشتراك", "callback_data": "bc:plan"}],
        ]
    }


async def telegram_send(chat_id: int, text_value: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text_value}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_call("sendMessage", payload)


async def telegram_show_bots(chat_id: int, owner_id: int, db: Session):
    rows = db.scalars(
        select(ManagedBot).where(ManagedBot.owner_id == owner_id).order_by(ManagedBot.id.desc())
    ).all()
    if not rows:
        await telegram_send(chat_id, "🤖 ما عندك بوتات مضافة حالياً.", owner_panel_keyboard())
        return
    keyboard = [[{"text": f"🗑 حذف @{b.bot_username}", "callback_data": f"bc:del:{b.id}"}] for b in rows[:30]]
    keyboard.append([{"text": "➕ إضافة بوت", "callback_data": "bc:add"}])
    keyboard.append([{"text": "⬅️ القائمة الرئيسية", "callback_data": "bc:home"}])
    lines = ["🤖 بوتاتك المسجلة في Bot Center:", ""]
    lines.extend([f"• @{b.bot_username} — رقم {b.id}" for b in rows[:30]])
    await telegram_send(chat_id, "\n".join(lines), {"inline_keyboard": keyboard})


async def telegram_show_users(chat_id: int, db: Session):
    rows = db.scalars(select(User).order_by(User.created_at.desc()).limit(15)).all()
    if not rows:
        await telegram_send(chat_id, "لا يوجد مستخدمون مسجلون.", owner_panel_keyboard())
        return
    keyboard = []
    for item in rows:
        label = ("🚫 " if item.is_banned else "👤 ") + (item.first_name or str(item.telegram_id))
        keyboard.append([{"text": label[:60], "callback_data": f"bc:user:{item.telegram_id}"}])
    keyboard.append([{"text": "⬅️ القائمة الرئيسية", "callback_data": "bc:home"}])
    await telegram_send(chat_id, "👥 آخر 15 مستخدماً. اختر مستخدماً لإدارة الحظر:", {"inline_keyboard": keyboard})


async def telegram_show_payments(chat_id: int, db: Session):
    rows = db.scalars(
        select(PaymentRequest).where(PaymentRequest.status == "pending")
        .order_by(PaymentRequest.created_at.desc()).limit(15)
    ).all()
    if not rows:
        await telegram_send(chat_id, "✅ لا توجد طلبات دفع معلّقة.", owner_panel_keyboard())
        return
    keyboard = []
    for item in rows:
        keyboard.append([
            {"text": f"✅ قبول #{item.id}", "callback_data": f"bc:pay:{item.id}:yes"},
            {"text": f"❌ رفض #{item.id}", "callback_data": f"bc:pay:{item.id}:no"},
        ])
    keyboard.append([{"text": "⬅️ القائمة الرئيسية", "callback_data": "bc:home"}])
    lines = ["💳 طلبات الدفع المعلّقة:", ""]
    lines.extend([f"#{p.id} | المستخدم {p.user_id} | {p.amount} | {p.method}" for p in rows])
    await telegram_send(chat_id, "\n".join(lines), {"inline_keyboard": keyboard})


async def process_telegram_update(update: dict):
    message = update.get("message") or {}
    callback = update.get("callback_query")
    sender = (callback or {}).get("from") or message.get("from") or {}
    if not sender.get("id"):
        return
    sender_id = int(sender["id"])
    chat_id = int(((callback or {}).get("message") or message).get("chat", {}).get("id", sender_id))
    db = SessionLocal()
    try:
        user = db.get(User, sender_id)
        if user is None:
            user = User(
                telegram_id=sender_id,
                username=sender.get("username"),
                first_name=sender.get("first_name"),
            )
            db.add(user)
            db.commit()
        elif user.is_banned and sender_id != settings.owner_id:
            await telegram_send(chat_id, "⛔ حسابك محظور من استخدام Bot Center.")
            return

        if callback:
            callback_id = callback.get("id")
            data = callback.get("data", "")
            try:
                await telegram_call("answerCallbackQuery", {"callback_query_id": callback_id})
            except Exception:
                pass
            if sender_id != settings.owner_id:
                await telegram_send(chat_id, "هذه اللوحة خاصة بالمطوّر.")
                return
            if data == "bc:home":
                await telegram_send(chat_id, "🛡️ لوحة مطوّر Bot Center\nاختر القسم المطلوب:", owner_panel_keyboard())
            elif data == "bc:bots":
                await telegram_show_bots(chat_id, sender_id, db)
            elif data == "bc:add":
                key = f"tg_state_{sender_id}"
                row = db.get(PlatformSetting, key)
                if row is None:
                    db.add(PlatformSetting(key=key, value="add_bot"))
                else:
                    row.value = "add_bot"
                db.commit()
                await telegram_send(chat_id, "➕ أرسل الآن توكن البوت من BotFather.\nسيتم التحقق منه وحفظه مشفّراً. لا ترسل أي رمز تحقق أو كلمة مرور.")
            elif data.startswith("bc:del:"):
                bot_id = int(data.split(":")[2])
                bot = db.scalar(select(ManagedBot).where(
                    ManagedBot.id == bot_id, ManagedBot.owner_id == sender_id
                ))
                if not bot:
                    await telegram_send(chat_id, "لم يتم العثور على البوت.")
                else:
                    name = bot.bot_username
                    db.delete(bot)
                    db.commit()
                    await telegram_send(chat_id, f"🗑 تم حذف @{name} من قائمة Bot Center.", owner_panel_keyboard())
            elif data == "bc:stats":
                stats = {
                    "users": len(db.scalars(select(User)).all()),
                    "bots": len(db.scalars(select(ManagedBot)).all()),
                    "payments": len(db.scalars(select(PaymentRequest).where(PaymentRequest.status == "pending")).all()),
                    "banned": len(db.scalars(select(User).where(User.is_banned.is_(True))).all()),
                }
                await telegram_send(chat_id, f"📊 إحصائيات Bot Center\n\n👥 المستخدمون: {stats['users']}\n🤖 البوتات: {stats['bots']}\n💳 طلبات الدفع المعلّقة: {stats['payments']}\n🚫 المحظورون: {stats['banned']}", owner_panel_keyboard())
            elif data == "bc:users":
                await telegram_show_users(chat_id, db)
            elif data.startswith("bc:user:"):
                target_id = int(data.split(":")[2])
                target = db.get(User, target_id)
                if not target:
                    await telegram_send(chat_id, "المستخدم غير موجود.")
                elif target_id == settings.owner_id:
                    await telegram_send(chat_id, "لا يمكن حظر حساب المطوّر.")
                else:
                    next_ban = not target.is_banned
                    await telegram_send(
                        chat_id,
                        f"👤 المستخدم: {target.first_name or 'بدون اسم'}\n🆔 {target_id}\nالحالة: {'محظور' if target.is_banned else 'نشط'}",
                        {"inline_keyboard": [[{"text": "🚫 حظر" if next_ban else "✅ إلغاء الحظر",
                                               "callback_data": f"bc:ban:{target_id}:{1 if next_ban else 0}"}],
                                             [{"text": "⬅️ المستخدمون", "callback_data": "bc:users"}]]}
                    )
            elif data.startswith("bc:ban:"):
                _, _, raw_id, raw_state = data.split(":")
                target_id, banned = int(raw_id), raw_state == "1"
                target = db.get(User, target_id)
                if target and target_id != settings.owner_id:
                    target.is_banned = banned
                    db.commit()
                    await telegram_send(chat_id, "تم تحديث حالة المستخدم: " + ("محظور 🚫" if banned else "نشط ✅"), owner_panel_keyboard())
            elif data == "bc:payments":
                await telegram_show_payments(chat_id, db)
            elif data.startswith("bc:pay:"):
                _, _, raw_id, decision = data.split(":")
                payment = db.get(PaymentRequest, int(raw_id))
                if not payment or payment.status != "pending":
                    await telegram_send(chat_id, "هذا الطلب غير موجود أو تمت مراجعته مسبقاً.")
                else:
                    if decision == "yes":
                        now = int(time.time())
                        active = db.scalars(select(Subscription).where(
                            Subscription.user_id == payment.user_id,
                            Subscription.status == "active"
                        )).all()
                        future = [s.expires_at for s in active if s.expires_at and s.expires_at > now]
                        start_at = max(future, default=now)
                        plan_row = db.get(PlatformSetting, "plan_name")
                        days_row = db.get(PlatformSetting, "plan_duration_days")
                        plan_name = plan_row.value if plan_row else "Premium"
                        days = int(days_row.value) if days_row else 30
                        for old in active:
                            old.status = "renewed"
                        db.add(Subscription(user_id=payment.user_id, plan_name=plan_name,
                                            status="active", expires_at=start_at + days * 86400))
                        payment.status = "approved"
                        user_text = f"🎉 تم تفعيل اشتراكك في Bot Center!\n💎 الخطة: {plan_name}\n📅 المدة: {days} يوم"
                    else:
                        payment.status = "rejected"
                        user_text = f"❌ تم رفض طلب الاشتراك رقم #{payment.id}."
                    db.commit()
                    try:
                        await telegram_send(payment.user_id, user_text)
                    except Exception:
                        pass
                    await telegram_send(chat_id, f"تمت مراجعة الطلب #{payment.id}: {'مقبول ✅' if decision == 'yes' else 'مرفوض ❌'}", owner_panel_keyboard())
            elif data == "bc:plan":
                plan = db.get(PlatformSetting, "plan_name")
                price = db.get(PlatformSetting, "plan_price")
                days = db.get(PlatformSetting, "plan_duration_days")
                await telegram_send(chat_id, f"💎 إعداد الاشتراك الحالي\nالخطة: {plan.value if plan else 'Premium'}\nالسعر: {price.value if price else '0'}\nالمدة: {days.value if days else '30'} يوم\n\nتعديل التفاصيل متاح حالياً من لوحة المطوّر في الموقع.", owner_panel_keyboard())
            return

        text_value = (message.get("text") or "").strip()
        if not text_value:
            return
        if text_value.startswith("/start") or text_value.startswith("/panel"):
            await telegram_send(chat_id, "أهلاً بك في Bot Center 🤖\nلإدارة بوتاتك واشتراكاتك افتح تطبيق المنصة من زر القائمة.")
            return

        state_key = f"tg_state_{sender_id}"
        state = db.get(PlatformSetting, state_key)
        if sender_id == settings.owner_id and text_value == "/cancel":
            if state:
                db.delete(state)
                db.commit()
            await telegram_send(chat_id, "تم إلغاء العملية.", owner_panel_keyboard())
            try:
                await telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": message.get("message_id")})
            except Exception:
                pass
            return
        if sender_id == settings.owner_id and state and state.value == "add_bot":
            token = text_value
            if len(token) < 20 or len(token) > 256 or ":" not in token:
                await telegram_send(chat_id, "❌ صيغة التوكن غير صحيحة. أرسل توكن BotFather الصحيح أو /cancel للإلغاء.")
                try:
                    await telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": message.get("message_id")})
                except Exception:
                    pass
                return
            try:
                import httpx
                async with httpx.AsyncClient(timeout=12) as client:
                    response = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    result = response.json()
                if not result.get("ok"):
                    await telegram_send(chat_id, "❌ تيليجرام رفض التوكن. تأكد منه ثم أرسله مرة أخرى.")
                    try:
                        await telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": message.get("message_id")})
                    except Exception:
                        pass
                    return
                username = result["result"].get("username") or "unknown"
                existing = db.scalar(select(ManagedBot).where(
                    ManagedBot.owner_id == sender_id,
                    ManagedBot.bot_username == username
                ))
                if existing:
                    await telegram_send(chat_id, f"هذا البوت @{username} مضاف مسبقاً.")
                else:
                    encrypted = encryptor().encrypt(token.encode()).decode()
                    db.add(ManagedBot(owner_id=sender_id, bot_username=username, token_encrypted=encrypted))
                    await telegram_send(chat_id, f"✅ تمت إضافة @{username} وحفظ التوكن بصورة مشفّرة.")
                db.delete(state)
                db.commit()
                try:
                    await telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": message.get("message_id")})
                except Exception:
                    pass
            except Exception:
                await telegram_send(chat_id, "تعذر التحقق من التوكن حالياً. حاول مرة أخرى لاحقاً.")
            return
    finally:
        db.close()


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    update: dict,
    secret_token: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
):
    expected = telegram_webhook_secret()
    if not hmac.compare_digest(secret_token, expected):
        raise HTTPException(401, "Unauthorized")
    try:
        await process_telegram_update(update)
    except Exception:
        # Acknowledge the update to avoid Telegram retry storms; details stay server-side.
        import logging
        logging.exception("Telegram webhook update failed")
    return {"ok": True}


@app.post("/api/telegram/webhook/setup")
async def setup_telegram_webhook(user: User = Depends(current_user)):
    require_owner(user)
    webhook_url = "https://bot-center-v2.vercel.app/api/telegram/webhook"
    try:
        result = await telegram_call("setWebhook", {
            "url": webhook_url,
            "secret_token": telegram_webhook_secret(),
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        })
        return {"ok": True, "webhook": result}
    except Exception as exc:
        raise HTTPException(502, f"تعذر تفعيل لوحة تيليجرام: {str(exc)[:250]}")


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
