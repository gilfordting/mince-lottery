import csv
import hashlib
import logging
import marshal
import os
import pickle
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Callable

import numpy as np

from validation import EmailType, check_history_folder, validate_email_batch

logger = logging.getLogger(__name__)

CACHE_FILE = ".db_cache.pkl"
EMAIL_TYPES = ["student", "staff", "affiliate", "non_mit"]


def _compute_fingerprint(
    window_size_years: int,
    current_popup_id: str,
    *fns: Callable,
) -> str:
    h = hashlib.md5()
    for root, dirs, files in os.walk("history"):
        dirs.sort()
        for fname in sorted(files):
            path = os.path.join(root, fname)
            stat = os.stat(path)
            h.update(f"{path}:{stat.st_mtime}:{stat.st_size}\n".encode())
    h.update(f"window:{window_size_years}\npopup:{current_popup_id}\n".encode())
    for fn in fns:
        h.update(marshal.dumps(fn.__code__))
    return h.hexdigest()


# Datatypes for database entries.
@dataclass(frozen=True)
class Guest:
    name: str
    email: str
    email_type: EmailType


@dataclass(frozen=True)
class Entry:
    guests: tuple[Guest, ...]
    notes: str
    extra_qs: tuple[str, ...]


def load_lottery_entries(
    popup_id: str, lottery_version: int, group_type: str | None
) -> list[Entry]:
    with open(
        f"history/lottery/{popup_id}_lottery.csv", newline="", encoding="utf-8"
    ) as f:
        rows = list(csv.DictReader(f))
        logger.data(f"Loaded {len(rows)} lottery entries for popup '{popup_id}'")
    if lottery_version == 2:
        rows = reformat_sheet_v2(rows, group_type)
    extra_qs_list = [
        [row[col] for col in row if col.startswith("extraq")] for row in rows
    ]
    return parse_entries(
        [
            (row["names"], row["emails"], row["notes"], extra_qs)
            for row, extra_qs in zip(rows, extra_qs_list)
        ]
    )


def make_entry(
    names: list[str],
    emails: list[str],
    email_types: list[EmailType],
    notes: str,
    extra_qs: list[str],
) -> Entry:
    assert len(names) == len(emails)
    assert len(names) == len(email_types)
    return Entry(
        guests=tuple(
            Guest(name, email, email_type)
            for name, email, email_type in zip(names, emails, email_types)
        ),
        notes=notes,
        extra_qs=tuple(extra_qs),
    )


# Turns list of individual guests and groups --> list of individual guests only, flattening groups. We should have no duplicates in this list.
def flatten_entries(entries: list[Entry]) -> list[Guest]:
    guests_flat = [guest for entry in entries for guest in entry.guests]
    emails_flat = [guest.email for guest in guests_flat]
    assert len(emails_flat) == len(set(emails_flat)), (
        "Duplicate emails found in flattened entries"
    )
    return guests_flat


class DropReason(Enum):
    MISMATCHED_COUNTS = auto()
    MISSING_EMAIL = auto()
    INVALID_EMAIL = auto()
    DUPLICATE_EMAILS = auto()
    NO_DROP = auto()


# Process a single row of a lottery entry spreadsheet.
# Returns None if row has *any* invalid data.
def process_row(
    names: list[str],
    emails: list[str],
    email_types: list[EmailType],
    notes: str = "",
    extra_qs: list[str] = [],
) -> tuple[Entry | None, DropReason]:
    # Mismatched number of names/emails -- throw out
    if len(names) != len(emails):
        return None, DropReason.MISMATCHED_COUNTS
    # Any email left blank (e.g. empty guest_email in a v2 group sheet)? -- throw out
    if any(not email for email in emails):
        return None, DropReason.MISSING_EMAIL
    # Any email in the group invalid? -- throw out
    if any(email_type == EmailType.INVALID for email_type in email_types):
        return None, DropReason.INVALID_EMAIL
    # Duplicate emails within the same entry -- throw out
    if len(set(emails)) != len(emails):
        return None, DropReason.DUPLICATE_EMAILS
    return make_entry(names, emails, email_types, notes, extra_qs), DropReason.NO_DROP


# Turns guest spreadsheet from a past popup into list of Guests.
def parse_guests(rows: list[tuple[str, str]]) -> list[Guest]:
    guests = []
    # for each row, get name and email
    names = [name.strip() for name, _ in rows]
    emails = [email.strip().lower() for _, email in rows]
    # feed in all emails as flattened list; it will be cached, and then we can just query again for individual rows
    _ = validate_email_batch(emails)
    for i, (name, email) in enumerate(zip(names, emails), 2):
        email_types = validate_email_batch([email])
        entry, _ = process_row([name], [email], email_types)
        if entry is None:
            logger.data(f"Invalid guest in row {i}: {name}, {email}")
            continue
        assert len(entry.guests) == 1, (
            f"Expected one guest in row {i}, got {len(entry.guests)}"
        )
        guests.append(entry.guests[0])
    return guests


