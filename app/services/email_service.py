import asyncio
import random
import smtplib
from email.mime.text import MIMEText

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncpg
import os

# =========================
# CONFIG
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM")

app = FastAPI()

# =========================
# DB CONNECTION
# =========================
pool = None

@app.on_event("startup")
async def startup():
    global pool
    print("🚀 STARTING APP")
    print("SMTP:", SMTP_HOST, SMTP_USER)

    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT,
            otp TEXT
        )
        """)

# =========================
# REQUEST MODEL
# =========================
class RegisterRequest(BaseModel):
    email: str

# =========================
# EMAIL FUNCTION
# =========================
async def send_email(to_email: str, otp: str):
    try:
        print(f"📧 Sending email to {to_email}")

        msg = MIMEText(f"Your OTP is: {otp}")
        msg["Subject"] = "OTP Verification"
        msg["From"] = EMAIL_FROM
        msg["To"] = to_email

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()

        print("✅ Email sent successfully")
        return True

    except Exception as e:
        print("❌ EMAIL FAILED:", e)
        return False

# =========================
# REGISTER API
# =========================
@app.post("/register")
async def register(data: RegisterRequest):
    otp = str(random.randint(100000, 999999))

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(email, otp) VALUES($1, $2)",
            data.email,
            otp
        )

    result = await send_email(data.email, otp)

    if not result:
        raise HTTPException(status_code=500, detail="Email failed")

    return {
        "success": True,
        "message": "OTP sent",
        "otp_debug": otp  # remove in production
    }

# =========================
# TEST EMAIL
# =========================
@app.get("/test-email")
async def test_email():
    otp = "123456"
    result = await send_email("bogala4307@gmail.com", otp)
    return {"success": result}