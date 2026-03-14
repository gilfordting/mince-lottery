# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "requests", "python-dotenv"]
# ///

from database import Database
import logging
import math

DATA = 15  # Between DEBUG (10) and INFO (20)
logging.addLevelName(DATA, "DATA")


def data(self, message, *args, **kwargs):
    if self.isEnabledFor(DATA):
        self._log(DATA, message, args, **kwargs)


logging.Logger.data = data

# logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
logging.basicConfig(level=DATA, format="[%(levelname)s] %(message)s")

def main():
    temperature = 1
    db = Database(
        current_popup_id="entropy",
        window_size_years=5,
        group_score_reduce_fn=lambda x: min(x),
        success_penalty_fn=lambda x: x - 10,
        weighting_fn=lambda x: math.exp(x / temperature),
    )
    assert db.data_valid, "Database validation failed"
    db.export_cumulative_data()
    db.export_lottery_results(num_samples=100)
    db.export_affiliations()


if __name__ == "__main__":
    main()
