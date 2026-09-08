"""Fixed reference values for synthetic data generation."""

CATEGORIES = [
    "Electronics",
    "Clothing",
    "Home & Garden",
    "Groceries",
    "Books",
    "Toys & Games",
    "Sports & Outdoors",
    "Beauty & Personal Care",
]

# Each segment gets a distinct order-frequency range and amount adjustment so
# Phase 7's Correlation/Trend agents have a real counter-intuitive pattern to
# find: Budget customers order often but for less; Premium rarely but big.
SEGMENT_CONFIG = {
    "Budget": {"orders_per_customer": (8, 16), "amount_adjustment": -45},
    "Regular": {"orders_per_customer": (3, 8), "amount_adjustment": 0},
    "Premium": {"orders_per_customer": (1, 4), "amount_adjustment": 150},
    "New": {"orders_per_customer": (1, 2), "amount_adjustment": 15},
}
SEGMENT_WEIGHTS = {"Budget": 0.35, "Regular": 0.35, "Premium": 0.20, "New": 0.10}

SQL_INJECTION_PAYLOADS = [
    "Robert'); DROP TABLE customers;--",
    "Alice'); DROP TABLE cleaned_records;--",
]

TYPO_EMAIL_DOMAINS = ["gmial.com", "hotnail.com", "yaho.com", "outlok.com"]

NUMBER_WORDS = {
    18: "eighteen", 19: "nineteen", 20: "twenty", 21: "twenty-one",
    22: "twenty-two", 25: "twenty-five", 28: "twenty-eight", 30: "thirty",
    32: "thirty-two", 35: "thirty-five", 38: "thirty-eight", 40: "forty",
    42: "forty-two", 45: "forty-five", 48: "forty-eight", 50: "fifty",
    52: "fifty-two", 55: "fifty-five", 58: "fifty-eight", 60: "sixty",
    62: "sixty-two", 65: "sixty-five", 68: "sixty-eight", 70: "seventy",
}
