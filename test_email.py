import asyncio
from services.email_service import send_email


async def main():
    await send_email(
        to_email="yinthong0323@gmail.com",
        subject="FarmTab Email Test",
        body="This is a test email from the FarmTab backend."
    )

    print("Email sent successfully!")


asyncio.run(main())
