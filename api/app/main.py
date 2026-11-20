from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.auth import hash_password
from app.config import get_settings
from app.db import SessionLocal
from app.models import Role, User
from app.routes import auth as auth_routes
from app.routes import exams as exam_routes
from app.routes import grading as grading_routes
from app.routes import imports as import_routes
from app.routes import review as review_routes
from app.routes import submissions as submission_routes


def bootstrap_admin() -> None:
    s = get_settings()
    if not (s.bootstrap_admin_email and s.bootstrap_admin_password):
        return
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            db.add(
                User(
                    email=s.bootstrap_admin_email,
                    name="Admin",
                    password_hash=hash_password(s.bootstrap_admin_password),
                    role=Role.admin,
                )
            )
            db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap_admin()
    yield


app = FastAPI(title="Paper Grader", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_routes.router)
app.include_router(exam_routes.router)
app.include_router(import_routes.router)
app.include_router(grading_routes.router)
app.include_router(review_routes.router)
app.include_router(submission_routes.router)


@app.get("/health")
def health():
    return {"status": "ok"}
