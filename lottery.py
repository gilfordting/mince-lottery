# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "requests", "python-dotenv", "pyyaml"]
# ///

import logging
import math

from database import Database

DATA = 15  # Between DEBUG (10) and INFO (20)
logging.addLevelName(DATA, "DATA")


def data(self, message, *args, **kwargs):
    if self.isEnabledFor(DATA):
        self._log(DATA, message, args, **kwargs)


logging.Logger.data = data

logging.basicConfig(level=DATA, format="[%(levelname)s] %(message)s")


def main():
    db = Database(
        current_popup_id="denmark",
        window_size_years=5,
        success_penalty_fn=lambda x: x - 10,
        rebuild=False,
    )
    assert db.data_valid, "Database validation failed"
    db.export_cumulative_data()
    # Higher temperature = more uniform weights, more randomness.
    # Low temperature = more concentrated weights, less randomness.
    temperature = 0.5
    db.export_lottery_results(
        num_samples=100,
        group_score_reduce_fn=min,
        weighting_fn=lambda x: math.exp(x / temperature),
    )
    db.export_affiliations()


if __name__ == "__main__":
    main()
