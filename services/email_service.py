import os

import aiosmtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()


async def send_email(
    to_email: str,
    subject: str,
    body: str,
):
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


async def send_verification_email(
    to_email: str,
    verification_code: str,
):
    subject = "FarmTab - Email Verification Code"

    body = f"""
Dear FarmTab User,

Thank you for registering an account with FarmTab.

To complete your registration, please use the verification code below:

    Verification Code: {verification_code}

This verification code is valid for 10 minutes.

For security reasons, please do not share this code with anyone.

If you did not create a FarmTab account, you may safely ignore this email.

Best regards,

FarmTab Team
"""


    await send_email(
        to_email=to_email,
        subject=subject,
        body=body.strip(),
    )


async def send_password_reset_email(
    to_email: str,
    reset_code: str,
):
    subject = "FarmTab - Password Reset Code"

    body = f"""
Dear FarmTab User,

We received a request to reset the password for your FarmTab account.

Please use the verification code below to continue with the password reset process:

    Password Reset Code: {reset_code}

This code is valid for 10 minutes.

For your security:

- Do not share this code with anyone.
- FarmTab will never ask you to provide your password.
- If you did not request a password reset, please ignore this email and your account will remain secure.

If you continue to experience problems accessing your account, please contact the FarmTab support team.

Best regards,

FarmTab Team
"""

    await send_email(
        to_email=to_email,
        subject=subject,
        body=body.strip(),
    )
