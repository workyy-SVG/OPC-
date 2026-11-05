"""Exact bounded quantities shared by CSV, UI, AI and inventory operations."""
from decimal import Decimal, InvalidOperation
import pandas as pd

MAX_QUANTITY = 1_000_000_000


def integer(value, minimum=0, maximum=MAX_QUANTITY):
    try:
        if pd.api.types.is_bool(value) or value is None:
            raise ValueError()
        number = Decimal(str(value).strip())
        if not number.is_finite() or number != number.to_integral_value() or not minimum <= number <= maximum:
            raise ValueError()
        return int(number)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'数量须为{minimum}至{maximum}之间的整数') from None

APP_REVISION = "audit-fixes-20261104"
