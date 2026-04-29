def format_number(number: int | None) -> str:
    return f"{number:,}" if number is not None else "0"
