"""One-off probe: find the API host and list what the access token returns."""
from __future__ import annotations

import os
import re
import sys

import requests

TOKEN = os.environ["CUNTWIN_TOKEN"]
ACCESS = os.environ["CUNTWIN_ACCESS_ID"]


def main() -> None:
    page = requests.post(
        "https://skgaming23.as6868.com/transactions/getAllTransactions",
        data={"pageIndex": "0"},
        headers={"Accept": "text/html"},
        timeout=20,
    )
    print("page", page.status_code, len(page.text))
    scripts = re.findall(r'src="([^"]+)"', page.text)
    print("scripts", len(scripts))
    for src in scripts:
        print("SRC", src)


if __name__ == "__main__":
    main()
