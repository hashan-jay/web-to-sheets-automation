from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Transaction:
    transaction_id: str
    username: str = ""
    name: str = ""
    mobile: str = ""
    bank_account_name: str = ""
    bank_account_number: str = ""
    amount: str = ""
    bank: str = ""
    method: str = ""
    datetime: str = ""
    gateway: str = ""
    status: str = ""
    created: str = ""
    processed: str = ""
    brand: str = ""
    bsb: str = ""
    pay_id: str = ""
    bank_lock: str = ""
    extras: dict[str, str] = field(default_factory=dict)
