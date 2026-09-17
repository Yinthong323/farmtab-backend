from fastapi import FastAPI

from auth.signup import router as signup_router
from auth.verification import router as verification_router
from auth.resend import router as resend_router
from organisation.create import router as organisation_router
from auth.login import router as login_router
from auth.forgot_password import router as forgot_password_router
from organisation.shared import router as shared_organisation_router
from organisation.create import router as organisation_router
from organisation.join import router as organisation_join_router
from sites.sites import router as site_router
from fastapi.staticfiles import StaticFiles
from shelves.shelves import router as shelves_router
from sensor_readings.sensor_readings import router as sensor_readings_router

app = FastAPI()
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def home():
    return {
        "message": "Farm application backend is running!"
    }


app.include_router(signup_router)
app.include_router(verification_router)
app.include_router(resend_router)
app.include_router(login_router)
app.include_router(forgot_password_router)

app.include_router(organisation_router)
app.include_router(shared_organisation_router)
app.include_router(organisation_join_router)
app.include_router(site_router)
app.include_router(shelves_router)
app.include_router(sensor_readings_router)