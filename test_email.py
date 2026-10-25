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


# Host cyphyai.usm.my
#   HostName cyphyai.usm.my
#   Port 8181
#   User ngyinthong

# Host farmtab-ec2
#   HostName 98.88.222.75
#   IdentityFile C:\Users\Dell\Downloads\labsuser.pem
#   User ec2-user

# Host my-ec2
#   HostName 100.62.8.111
#   User ec2-user
#   IdentityFile C:\Users\Dell\.ssh\labsuser.pem

# Host pi2
#   HostName farmtab-l1-right.local
#   User yinthong
#   IdentityFile C:\Users\Dell\.ssh\id_ed25519

# Host pi1
#   HostName farmtab-l1-left.local
#   User yinthong
#   IdentityFile C:\Users\Dell\.ssh\id_ed25519

# Host pi3
#   HostName farmtab-l2-left.local
#   User yinthong
#   IdentityFile C:\Users\Dell\.ssh\id_ed25519

# Host pi4
#   HostName farmtab-l2-right.local
#   User yinthong
#   IdentityFile C:\Users\Dell\.ssh\id_ed25519

# Host pi5
#   HostName 10.207.203.58
#   User yinthong
#   IdentityFile C:\Users\Dell\.ssh\id_ed25519

# Host pi6
#   HostName farmtab-l3-right.local
#   User yinthong
#   IdentityFile C:\Users\Dell\.ssh\id_ed25519