# Turns spreadsheet of lottery entries into list of Entries.
# Input: list of (names, emails, notes, extra questions).
# Emails are normalized (remove whitespace, turn to lowercase) in the output.
def parse_entries(rows: list[tuple[str, str, str, list[str]]]) -> list[Entry]:
    entries: set[Entry] = set()
    # Maps a guest's email to their entry.
    entry_by_email: dict[str, Entry] = {}

    # Removes past entries for a single guest.
    def remove_previous(email: str):
        if email not in entry_by_email:
            return
        entry = entry_by_email[email]
        # If the entry was already removed, we do nothing, so use .discard
        entries.discard(entry)
        del entry_by_email[email]

    def add_entry(entry: Entry):
        for guest in entry.guests:
            remove_previous(guest.email)
            entry_by_email[guest.email] = entry

        entries.add(entry)

    all_emails = [email.strip().lower() for row in rows for email in row[1].split(",")]
    # feed in all emails as flattened list; it will be cached, and then we can just query again for individual rows
    _ = validate_email_batch(all_emails)

    for i, (names_str, emails_str, notes, extra_qs) in enumerate(rows, 2):
        names = [s.strip() for s in names_str.split(",")]
        emails = [email.strip().lower() for email in emails_str.split(",")]

        email_types = validate_email_batch(emails)
        entry, drop_reason = process_row(
            names, emails, email_types, notes=notes, extra_qs=extra_qs
        )
        if entry is None:
            assert drop_reason != DropReason.NO_DROP, (
                "Drop reason should be provided if None is returned"
            )
            match drop_reason:
                case DropReason.DUPLICATE_EMAILS:
                    msg = f"duplicate emails {emails}"
                case DropReason.MISSING_EMAIL:
                    msg = f"blank email for names: {names}, emails: {emails}"
                case DropReason.INVALID_EMAIL:
                    msg = f"invalid email in {emails}"
                case DropReason.MISMATCHED_COUNTS:
                    msg = f"mismatched counts between names: {names}, emails: {emails}"
                case _:
                    # Will catch errors if we add new affiliation types
                    assert False, "Unhandled drop reason"
            logger.data(f"Dropping row {i}; {msg}")
            continue
        # Blank names don't invalidate an entry, but flag them for cleaning
        if any(not guest.name for guest in entry.guests):
            logger.data(f"Blank name in row {i}; names: {names}, emails: {emails}")
        add_entry(entry)

    return list(entries)

# We turn newer version of data (v2) back into the old format
def reformat_sheet_v2(rows: list[dict], group_type: str) -> list[dict]:
    new_rows = []
    for row in rows:
        new_row = dict(row)  # start with all existing fields
        if group_type == "solo":
            # convert to known format
            new_row["names"] = new_row.pop("name")
            new_row["emails"] = new_row.pop("email")
        elif group_type == "group":
            new_row["names"] = f"{new_row.pop('name')}, {new_row.pop('guest_name')}"
            new_row["emails"] = f"{new_row.pop('email')}, {new_row.pop('guest_email')}"
        new_rows.append(new_row)
    return new_rows

