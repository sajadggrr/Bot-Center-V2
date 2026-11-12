# Bot Center V2

Arabic-first Telegram bot management platform.

## Local setup

1. Copy `.env.example` to `.env`.
2. Set a long random `SECRET_KEY`.
3. Set the Telegram bot token in the environment as `TELEGRAM_BOT_TOKEN`.
4. Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

5. Put that value in `BOT_TOKEN_ENCRYPTION_KEY`.
6. Install dependencies and run:

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `/docs` to inspect the API.

## Important

This is the first backend foundation, not a finished public SaaS yet. Telegram Mini App authentication uses signed init data. Never expose bot tokens in source control. Production deployment requires HTTPS, a persistent database/volume, strict CORS, rate limits, backups, and an operator-managed secret store.

The legacy `Bot-Center` repository is not modified.
