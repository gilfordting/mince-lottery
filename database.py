import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from enum import Enum, auto

import numpy as np

from validation import check_history_folder, email_valid

logger = logging.getLogger("lottery")


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


# Turns list of individual guests and groups --> list of individual guests only, flattening groups. We should have no duplicates in this list.
def flatten_entries(entries: list[Entry]) -> list[Guest]:
    entries_flat = sum([list(entry.guests) for entry in entries], start=[])
    emails_flat = [entry.email for entry in entries_flat]
    assert len(emails_flat) == len(set(emails_flat)), (
        "Duplicate emails found in flattened entries"
    )
    return entries_flat


class DropReason(Enum):
    MISMATCHED_COUNTS = auto()
    INVALID_EMAIL = auto()
    DUPLICATE_EMAILS = auto()
    NO_DROP = auto()


# Process a single row of a lottery entry spreadsheet.
# Returns None if row has *any* invalid data.
def process_row(names: str, emails: str, notes="") -> tuple[Entry | None, DropReason]:
    names = [name.strip() for name in names.split(",")]
    emails = [email.lower().strip() for email in emails.split(",")]
    # Mismatched number of names/emails -- throw out
    if len(names) != len(emails):
        return None, DropReason.MISMATCHED_COUNTS
    # Any email in the group invalid? -- throw out
    if any(not email_valid(email) for email in emails):
        return None, DropReason.INVALID_EMAIL
    # Duplicate emails within the same entry -- throw out
    if len(set(emails)) != len(emails):
        return None, DropReason.DUPLICATE_EMAILS
    return make_entry(names, emails, notes), DropReason.NO_DROP


# Turns guest spreadsheet from a past popup into list of Guests.
def get_guests(rows: list[tuple[str, str]]) -> list[Guest]:
    entries = []
    for row in rows:
        names, emails = row
        entry, _ = process_row(names, emails)
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

    for row_num, row in enumerate(rows):
        names, emails, notes = row
        entry, drop_reason = process_row(names, emails, notes)
        if entry is None:
            match drop_reason:
                case DropReason.DUPLICATE_EMAILS:
                    msg = f"duplicate emails {emails}"
                case DropReason.INVALID_EMAIL:
                    msg = f"invalid email in {emails}"
                case DropReason.MISMATCHED_COUNTS:
                    msg = f"mismatched counts for names: {names}, emails: {emails}"
                case DropReason.NO_DROP:
                    assert False, "Drop reason should be provided"
                case _:
                    msg = f"unknown drop reason: {drop_reason}"
            logger.debug(f"Dropping row {row_num + 2}; {msg}")
            continue
        add_entry(entry)

    return list(entries)


class Database:
    def __init__(
        self,
        current_popup_id: str,
        window_size_years: int,
        group_score_reduce_fn: Callable[[list[float]], float],
        success_penalty_fn: Callable[[float], float],
        weighting_fn: Callable[[float], float],
    ):
        self.current_popup_id = current_popup_id
        self.window_size_years = window_size_years
        self.group_score_reduce_fn = group_score_reduce_fn
        self.success_penalty_fn = success_penalty_fn
        self.weighting_fn = weighting_fn
        # keep track of each guest's score
        self.counts: dict[str, int] = defaultdict(int)
        # keep track of each guest's attendance history
        self.attended: dict[str, list[str]] = defaultdict(list)
        self.data_valid = check_history_folder()
        if not self.data_valid:
            logging.error(
                "History folder validation failed. Please fix the errors and try again."
            )
            return

        self.recent_popup_ids = self.get_recent_popup_ids()
        self.history_playback()

    def get_recent_popup_ids(self) -> dict[str, datetime]:
        ids: dict[str, datetime] = {}
        # Find all relevant popup IDs
        window_start = datetime.now() - timedelta(days=self.window_size_years * 365)
        logger.info(
            "Considering popups from %s onwards", window_start.strftime("%Y-%m-%d")
        )
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
        logger.info("Found %d popups: %s", len(ids), list(ids.keys()))
        return ids

    def process_past_popup(self, popup_id: str, date: datetime):
        # Process lottery first
        with open(
            f"history/lottery/{popup_id}_lottery.csv", newline="", encoding="utf-8"
        ) as csvfile:
            rows = list(csv.DictReader(csvfile))
            logging.debug(
                f"Processing {len(rows)} lottery entries for popup {popup_id}"
            )
            entries = get_entries(
                [(row["names"], row["emails"], row["notes"]) for row in rows]
            )
            guests = flatten_entries(entries)
            for guest in guests:
                self.counts[guest.email] += 1
        # Then process guests from that popup
        with open(
            f"history/guests/{popup_id}_guests.csv", newline="", encoding="utf-8"
        ) as csvfile:
            rows = list(csv.DictReader(csvfile))
            guests = get_guests([(row["name"], row["email"]) for row in rows])
            for guest in guests:
                self.counts[guest.email] = self.success_penalty_fn(
                    self.counts[guest.email]
                )
                # Also add to their attendance history
                self.attended[guest.email].append(popup_id)

    def history_playback(self):
        for popup_id, date in self.recent_popup_ids.items():
            self.process_past_popup(popup_id, date)

    def export_cumulative_data(self):
        logging.info("Exporting cumulative scores to `scores.csv`")
        with open("scores.csv", "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["email", "score"])
            # Sort scores by score decreasing (alpha order as secondary key)
            rows = list(self.counts.items())
            rows.sort(key=lambda x: x[0])
            rows.sort(key=lambda x: x[1], reverse=True)
            for email, score in rows:
                writer.writerow([email, score])

        logging.info("Exporting past attendance to `past_attendance.csv`")
        with open("past_attendance.csv", "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["email", "attended_popups"])
            # Sort attendance by len(popups) decreasing, then email alpha increasing
            rows = list(self.attended.items())
            rows.sort(key=lambda x: x[0])
            rows.sort(key=lambda x: len(x[1]), reverse=True)
            for email, popups in rows:
                writer.writerow([email, ", ".join(popups)])

    # num_samples: number of entries to draw, so the number of people is in [num_samples, 2*num_samples]
    def export_lottery_results(self, num_samples: int):
        logging.info(
            "Exporting lottery results to `lottery_results_%s.csv`",
            self.current_popup_id,
        )
        input_file = f"history/lottery/{self.current_popup_id}_lottery.csv"
        output_file = f"lottery_results_{self.current_popup_id}.csv"

        with open(input_file, newline="", encoding="utf-8") as csvfile:
            rows = list(csv.DictReader(csvfile))
            entries = get_entries(
                [(row["names"], row["emails"], row["notes"]) for row in rows]
            )

        assert self.counts, "self.counts must not be empty"

        def group_score(emails):
            scores = []
            for email in emails:
                email = email.strip()
                score = self.counts.get(email, 0) + 1
                scores.append(score)
            return self.group_score_reduce_fn(scores)

        guests_data = []
        weights = []

        for entry in entries:
            emails = [guest.email for guest in entry.guests]
            score = group_score(emails)
            weight = self.weighting_fn(score)
            guests_data.append(
                {
                    "names": ", ".join([guest.name for guest in entry.guests]),
                    "emails": ", ".join([guest.email for guest in entry.guests]),
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
                        ", ".join(sorted(unique_popups)),
                    ]
                )
