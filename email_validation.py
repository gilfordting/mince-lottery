import concurrent.futures
import logging
import os
import re
from enum import Enum, auto
from functools import cache

import requests
import yaml
from dotenv import load_dotenv

logger = logging.getLogger("lottery")

load_dotenv()
MIT_PEOPLE_API_ENDPOINT = "https://mit-people-v3.cloudhub.io/people/v3/people/"
MIT_PEOPLE_CLIENT_ID = os.getenv("MIT_PEOPLE_API_CLIENT_ID")
MIT_PEOPLE_CLIENT_SECRET = os.getenv("MIT_PEOPLE_API_CLIENT_SECRET")


class Affiliation(Enum):
    STUDENT = auto()
    STAFF = auto()
    AFFILIATE = auto()
    NOT_FOUND = auto()
    WRONG_FORMAT = auto()  # email not formatted like MIT email


MAX_NUM_RETRIES = 3


@cache
def get_affiliation(kerb: str) -> Affiliation:
    """Check the affiliation of a Kerberos username via the MIT People API."""
    logger.debug(f"Checking affiliation of kerb `{kerb}`")

    def send_request(attempt_i):
        """
        Sends a single request to the MIT People API to check the affiliation of a Kerberos username.

        Returns None if the request failed for any reason, or the affiliation type if determinable.
        """
        try:
            resp = requests.get(
                MIT_PEOPLE_API_ENDPOINT + kerb,
                headers={
                    "client_id": MIT_PEOPLE_CLIENT_ID,
                    "client_secret": MIT_PEOPLE_CLIENT_SECRET,
                },
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            logger.debug(
                f"Connection error for kerb `{kerb}` (attempt {attempt_i + 1}/{MAX_NUM_RETRIES}), retrying..."
            )
            return None
        if resp.status_code != 200:
            return Affiliation.NOT_FOUND
        if not resp.text:
            logger.debug(
                f"Empty API response for kerb `{kerb}` (attempt {attempt_i + 1}/{MAX_NUM_RETRIES}), retrying..."
            )
            return None
        try:
            affiliation_type = resp.json()["item"]["affiliations"][0]["type"]
            match affiliation_type:
                case "student":
                    return Affiliation.STUDENT
                case "staff":
                    return Affiliation.STAFF
                case "affiliate":
                    return Affiliation.AFFILIATE
                case _:
                    logger.data(
                        f"kerb `{kerb}` was of unknown affiliate type `{affiliation_type}`"
                    )
                    return Affiliation.NOT_FOUND
        except requests.exceptions.JSONDecodeError:
            logger.debug(
                f"Non-JSON API response for kerb `{kerb}` (attempt {attempt_i + 1}/{MAX_NUM_RETRIES}), retrying..."
            )
            return None
        except (KeyError, IndexError):
            logger.data(
                f"Got strangely formatted API response for kerb `{kerb}`, assuming invalid email"
            )
            return Affiliation.NOT_FOUND

    for attempt in range(MAX_NUM_RETRIES):
        result = send_request(attempt)
        if result is not None:
            return result

    logger.data(
        f"MIT People API failed for kerb `{kerb}` after {MAX_NUM_RETRIES} attempts, assuming invalid email"
    )
    return Affiliation.NOT_FOUND

# These are known to be problematic kerbs (in terms of API/record-keeping), but should be correct
def _load_problem_kerbs() -> set[str]:
    path = os.path.join(os.path.dirname(__file__), "history", "problem_kerbs.yaml")
    with open(path) as f:
        entries = yaml.safe_load(f)
    return {entry["kerb"] for entry in entries}


EXCEPTIONS = _load_problem_kerbs()


def mit_email_affiliation(email: str) -> Affiliation:
    """Returns the affiliation of an email address. First, checks that that the email matches kerb format (lowercase alphanumeric/underscore, 2-8 chars before @mit.edu); then, extracts the kerb and checks affiliation via the MIT People API.

    Relevant sources: https://mitadmissions.org/blogs/entry/dont-screw-up-your-username/, https://ist.mit.edu/start/kerberos. Note that the length lower bound for kerbs is actually 2 characters, not 3 (e.g. eo@mit.edu is a valid kerb).
    """
    KERB_FORMAT = re.compile(r"^([a-z0-9_]{2,8})@mit\.edu$")
    # KERB_FORMAT = re.compile(r"^([a-z0-9_]+)@mit\.edu$")
    match = KERB_FORMAT.match(email)
    if not match:
        return Affiliation.WRONG_FORMAT
    kerb = match.group(1)
    if kerb in EXCEPTIONS:
        return Affiliation.AFFILIATE
    return get_affiliation(kerb)


def is_email(email: str) -> bool:
    """Return True if `email` is a syntactically valid email address."""
    EMAIL_FORMAT = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    return EMAIL_FORMAT.match(email) is not None


# TODO: fix because we can't differentiate between typo and alum


class EmailType(Enum):
    STUDENT = "student"  # current MIT student
    STAFF = "staff"  # MIT staff
    AFFILIATE = "affiliate"  # previously affiliated, probably alum
    NON_MIT = "non_mit"  # valid email, but not MIT-affiliated
    INVALID = "invalid"  # invalid email (typo or otherwise malformed)


def email_validation_batch(emails: list[str]) -> list[EmailType]:
    """Returns a list of EmailTypes, indicating the category each email falls into. Batches network requests for better performance."""

    def email_validation(email: str) -> bool:
        if not is_email(email):
            return EmailType.INVALID
        match mit_email_affiliation(email):
            case Affiliation.STUDENT:
                return EmailType.STUDENT
            case Affiliation.STAFF:
                return EmailType.STAFF
            case Affiliation.AFFILIATE:
                return EmailType.AFFILIATE
            case Affiliation.WRONG_FORMAT:
                # correctly formatted email, but not MIT format
                # kerbs that are too long will trigger this (i.e. very_long_kerb_over_8_characters@mit.edu)
                return EmailType.NON_MIT
            case Affiliation.NOT_FOUND:
                # formatted like an MIT email, but not found in the database
                return EmailType.INVALID
            case _:
                # Will catch errors if we add new affiliation types
                assert False, "Unhandled affiliation"

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        return list(executor.map(email_validation, emails))
