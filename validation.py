import csv
import logging
import os
import re
from datetime import datetime

from email_validation import EmailType, validate_email_batch

logger = logging.getLogger(__name__)


def columns_match(filename, columns_expected):
    """Return True if the CSV file's header exactly matches columns_expected."""
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        return columns == columns_expected

def columns_exist(filename, columns_required):
    """Return True if the CSV file's header contains all columns_required (order-independent)."""
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or [])
        return set(columns_required).issubset(columns)


def check_metadata_sheet():
    """Checks that the metadata sheet has the correct columns ('name', 'date', 'id', 'lottery_version', 'group_type') and that the data inside is valid (nonempty name, valid date, unique id, lottery version; proper evolution of extra columns.). Logs errors for each violation found."""
    popup_csv = "history/popups.csv"
    lottery_dir = os.path.join("history", "lottery")
    guests_dir = os.path.join("history", "guests")

    if not os.path.exists(popup_csv):
        logger.error(f"{popup_csv} not found")
        return False

    no_errors = True

    def err(message: str):
        nonlocal no_errors
        logger.error(message)
        no_errors = False

    with open(popup_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        expected_columns = ["name", "date", "id", "lottery_version", "group_type"]
        if not columns_match(popup_csv, expected_columns):
            err(f"{popup_csv} does not have the expected columns {expected_columns}")
            return False

        seen_ids = set()
        last_date = datetime.min
        for i, row in enumerate(rows, 2):
            # Popup IDs must exist and be unique; corresponding guests/lottery .csv's must also exist
            id = row["id"].strip()
            if not id:
                err(f"{popup_csv}, row {i}: empty id")
            elif id in seen_ids:
                err(f"{popup_csv}, row {i}: duplicate id `{id}`")
            else:
                seen_ids.add(id)
            lottery_file = os.path.join(lottery_dir, f"{id}_lottery.csv")
            if not os.path.exists(lottery_file):
                err(f"Missing lottery file: {lottery_file}")
            guests_file = os.path.join(guests_dir, f"{id}_guests.csv")
            if i != len(rows) + 1 and not os.path.exists(guests_file):
                err(f"Missing guests file: {guests_file}")

            # Popup names must exist and be nonempty
            name = row["name"].strip()
            if not name:
                err(f"{popup_csv}, row {i}: empty name")

            # Popup dates must be valid and in increasing order
            date = row["date"].strip()
            if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", date):
                err(
                    f"{popup_csv}, row {i}: invalid date `{date}` (expected YYYY.MM.DD)"
                )
            else:
                date = datetime.strptime(date, "%Y.%m.%d")
                if date <= last_date:
                    err(
                        f"{popup_csv}, row {i}: date is not after previous popup's date"
                    )
                last_date = date

            # If lottery version >= 2, must have provided group_type as "solo" or "group"
            lottery_version = int(row["lottery_version"])
            if lottery_version >= 2:
                if "group_type" not in row:
                    err(
                        f"{popup_csv}, row {i}: missing group_type for lottery_version >= 2"
                    )
                    continue
                group_type = row["group_type"]
                if group_type not in ["solo", "group"]:
                    err(
                        f"{popup_csv}, row {i}: invalid group type `{group_type}` (expected 'solo' or 'group')"
                    )

    return no_errors


def check_guests_sheets(popup_ids: set[str]):
    """Checks that the guests sheets for the given popup_ids have the correct columns ('name', 'email') and that the data inside is valid (nonempty name, valid email). Logs errors for each violation found. Returns True if no errors were found, False otherwise."""
    no_errors = True

    def err(message: str):
        nonlocal no_errors
        logger.error(message)
        no_errors = False

    guests_dir = os.path.join("history", "guests")
    guests_expected_cols = ["name", "email"]
    csv_files = [f"{id}_guests.csv" for id in popup_ids]

    # Warm the email validation cache for all guest sheets at once so API calls
    # across all files fire concurrently rather than sequentially per file.
    all_emails = []
    for file in csv_files:
        path = os.path.join(guests_dir, file)
        if os.path.exists(path) and columns_match(path, guests_expected_cols):
            with open(path, newline="", encoding="utf-8") as f:
                all_emails.extend(row["email"].strip() for row in csv.DictReader(f))
    _ = validate_email_batch(all_emails)

    for file in csv_files:
        path = os.path.join(guests_dir, file)
        if not os.path.exists(path):
            continue
        if not columns_match(path, guests_expected_cols):
            err(
                f"Guests file `{file}` does not match the expected columns {guests_expected_cols}"
            )
            continue

        # for guests, we also check that the data inside is valid, since we shouldn't just drop rows with invalid data like for lottery sheets
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            names = [row["name"].strip() for row in rows]
            emails = [row["email"].strip() for row in rows]
            email_types = validate_email_batch(emails)

            for i, (name, email, email_type) in enumerate(
                zip(names, emails, email_types), 2
            ):
                if not name:
                    err(f"{file}, row {i}: empty name")
                if email_type == EmailType.INVALID:
                    err(f"{file}, row {i}: invalid email `{email}`")

    return no_errors


def check_lottery_sheet_v1(popup_id: str):
    """Checks that the lottery sheet for the given popup has the correct columns ('names', 'emails', 'notes'). Logs errors for each violation found. Returns True if no errors were found, False otherwise."""
    no_errors = True

    def err(message: str):
        nonlocal no_errors
        logger.error(message)
        no_errors = False

    expected_cols = ["names", "emails", "notes"]

    path = os.path.join("history", "lottery", f"{popup_id}_lottery.csv")
    if not os.path.exists(path):
        err(f"Lottery file `{path}` does not exist")
        return False
    if not columns_match(path, expected_cols):
        err(
            f"Lottery file `{path}` does not match the expected columns {expected_cols}"
        )
    return no_errors


def check_lottery_sheet_v2(popup_id: str, group_type: str):
    """Checks that the v2 lottery sheet for the given popup has required columns based on group_type.
    Logs errors for each violation found. Returns True if no errors, False otherwise.
    """
    no_errors = True

    def err(message: str):
        nonlocal no_errors
        logger.error(message)
        no_errors = False

    path = os.path.join("history", "lottery", f"{popup_id}_lottery.csv")
    if not os.path.exists(path):
        err(f"Lottery file `{path}` does not exist")
        return False

    # Determine expected columns by group_type
    match group_type:
        case "solo":
            expected_cols = ["name", "email", "notes"]
        case "group":
            expected_cols = ["name", "email", "guest_name", "guest_email", "notes"]
        case _:
            err(f"Unknown group_type `{group_type}` for popup `{popup_id}`")
            return False

    if not columns_exist(path, expected_cols):
        err(f"Lottery file `{path}` does not have the required columns {expected_cols}")
    return no_errors


def check_lottery_sheets(popup_ids: set[str]):
    """
    Checks that lottery sheets for the given popup_ids have the correct columns for their sheet version.
    Feeds each popup row to either check_lottery_sheet_v1 or check_lottery_sheet_v2 as appropriate.
    Logs errors for each violation found, and warnings for extraneous files in the lottery folder.
    Returns True if no errors were found, False otherwise.
    """
    no_errors = True
    popup_csv = "history/popups.csv"
    lottery_dir = os.path.join("history", "lottery")

    def err(message: str):
        nonlocal no_errors
        logger.error(message)
        no_errors = False

    # Track files seen in popups.csv
    referenced_files = set()

    with open(popup_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for i, row in enumerate(reader, 2):
            popup_id = row["id"].strip()
            referenced_files.add(f"{popup_id}_lottery.csv")
            if popup_id not in popup_ids:
                continue
            lottery_version = int(row["lottery_version"])
            if lottery_version == 1:
                if not check_lottery_sheet_v1(popup_id):
                    no_errors = False
            elif lottery_version == 2:
                group_type = row["group_type"]
                if not check_lottery_sheet_v2(popup_id, group_type):
                    no_errors = False
            else:
                err(
                    f"{popup_csv}, row {i}: invalid lottery_version `{lottery_version}`"
                )

    # Warn for extraneous files in the lottery directory
    for file in os.listdir(lottery_dir):
        if not file.endswith(".csv"):
            logger.warning(f"Skipping non-CSV file in lottery/ folder: {file}")
            continue
        if file not in referenced_files:
            logger.warning(f"Extraneous lottery file in lottery/ folder: {file}")

    return no_errors



def check_history_folder(popup_ids: set[str]):
    """Validates the structure and contents of the history/ folder for the given popup_ids. See above functions for more details; details are logged as they are encountered.

    If there are critical errors, the function will return False, signaling that the history/ folder must be fixed before continuing.
    """
    results = [check_guests_sheets(popup_ids), check_lottery_sheets(popup_ids), check_metadata_sheet()]
    return all(results)
