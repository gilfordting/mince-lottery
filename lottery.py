from collections import defaultdict
import csv
from datetime import datetime, timedelta
from dataclasses import dataclass
import math
from validation import email_valid
import numpy as np

# Consider only lottery history within a sliding window of N years
YEAR_WINDOW = 5

ALLOWED_POPUP_IDS = ("entropy", "ayuy", "untitled")
# Before this date (day after Spring into Summer), we don't have data on guest lists (history/guests/*_guests.csv).
GUESTLIST_HISTORY_CUTOFF = datetime(2023, 4, 31)

# Constructs a prefix ala raft state machine replay


# Datatypes for database entries.


@dataclass(frozen=True)
class Guest:
    name: str
    email: str


@dataclass(frozen=True)
class Entry:
    guests: tuple[Guest, ...]
    notes: str


def make_entry(names: list[str], emails: list[str], notes: str) -> Entry:
    assert len(names) == len(emails)
    return Entry(
        guests=tuple(Guest(name, email) for name, email in zip(names, emails)),
        notes=notes,
    )


# Turns list of individual guests and groups --> list of individual guests only, flattening groups. Gets rid of
# We should have no duplicates in this list.
# TODO: do we need this?
def flatten_entries(entries: list[Entry]) -> list[Guest]:
    entries_flat = sum([list(entry.guests) for entry in entries], start=[])
    emails_flat = [entry.email for entry in entries_flat]
    assert len(emails_flat) == len(set(emails_flat)), (
        "Duplicate emails found in flattened entries"
    )
    return entries_flat


# Process a single row of a lottery entry spreadsheet.
# Returns None if row has *any* invalid data.
def process_row(names: str, emails: str, notes="") -> Entry | None:
    names = [name.strip() for name in names.split(",")]
    emails = [email.lower().strip() for email in emails.split(",")]
    # Mismatched number of names/emails -- throw out
    if len(names) != len(emails):
        return None
    # Any email in the group invalid? -- throw out
    if any(not email_valid(email) for email in emails):
        return None
    # Duplicate emails within the same entry -- throw out
    if len(set(emails)) != len(emails):
        return None
    return make_entry(names, emails, notes)


# Turns guest spreadsheet from a past popup into list of Guests.
def get_guests(rows: list[tuple[str, str]]) -> list[Guest]:
    entries = []
    for row in rows:
        names, emails = row
        entry = process_row(names, emails)
        assert entry is not None, f"Invalid guest row: {names}, {emails}"
        assert len(entry.guests) == 1, f"Expected single guest, got {len(entry.guests)}"
        entries.append(entry.guests[0])
    return entries


# Turns spreadsheet of lottery entries into list of Entries.
# Input: list of (names, emails, notes).
def get_entries(rows: list[tuple[str, str, str]]) -> list[Entry]:
    entries: set[Entry] = set()
    # Maps a guest's email to their entry.
    guest_mapping: dict[str, Guest] = {}

    # Removes past entries for a single guest.
    def remove_previous(email: str):
        if email not in guest_mapping:
            return
        entry = guest_mapping[email]
        # If the entry was already removed, we do nothing -- use .discard
        entries.discard(entry)
        del guest_mapping[email]

    def add_entry(entry: Entry):
        if isinstance(entry, Guest):
            remove_previous(entry.email)
            guest_mapping[entry.email] = entry
            return
        # group
        for guest in entry.guests:
            remove_previous(guest.email)
            guest_mapping[guest.email] = entry

        entries.add(entry)

    for row in rows:
        names, emails, notes = row
        entry = process_row(names, emails, notes)
        if entry is None:
            continue
        add_entry(entry)

    return list(entries)


