import os
import aiosmtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()


async def send_email(to_email: str, subject: str, body: str):
    message = EmailMessage()

    message["From"] = os.getenv("EMAIL_ADDRESS")
    message["To"] = to_email
    message["Subject"] = subject

    message.set_content(body)

    await aiosmtplib.send(
        message,
        hostname="smtp.gmail.com",
        port=587,
        start_tls=True,
        username=os.getenv("EMAIL_ADDRESS"),
        password=os.getenv("EMAIL_APP_PASSWORD"),
    )
