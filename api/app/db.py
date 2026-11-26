from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


def is_sqlite(url_or_engine) -> bool:
    name = url_or_engine.dialect.name if isinstance(url_or_engine, Engine) else str(url_or_engine).split(":", 1)[0]
    return name.startswith("sqlite")


def make_engine(url: str) -> Engine:
    """Postgres (the default) or SQLite (tests and local development: DATABASE_URL=sqlite:///path/to/file.db).

    SQLite is set up the way the app needs it: WAL so the API and the worker process can read while one writes,
    a busy timeout so a writer waits for the other instead of failing, and foreign keys on (SQLite ignores them
    by default, Postgres never does).
    """
    if not is_sqlite(url):
        return create_engine(url, pool_pre_ping=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        if ":memory:" not in url and url != "sqlite://":
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session