class Database:
    def __init__(
        self,
        current_popup_id: str,
        window_size_years: int,
        success_penalty_fn: Callable[[float], float],
        rebuild: bool = False,
    ):
        self.current_popup_id = current_popup_id
        self.window_size_years = window_size_years
        self.success_penalty_fn = success_penalty_fn
        # keep track of each guest's score
        self.scores: dict[str, float] = defaultdict(float)
        # keep track of each guest's lottery attempt history
        self.attempted: dict[str, list[str]] = defaultdict(list)
        # keep track of each guest's attendance history
        self.attended: dict[str, list[str]] = defaultdict(list)
        # keep track of each guest's email type (last seen wins)
        self.email_types: dict[str, EmailType] = {}
        # keep track of email type counts per popup (for stats export)
        self.popup_entrant_types: dict[str, dict[str, int]] = {}
        self.init_db(rebuild)

    # will set .data_valid flag to True if successfully initialized
    # does stuff with fingerprint, cache
    def init_db(self, rebuild: bool):
        logger.info(f"Initializing database for popup `{self.current_popup_id}`")

        self.recent_popup_ids = self.get_recent_popups()

        # Note that group_score_reduce and weighting functions are not needed for history playback, so we don't include them in the fingerprint
        fingerprint = _compute_fingerprint(
            self.window_size_years,
            self.current_popup_id,
            self.success_penalty_fn,
        )

        if not rebuild and os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "rb") as f:
                cached = pickle.load(f)
            cache_age = time.time() - cached.get("timestamp", 0)
            if cached.get("fingerprint") == fingerprint and cache_age < 600:
                self.scores = defaultdict(float, cached["scores"])
                self.attempted = defaultdict(list, cached["attempted"])
                self.attended = defaultdict(list, cached["attended"])
                self.email_types = cached.get("email_types", {})
                self.popup_entrant_types = cached.get("popup_entrant_types", {})
                logger.info("Loaded database from cache")
                self.data_valid = True
                return
            if cache_age >= 600:
                logger.info("Cache expired — rebuilding")
            else:
                logger.info("Cache fingerprint mismatch — rebuilding")

        self.data_valid = check_history_folder(set(self.recent_popup_ids.keys()) | {self.current_popup_id})
        if not self.data_valid:
            logger.error(
                "History folder validation failed. Please fix the errors and try again."
            )
            return
        logger.info("History folder validated")

        self.history_playback()

        with open(CACHE_FILE, "wb") as f:
            pickle.dump(
                {
                    "fingerprint": fingerprint,
                    "timestamp": time.time(),
                    "scores": dict(self.scores),
                    "attempted": dict(self.attempted),
                    "attended": dict(self.attended),
                    "email_types": self.email_types,
                    "popup_entrant_types": self.popup_entrant_types,
                },
                f,
            )
        logger.info("Saved database to cache")

    def get_recent_popups(self) -> dict[str, tuple[datetime, int, str | None]]:
        # Maps popup ID to (date, lottery version)
        ids: dict[str, tuple[datetime, int, str | None]] = {}
        # Find all relevant popup IDs
        window_start = datetime.now() - timedelta(days=self.window_size_years * 365)
        logger.info(
            f"Considering popups from {window_start.strftime('%Y-%m-%d')} onwards"
        )
        prev_date = None
        with open("history/popups.csv", newline="", encoding="utf-8") as csvfile:
            rows = list(csv.DictReader(csvfile))
            for i, row in enumerate(rows):
                if i == len(rows) - 1:
                    assert row["id"] == self.current_popup_id, (
                        "The last popup in `popups.csv` should be the current popup"
                    )
                    self.current_popup_version = int(row["lottery_version"])
                    self.current_popup_group_type = row.get("group_type") or None
                    break  # Don't put the current popup in the database
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
                    ids[row["id"]] = (
                        date,
                        int(row["lottery_version"]),
                        row.get("group_type", None),
                    )
        logger.info(f"Found {len(ids)} popups: {list(ids.keys())}")
        return ids

    def process_past_popup(
        self,
        popup_id: str,
        date: datetime,
        lottery_version: int,
        group_type: str | None,
    ):
        # Process lottery first
        entries = load_lottery_entries(popup_id, lottery_version, group_type)
        guests = flatten_entries(entries)
        type_counts: dict[str, int] = defaultdict(int)
        for guest in guests:
            self.scores[guest.email] += 1
            self.attempted[guest.email].append(popup_id)
            self.email_types[guest.email] = guest.email_type
            type_counts[guest.email_type.value] += 1
        self.popup_entrant_types[popup_id] = dict(type_counts)
        # Then process guests from that popup
        with open(
            f"history/guests/{popup_id}_guests.csv", newline="", encoding="utf-8"
        ) as csvfile:
            rows = list(csv.DictReader(csvfile))
            guests = parse_guests([(row["name"], row["email"]) for row in rows])
            for guest in guests:
                self.scores[guest.email] = self.success_penalty_fn(
                    self.scores[guest.email]
                )
                # Also add to their attendance history
                self.attended[guest.email].append(popup_id)
                self.email_types[guest.email] = guest.email_type

    def history_playback(self):
        for popup_id, (
            date,
            lottery_version,
            group_type,
        ) in self.recent_popup_ids.items():
            self.process_past_popup(popup_id, date, lottery_version, group_type)

    def export_cumulative_data(self):
        logger.info("Exporting cumulative scores to `scores.csv`")
        with open("scores.csv", "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(
                ["email", "email_type", "score", "popups_attempted", "popups_attended"]
            )
            rows = list(self.scores.items())
            rows.sort(
                key=lambda x: (
                    x[1],
                    len(self.attempted.get(x[0], [])),
                    -len(self.attended.get(x[0], [])),
                ),
                reverse=True,
            )
            for email, score in rows:
                attempted = self.attempted.get(email, [])
                attended = self.attended.get(email, [])
                email_type = self.email_types.get(email, EmailType.NON_MIT).value
                writer.writerow(
                    [
                        email,
                        email_type,
                        score,
                        ", ".join(attempted),
                        ", ".join(attended),
                    ]
                )

        logger.info("Exporting past attendance to `past_attendance.csv`")
        with open("past_attendance.csv", "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["email", "email_type", "attended_popups"])
            # Sort attendance by len(popups) decreasing, then email alpha increasing
            rows = list(self.attended.items())
            rows.sort(key=lambda x: x[0])
            rows.sort(key=lambda x: len(x[1]), reverse=True)
            for email, popups in rows:
                email_type = self.email_types.get(email, EmailType.NON_MIT).value
                writer.writerow([email, email_type, ", ".join(popups)])

    def export_lottery_results(self, num_samples: int, group_score_reduce_fn: Callable[[list[float]], float], weighting_fn: Callable[[float], float]):
        """Draw num_samples entries; since entries can be 1-2 people, headcount is in [num_samples, 2*num_samples]."""
        logger.info(
            f"Exporting lottery results to `lottery_results_{self.current_popup_id}.csv`"
        )
        output_file = f"lottery_results_{self.current_popup_id}.csv"

        entries = load_lottery_entries(
            self.current_popup_id,
            self.current_popup_version,
            self.current_popup_group_type,
        )

        def group_score(emails):
            scores = []
            for email in emails:
                score = self.scores.get(email, 0) + 1
                scores.append(score)
            return group_score_reduce_fn(scores)

        guests_data = []
        weights = []

        num_extra_qs = len(entries[0].extra_qs) if entries else 0

        for entry in entries:
            emails = [guest.email for guest in entry.guests]
            score = group_score(emails)
            weight = weighting_fn(score)
            guests_data.append(
                {
                    "names": ", ".join([guest.name for guest in entry.guests]),
                    "emails": ", ".join([guest.email for guest in entry.guests]),
                    "email_types": ", ".join(
                        [guest.email_type.value for guest in entry.guests]
                    ),
                    "notes": entry.notes,
                    "extra_qs": entry.extra_qs,
                    "emails_list": emails,
                    "score": score,
                    "weight": weight,
                }
            )
            weights.append(weight)

        assert all(w > 0 for w in weights), "All weights must be positive"

        weights = np.array(weights) / np.sum(weights)  # normalize weights

        selected_rows = np.random.choice(
            guests_data, size=num_samples, replace=False, p=weights
        )

        extra_q_cols = [f"extraq_{i + 1}" for i in range(num_extra_qs)]
        with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(
                [
                    "names",
                    "emails",
                    "email_types",
                    "notes",
                    "score",
                    "weight",
                    "total_popups_attended",
                    "popups_attended",
                ]
                + extra_q_cols
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
                        row["email_types"],
                        row["notes"],
                        row["score"],
                        row["weight"],
                        total_attended,
                        ", ".join(sorted(unique_popups)),
                    ]
                    + list(row["extra_qs"])
                )

    def export_affiliations(self):
        logger.info("Exporting affiliation stats to `affiliations.csv`")

        # Read current popup's lottery CSV for its stats
        current_type_counts: dict[str, int] = defaultdict(int)
        current_input = f"history/lottery/{self.current_popup_id}_lottery.csv"
        if os.path.exists(current_input):
            entries = load_lottery_entries(
                self.current_popup_id,
                self.current_popup_version,
                self.current_popup_group_type,
            )
            for guest in flatten_entries(entries):
                current_type_counts[guest.email_type.value] += 1

        # Global unique-people counts from self.email_types
        global_counts: dict[str, int] = defaultdict(int)
        for email_type in self.email_types.values():
            global_counts[email_type.value] += 1

        with open("affiliations.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "popup_id",
                    "date",
                    "student",
                    "staff",
                    "affiliate",
                    "non_mit",
                    "total",
                ]
            )
            for popup_id, (date, _, _) in self.recent_popup_ids.items():
                counts = self.popup_entrant_types.get(popup_id, {})
                row_vals = [counts.get(t, 0) for t in EMAIL_TYPES]
                writer.writerow(
                    [popup_id, date.strftime("%Y.%m.%d")] + row_vals + [sum(row_vals)]
                )
            if current_type_counts:
                row_vals = [current_type_counts.get(t, 0) for t in EMAIL_TYPES]
                # Look up current popup date from popups.csv
                current_date = ""
                with open("history/popups.csv", newline="", encoding="utf-8") as pf:
                    for row in csv.DictReader(pf):
                        if row["id"] == self.current_popup_id:
                            current_date = row["date"]
                writer.writerow(
                    [f"{self.current_popup_id} (current)", current_date]
                    + row_vals
                    + [sum(row_vals)]
                )
            row_vals = [global_counts.get(t, 0) for t in EMAIL_TYPES]
            writer.writerow(["TOTAL (unique people)", ""] + row_vals + [sum(row_vals)])
