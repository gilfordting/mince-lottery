from database import Database
import logging
import math

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

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


if __name__ == "__main__":
    main()
