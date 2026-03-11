import re

EMAIL_FORMAT = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
# Sources: https://mitadmissions.org/blogs/entry/dont-screw-up-your-username/, https://ist.mit.edu/start/kerberos
KERB_FORMAT = re.compile(r"^[a-z0-9_]{3,8}@mit\.edu$")


def kerb_exists(kerb: str) -> bool:
    # TODO: use MIT people API
    pass


def is_mit_email(email: str) -> bool:
    return KERB_FORMAT.match(email) is not None


def is_email(email: str) -> bool:
    return EMAIL_FORMAT.match(email) is not None


# Checks if email is valid.
# If MIT email, check that in right format.
def email_valid(email: str) -> bool:
    if "@mit" in email:
        return is_mit_email(email)
    return is_email(email)


# Go through the log as follows:
# Go popup by popup.
# For each popup, first process lottery entries, then guests.
# Guests get clamped to 0.


# Two functions: one to run lottery (keep groups together), and one to get list of people who lotteried


def deduplicate():
    pass


# TODO: statistics for like, what are the current scores for people?
