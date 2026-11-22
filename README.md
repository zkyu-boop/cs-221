# CS221 Group Trip Planner Backend

FastAPI and SQLite service for two- or three-day trips with three to six travelers. The server stores trip rooms, preferences, itinerary revisions, and activity rejections. The solver uses backtracking over the fixed Seattle demo catalog.

## Start locally

Use Python 3.10 or newer. From this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app
```

Open http://127.0.0.1:8000/ to use the trip planner. The same server also exposes interactive API docs at http://127.0.0.1:8000/docs. SQLite data lives in `trips.sqlite3` by default. Set `TRIP_DB_PATH` to use another database. To reuse a previous database, stop the old server and point this version to the existing file.

## Try the web app

1. Create a trip as the organizer. Copy the invite link.
2. Open the link in two separate tabs or browsers and join as two different travelers. The current tab stores its traveler token for reloads. Each traveler submits their own preferences.
3. Once everyone is ready, generate an itinerary from the organizer tab. Other open tabs refresh room state automatically.
4. From a traveler tab, remove an activity. The replacement itinerary appears for everyone, with changed slots highlighted. If someone edits preferences, the organizer can update the itinerary with the same change penalty.

For a test across devices on the same trusted Wi-Fi, run `python -m uvicorn app:app --host 0.0.0.0` and open the host computer's LAN address on port 8000 before copying the invite link. A localhost link only works on the host computer.

## Try the API directly

1. Call `POST /trips` with `{"title":"Seattle weekend","name":"Organizer","capacity":3}`. Keep the returned organizer token and share the trip ID with two travelers.
2. Each traveler calls `POST /trips/{trip_id}/join` with their name. Each response has a separate bearer token. In API docs, use **Authorize** to switch identities. Each traveler's token can read the room and edit their own preferences.
3. Each of the three users calls `PUT /trips/{trip_id}/preferences`, for example:

   ```json
   {
     "budget": 55,
     "ratings": {"free":1,"hike":5,"museum":3,"market":4,"kayak":2,"garden":4,"food":4,"locks":3},
     "unavailable": [0],
     "excluded": ["kayak"]
   }
   ```

4. As organizer, fetch `GET /trips/{trip_id}`. Send its current `revision` in `POST /trips/{trip_id}/itinerary`.
5. As any traveler, take a non-free activity from the resulting plan. Send its ID, slot index, and the current revision to `POST /trips/{trip_id}/feedback`, for example `{"revision":6,"slot_index":1,"activity_id":"hike"}` with values from your actual plan. The server saves the new itinerary and marks changed slots.
6. Fetch the room again after a server restart to see the saved plan. If an update returns HTTP 409 because of a stale revision, fetch the room again before retrying.

## Prototype behavior

- A shared invite capability is the random trip ID. The organizer and each traveler receive separate tokens. Share the trip ID and keep individual tokens private.
- Average satisfaction is the mean of each traveler's normalized activity ratings. The objective subtracts a penalty for the highest-to-lowest satisfaction gap and, during feedback, a penalty per changed slot.
- Prices are fixed demo prices per traveler. An unavailable slot is left free for the group. Rejected activities are excluded for the rest of the trip. Free time is always feasible.
- The demo is local by default. Team members on other devices need a reachable instance of the service. Use a trusted test environment and HTTPS if hosting it beyond your machine.

Negotiable-constraint approval and the proposal's baseline, ablations, and user-study targets are outside this integration. The solver always allows free time.
