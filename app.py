"""FastAPI service for small collaborative trip rooms."""

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from planner import BY_ID, CATALOG, solve, trip_slots

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    days INTEGER NOT NULL,
    capacity INTEGER NOT NULL,
    fairness REAL NOT NULL,
    stability REAL NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    stale INTEGER NOT NULL DEFAULT 0,
    plan TEXT,
    rejected TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS travelers (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    organizer INTEGER NOT NULL DEFAULT 0,
    preferences TEXT
);
"""


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Join(Input):
    name: Name


class CreateTrip(Join):
    title: Name
    days: Literal[2, 3] = 2
    capacity: int = Field(default=4, ge=3, le=6)
    fairness: float = Field(default=0.5, ge=0, le=2)
    stability: float = Field(default=0.25, ge=0, le=2)


class Preferences(Input):
    budget: int = Field(ge=0)
    ratings: dict[str, Annotated[int, Field(ge=0, le=5)]]
    unavailable: set[Annotated[int, Field(ge=0, le=5)]] = Field(default_factory=set)
    excluded: set[str] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_catalog(self):
        if self.ratings.keys() != BY_ID.keys():
            raise ValueError("Provide one rating for every ID returned by GET /catalog.")
        if self.excluded - (BY_ID.keys() - {"free"}):
            raise ValueError("Exclusions must be activity IDs other than free.")
        return self


class Revision(Input):
    revision: int = Field(ge=0)


class Feedback(Revision):
    slot_index: int = Field(ge=0, le=5)
    activity_id: str = Field(min_length=1, max_length=32)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def get_trip(db, trip_id):
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        raise HTTPException(404, "Trip room not found.")
    return trip


def authorize(db, trip_id, credentials, organizer_only=False):
    if credentials is None:
        raise HTTPException(401, "A traveler token is required.", {"WWW-Authenticate": "Bearer"})
    member = db.execute(
        "SELECT * FROM travelers WHERE trip_id = ? AND token_hash = ?",
        (trip_id, token_hash(credentials.credentials)),
    ).fetchone()
    if member is None:
        raise HTTPException(401, "Invalid traveler token for this room.", {"WWW-Authenticate": "Bearer"})
    if organizer_only and not member["organizer"]:
        raise HTTPException(403, "Only the organizer can generate an itinerary.")
    return member


def add_traveler(db, trip_id, name, organizer=False):
    traveler_id = secrets.token_urlsafe(12)
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO travelers (id, trip_id, name, token_hash, organizer) VALUES (?, ?, ?, ?, ?)",
        (traveler_id, trip_id, name, token_hash(token), organizer),
    )
    return {"trip_id": trip_id, "traveler_id": traveler_id, "token": token, "organizer": organizer}


def read_room(db, trip):
    travelers = db.execute(
        "SELECT id, name, organizer, preferences IS NOT NULL AS ready FROM travelers "
        "WHERE trip_id = ? ORDER BY rowid",
        (trip["id"],),
    ).fetchall()
    return {
        "id": trip["id"],
        "title": trip["title"],
        "days": trip["days"],
        "capacity": trip["capacity"],
        "slots": trip_slots(trip["days"]),
        "travelers": [
            {"id": row["id"], "name": row["name"], "organizer": bool(row["organizer"]),
             "ready": bool(row["ready"])}
            for row in travelers
        ],
        "revision": trip["revision"],
        "stale": bool(trip["stale"]),
        "plan": json.loads(trip["plan"]) if trip["plan"] else None,
    }


def preferences_for_plan(db, trip):
    rows = db.execute(
        "SELECT preferences FROM travelers WHERE trip_id = ? ORDER BY rowid",
        (trip["id"],),
    ).fetchall()
    if len(rows) < 3 or any(row["preferences"] is None for row in rows):
        raise HTTPException(409, "At least three travelers must join and all joined travelers must submit preferences.")
    return [json.loads(row["preferences"]) for row in rows]


def check_revision(trip, revision):
    if trip["revision"] != revision:
        raise HTTPException(409, "Trip changed. Fetch the latest room state and try again.")


def invalidate_plan(db, trip):
    db.execute(
        "UPDATE trips SET revision = revision + 1, stale = ? WHERE id = ?",
        (int(trip["plan"] is not None), trip["id"]),
    )


def save_plan(db, trip, plan, rejected):
    db.execute(
        "UPDATE trips SET plan = ?, rejected = ?, revision = revision + 1, stale = 0 WHERE id = ?",
        (json.dumps(plan), json.dumps(rejected), trip["id"]),
    )
    return read_room(db, get_trip(db, trip["id"]))


def create_app(database=None):
    path = Path(database or os.environ.get("TRIP_DB_PATH", "trips.sqlite3")).resolve()

    @contextmanager
    def connection(write=False):
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @asynccontextmanager
    async def lifespan(_app):
        path.parent.mkdir(parents=True, exist_ok=True)
        with connection(write=True) as db:
            db.executescript(SCHEMA)
        yield

    app = FastAPI(title="CS221 Group Trip Planner", lifespan=lifespan)

    web = Path(__file__).with_name("web")
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(web / "index.html")

    @app.get("/catalog")
    def catalog():
        return [asdict(activity) for activity in CATALOG]

    @app.post("/trips", status_code=201)
    def create_trip(body: CreateTrip):
        trip_id = secrets.token_urlsafe(16)
        with connection(write=True) as db:
            db.execute(
                "INSERT INTO trips (id, title, days, capacity, fairness, stability) VALUES (?, ?, ?, ?, ?, ?)",
                (trip_id, body.title, body.days, body.capacity, body.fairness, body.stability),
            )
            return add_traveler(db, trip_id, body.name, organizer=True)

    @app.post("/trips/{trip_id}/join", status_code=201)
    def join(trip_id: str, body: Join):
        with connection(write=True) as db:
            trip = get_trip(db, trip_id)
            count = db.execute("SELECT COUNT(*) FROM travelers WHERE trip_id = ?", (trip_id,)).fetchone()[0]
            if count >= trip["capacity"]:
                raise HTTPException(409, "This trip room is full.")
            member = add_traveler(db, trip_id, body.name)
            invalidate_plan(db, trip)
            return member

    @app.get("/trips/{trip_id}")
    def room(trip_id: str, credentials: Credentials):
        with connection() as db:
            authorize(db, trip_id, credentials)
            return read_room(db, get_trip(db, trip_id))

    @app.get("/trips/{trip_id}/preferences")
    def own_preferences(trip_id: str, credentials: Credentials):
        with connection() as db:
            member = authorize(db, trip_id, credentials)
            return json.loads(member["preferences"]) if member["preferences"] else None

    @app.put("/trips/{trip_id}/preferences")
    def set_preferences(trip_id: str, body: Preferences, credentials: Credentials):
        with connection(write=True) as db:
            member = authorize(db, trip_id, credentials)
            trip = get_trip(db, trip_id)
            if any(index >= 2 * trip["days"] for index in body.unavailable):
                raise HTTPException(422, "An unavailable slot is outside this trip.")
            preferences = body.model_dump(mode="json")
            preferences["unavailable"] = sorted(body.unavailable)
            preferences["excluded"] = sorted(body.excluded)
            serialized = json.dumps(preferences, sort_keys=True)
            if member["preferences"] != serialized:
                db.execute("UPDATE travelers SET preferences = ? WHERE id = ?", (serialized, member["id"]))
                invalidate_plan(db, trip)
            return read_room(db, get_trip(db, trip_id))

    @app.post("/trips/{trip_id}/itinerary")
    def generate(trip_id: str, body: Revision, credentials: Credentials):
        with connection(write=True) as db:
            authorize(db, trip_id, credentials, organizer_only=True)
            trip = get_trip(db, trip_id)
            check_revision(trip, body.revision)
            travelers = preferences_for_plan(db, trip)
            rejected = json.loads(trip["rejected"])
            previous = (
                [slot["id"] for slot in json.loads(trip["plan"])["slots"]]
                if trip["plan"] else []
            )
            plan = solve(trip["days"], travelers, trip["fairness"], trip["stability"], previous, rejected)
            return save_plan(db, trip, plan, rejected)

    @app.post("/trips/{trip_id}/feedback")
    def feedback(trip_id: str, body: Feedback, credentials: Credentials):
        with connection(write=True) as db:
            authorize(db, trip_id, credentials)
            trip = get_trip(db, trip_id)
            check_revision(trip, body.revision)
            if not trip["plan"] or trip["stale"]:
                raise HTTPException(409, "Generate an itinerary with current preferences before submitting feedback.")
            previous = [slot["id"] for slot in json.loads(trip["plan"])["slots"]]
            if body.activity_id == "free" or body.slot_index >= len(previous) or previous[body.slot_index] != body.activity_id:
                raise HTTPException(422, "Choose an activity from the current itinerary.")
            rejected = [*json.loads(trip["rejected"]), body.activity_id]
            travelers = preferences_for_plan(db, trip)
            plan = solve(trip["days"], travelers, trip["fairness"], trip["stability"], previous, rejected)
            return save_plan(db, trip, plan, rejected)

    return app


app = create_app()
