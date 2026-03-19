import csv
import os
import logging
import re
from email_validation import EmailType, email_validation_batch


logger = logging.getLogger("lottery")


def columns_match(filename, columns_expected):
    """Return True if the CSV file's header exactly matches columns_expected."""
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        return columns == columns_expected


def check_lottery_sheets():
    """Checks that the lottery sheets have the correct columns ('names', 'emails', 'notes'). Logs errors for each violation found, and warnings for extraneous files. Returns True if no errors were found, False otherwise."""
    no_errors = True
    lottery_dir = os.path.join("history", "lottery")
    lottery_expected_cols = ["names", "emails", "notes"]
    for file in os.listdir(lottery_dir):
        if not file.endswith(".csv"):
            logger.warning(f"Skipping non-CSV file in lottery/ folder: {file}")
            continue
        path = os.path.join(lottery_dir, file)
        if not columns_match(path, lottery_expected_cols):
            logger.error(
                f"Lottery file `{file}` does not have the columns {lottery_expected_cols}"
            )
            no_errors = False

    return no_errors


def check_guests_sheets():
    """Checks that the guests sheets have the correct columns ('name', 'email') and that the data inside is valid (nonempty name, valid email). Logs errors for each violation found, and warnings for extraneous files. Returns True if no errors were found, False otherwise."""
    no_errors = True

    guests_dir = os.path.join("history", "guests")
    guests_expected_cols = ["name", "email"]
    for file in os.listdir(guests_dir):
        if not file.endswith(".csv"):
            logger.warning(f"Skipping non-CSV file in guests/ folder: {file}")
            continue
        path = os.path.join(guests_dir, file)
        if not columns_match(path, guests_expected_cols):
            logger.error(
                f"Guests file `{file}` does not have the columns {guests_expected_cols}"
            )
            no_errors = False
            continue

        # for guests, we also check that the data inside is valid, since we shouldn't just drop rows with invalid data like for lottery sheets
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            names = [row["name"].strip() for row in rows]
            emails = [row["email"].strip() for row in rows]
            email_types = email_validation_batch(emails)

            for i, (name, email, email_type) in enumerate(
                zip(names, emails, email_types), 2
            ):
                if not name:
                    logger.error(f"{file}, row {i}: empty name")
                    no_errors = False
                    continue
                if email_type == EmailType.INVALID:
                    logger.error(f"{file}, row {i}: invalid email `{email}`")
                    no_errors = False
                    continue

    return no_errors


def check_metadata_sheet():
    """Checks that the metadata sheet has the correct columns ('name', 'date', 'id') and that the data inside is valid (nonempty name, valid date, unique id). Logs errors for each violation found."""
    popup_csv = "history/popups.csv"
    lottery_dir = os.path.join("history", "lottery")
    guests_dir = os.path.join("history", "guests")

    if not os.path.exists(popup_csv):
        logger.error(f"{popup_csv} not found")
        return False
    with open(popup_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        expected_columns = ["name", "date", "id"]
        if not columns_match(popup_csv, expected_columns):
            logger.error(
                f"{popup_csv} does not have the expected columns {expected_columns}"
            )
            return False

        seen_ids = set()
        for i, row in enumerate(reader, 2):
            popup_id = row["id"].strip()
            popup_name = row["name"].strip()
            popup_date = row["date"].strip()

            if not popup_name:
                logger.error(f"{popup_csv}, row {i}: empty name")
            if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", popup_date):
                logger.error(
                    f"{popup_csv}, row {i}: invalid date `{popup_date}` (expected YYYY.MM.DD)"
                )
            if not popup_id:
                logger.error(f"{popup_csv}, row {i}: empty id")
            elif popup_id in seen_ids:
                logger.error(f"{popup_csv}, row {i}: duplicate id `{popup_id}`")
            else:
                seen_ids.add(popup_id)

            guests_file = os.path.join(guests_dir, f"{popup_id}_guests.csv")
            lottery_file = os.path.join(lottery_dir, f"{popup_id}_lottery.csv")
            if not os.path.exists(guests_file):
                logger.error(f"Missing guests file: {guests_file}")
            if not os.path.exists(lottery_file):
                logger.error(f"Missing lottery file: {lottery_file}")
    return True


def check_history_folder():
    """Validates the structure and contents of the history/ folder. See above functions for more details; details are logged as they are encountered.

    If there are critical errors, the function will return False, signaling that the history/ folder must be fixed before continuing.
    """
    results = [check_guests_sheets(), check_lottery_sheets(), check_metadata_sheet()]
    return all(results)
