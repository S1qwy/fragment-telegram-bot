import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PayerIdentity:
    user_id: str
    user_name: str
    contact: str
    platega_email: str | None = None

    def platega_metadata(self):
        if not self.platega_email or not re.fullmatch(
            r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", self.platega_email
        ):
            raise ValueError("Platega requires the payer's valid email")
        return {
            "userId": self.user_id,
            "userName": self.user_name,
            "user_email": self.platega_email,
        }


def resolve_cispay_period(period_days, is_daily):
    if is_daily or type(period_days) is not int or not 7 <= period_days <= 360:
        raise ValueError("CisPay recurring interval must be 7–360 days")
    return period_days