class Database:
    def __init__(self, current_popup_id: str, window_size_years: int):
        self.current_popup_id = current_popup_id
        self.window_size_years = window_size_years
        # keep track of each guest's score
        self.counts: dict[str, int] = defaultdict(int)
        # keep track of each guest's attendance history
        self.attended: dict[str, list[str]] = defaultdict(list)

        self.recent_popup_ids = self.get_recent_popup_ids()
        self.history_playback()

    def get_recent_popup_ids(self) -> dict[str, datetime]:
        ids: dict[str, datetime] = {}
        # Find all relevant popup IDs
        window_start = datetime.now() - timedelta(days=self.window_size_years * 365)
        print(f"Considering popups from {window_start.strftime('%Y-%m-%d')} onwards")
        prev_date = None
        with open("history/popups.csv", newline="", encoding="utf-8") as csvfile:
            rows = list(csv.DictReader(csvfile))
            for i, row in enumerate(rows):
                if i == len(rows) - 1:
                    assert row["id"] == self.current_popup_id, (
                        "The last popup in `popups.csv` should be the current popup"
                    )
                    break
                date = datetime.strptime(row["date"], "%Y.%m.%d")
                if prev_date is not None:
                    assert date > prev_date, (
                        f'Popup dates are not in increasing order; invariant broken by popup "{row["name"]}"'
                    )
                prev_date = date
                assert row["id"] not in ids, (
                    f'Popup ID "{row["id"]}" is repeated; invariant broken by popup "{row["name"]}"'
                )
                if date >= window_start and row["id"] != self.current_popup_id:
                    ids[row["id"]] = date
        print(f"Found popups: {ids}")
        return ids

    def process_past_popup(self, popup_id: str, date: datetime):
        # TODO: temporary
        if popup_id not in ALLOWED_POPUP_IDS:
            return
        # Process lottery first
        with open(
            f"history/lottery/{popup_id}_lottery.csv", newline="", encoding="utf-8"
        ) as csvfile:
            rows = list(csv.DictReader(csvfile))
            entries = get_entries(
                [(row["names"], row["emails"], row["notes"]) for row in rows]
            )
            guests = flatten_entries(entries)
            for guest in guests:
                self.counts[guest.email] += 1
        # Then process guests from that popup, if they exist
        if date < GUESTLIST_HISTORY_CUTOFF:
            return
        with open(
            f"history/guests/{popup_id}_guests.csv", newline="", encoding="utf-8"
        ) as csvfile:
            rows = list(csv.DictReader(csvfile))
            guests = get_guests([(row["name"], row["email"]) for row in rows])
            for guest in guests:
                # Reset attended guests back to 0. TODO: how can we make this better?
                self.counts[guest.email] = 0
                # Also add to their attendance history
                self.attended[guest.email].append(popup_id)

    def history_playback(self):
        for popup_id, date in self.recent_popup_ids.items():
            self.process_past_popup(popup_id, date)

    def export_cumulative_data(self):
        with open("scores.csv", "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["email", "score"])
            # Sort scores by score decreasing (alpha order as secondary key)
            rows = list(self.counts.items())
            rows.sort(key=lambda x: x[0])
            rows.sort(key=lambda x: x[1], reverse=True)
            for email, score in rows:
                writer.writerow([email, score])

        with open("past_attendance.csv", "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["email", "attended_popups"])
            # Sort attendance by len(popups) decreasing, then email alpha increasing
            rows = list(self.attended.items())
            rows.sort(key=lambda x: x[0])
            rows.sort(key=lambda x: len(x[1]), reverse=True)
            for email, popups in rows:
                writer.writerow([email, ",".join(popups)])

    # TODO: function for getting scores for the current popup
    # num_samples: number of entries to draw, so the number of people is in [num_samples, 2*num_samples]
    # temperature: controls the spread of the distribution
    def export_lottery_results(self, num_samples: int, temperature: float = 1.0):
        input_file = f"history/lottery/{self.current_popup_id}_lottery.csv"
        output_file = f"lottery_results_{self.current_popup_id}.csv"

        with open(input_file, newline="", encoding="utf-8") as csvfile:
            rows = list(csv.DictReader(csvfile))
            entries = get_entries(
                [(row["names"], row["emails"], row["notes"]) for row in rows]
            )

        assert self.counts, "self.counts must not be empty"

        def group_score(emails):
            # TODO: currently just averages the scores, change if needed
            scores = []
            for email in emails:
                email = email.strip()
                score = self.counts.get(email, 0)  # TODO score of 0 is turned into 1
                scores.append(score)
            return sum(scores) / len(scores) if scores else 0

        guests_data = []
        weights = []

        for entry in entries:
            emails = [guest.email for guest in entry.guests]
            score = group_score(emails)
            weight = math.exp(score / temperature)
            guests_data.append(
                {
                    "names": ",".join([guest.name for guest in entry.guests]),
                    "emails": ",".join([guest.email for guest in entry.guests]),
                    "notes": entry.notes,
                    "emails_list": emails,
                    "score": score,
                    "weight": weight,
                }
            )
            weights.append(weight)

        weights = np.array(weights) / np.sum(weights)  # normalize weights

        selected_rows = np.random.choice(
            guests_data, size=num_samples, replace=False, p=weights
        )

        with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(
                [
                    "names",
                    "emails",
                    "notes",
                    "score",
                    "weight",
                    "total_popups_attended",
                    "popups_attended",
                ]
            )
            for row in selected_rows:
                attended_counts = []
                attended_lists = []
                for email in row["emails_list"]:
                    count = len(self.attended.get(email, []))
                    attended_counts.append(count)
                    attended_lists.append(self.attended.get(email, []))
                total_attended = sum(attended_counts)
                unique_popups = set(
                    p for popup_list in attended_lists for p in popup_list
                )
                writer.writerow(
                    [
                        row["names"],
                        row["emails"],
                        row["notes"],
                        row["score"],
                        row["weight"],
                        total_attended,
                        ",".join(sorted(unique_popups)),
                    ]
                )

    # returns the number of entries since the last attended popup
    # if never attended: return -1?
    # def entries_accumulated(self, guest: str) -> int:
    #     pass

    # def has_attended(self, guest: str) -> bool:
    #     pass

    # # Returns the number of times a guest has attended
    # def get_count(self, guest: str) -> int:
    #     pass


if __name__ == "__main__":
    db = Database(current_popup_id="entropy", window_size_years=5)
    db.export_cumulative_data()
    db.export_lottery_results(num_samples=100, temperature=3.0)
