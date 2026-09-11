from fastapi import FastAPI

from auth.signup import router as signup_router
from auth.verification import router as verification_router
from auth.resend import router as resend_router


app = FastAPI()


@app.get("/")
def home():
    return {
        "message": "Farm application backend is running!"
    }


app.include_router(signup_router)
app.include_router(verification_router)
app.include_router(resend_router)