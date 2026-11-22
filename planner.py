"""Deterministic itinerary optimization over a small demo catalog."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Activity:
    id: str
    name: str
    cost: int
    period: str | None = None


CATALOG = (
    Activity("free", "Free time", 0),
    Activity("hike", "Discovery Park hike", 0),
    Activity("museum", "Seattle Art Museum", 25),
    Activity("market", "Pike Place Market", 10, "morning"),
    Activity("kayak", "Lake Union kayaking", 40, "afternoon"),
    Activity("garden", "Washington Park Arboretum", 0),
    Activity("food", "International District food stop", 20, "afternoon"),
    Activity("locks", "Ballard Locks", 0),
)
BY_ID = {activity.id: activity for activity in CATALOG}
PERIODS = ("morning", "afternoon")


def trip_slots(days):
    return [
        f"{day} {period}"
        for day in ("Saturday", "Sunday", "Monday")[:days]
        for period in PERIODS
    ]


def solve(days, travelers, fairness, stability, previous=(), rejected=()):
    slots = trip_slots(days)
    budget = min(traveler["budget"] for traveler in travelers)
    choices = []
    for index in range(len(slots)):
        period = PERIODS[index % len(PERIODS)]
        choices.append([
            activity for activity in CATALOG
            if activity.id == "free" or (
                activity.id not in rejected
                and activity.period in (None, period)
                and all(
                    index not in traveler["unavailable"]
                    and activity.id not in traveler["excluded"]
                    for traveler in travelers
                )
            )
        ])

    best = None
    itinerary = []
    used = set()
    ratings = [0] * len(travelers)

    def search(index, cost, changes):
        nonlocal best
        if index == len(slots):
            average = sum(ratings) / (5 * len(slots) * len(travelers))
            gap = (max(ratings) - min(ratings)) / (5 * len(slots))
            utility = average - fairness * gap
            objective = utility - stability * changes
            if best is None or (round(objective, 12), -cost) > best[0]:
                best = (
                    (round(objective, 12), -cost),
                    {
                        "slots": [
                            {"index": i, "label": slots[i], **asdict(activity),
                             "changed": bool(previous and previous[i] != activity.id)}
                            for i, activity in enumerate(itinerary)
                        ],
                        "cost_per_traveler": cost,
                        "average_satisfaction": average,
                        "satisfaction_gap": gap,
                        "group_utility": utility,
                        "changed_slots": changes,
                    },
                )
            return

        for activity in choices[index]:
            if activity.id in used or cost + activity.cost > budget:
                continue
            itinerary.append(activity)
            if activity.id != "free":
                used.add(activity.id)
            for i, traveler in enumerate(travelers):
                ratings[i] += traveler["ratings"][activity.id]
            changed = bool(previous and previous[index] != activity.id)
            search(index + 1, cost + activity.cost, changes + int(changed))
            for i, traveler in enumerate(travelers):
                ratings[i] -= traveler["ratings"][activity.id]
            used.discard(activity.id)
            itinerary.pop()

    search(0, 0, 0)
    return best[1]
