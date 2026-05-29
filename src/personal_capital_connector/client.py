"""Personal Capital API wrapper and data formatting helpers."""

import re
from datetime import datetime, timedelta

from personalcapital import PersonalCapital


def _safe_float(value) -> float | None:
    """Convert a value to float, returning None if not possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_last4(original_name: str | None) -> str | None:
    """Extract last 4 digits from originalName like '... Ending in 7783'."""
    if not original_name:
        return None
    match = re.search(r'[Ee]nding in\s+(\d{4})\s*$', original_name)
    return match.group(1) if match else None


class PersonalCapitalAPI:
    """Thin wrapper around PersonalCapital with typed data-fetch methods."""

    def __init__(self, pc: PersonalCapital):
        self.pc = pc

    def get_accounts(self) -> dict:
        """Fetch all accounts. Returns the spData payload from /newaccount/getAccounts."""
        response = self.pc.fetch("/newaccount/getAccounts")
        data = response.json()
        if not data.get("spHeader", {}).get("success"):
            raise RuntimeError(f"getAccounts failed: {data.get('spHeader', {}).get('errors', [])}")
        return data.get("spData", {})

    def get_transactions(self, days: int = 30) -> list:
        """Fetch transactions for the past `days` days."""
        end = datetime.now()
        start = end - timedelta(days=days)
        response = self.pc.fetch(
            "/transaction/getUserTransactions",
            {
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d"),
            },
        )
        data = response.json()
        if not data.get("spHeader", {}).get("success"):
            raise RuntimeError(f"getTransactions failed: {data.get('spHeader', {}).get('errors', [])}")
        return data.get("spData", {}).get("transactions", [])

    def get_holdings(self) -> list:
        """Fetch investment holdings from /invest/getHoldings."""
        response = self.pc.fetch("/invest/getHoldings")
        data = response.json()
        if not data.get("spHeader", {}).get("success"):
            raise RuntimeError(f"getHoldings failed: {data.get('spHeader', {}).get('errors', [])}")
        return data.get("spData", {}).get("holdings", [])


# ---------------------------------------------------------------------------
# Formatting helpers (pure functions — no API calls)
# ---------------------------------------------------------------------------

def categorize_accounts(accounts_data: dict, hide_zero_balance: bool = False) -> dict:
    """
    Group accounts from spData into cash / credit / investment / loan / other.
    Returns a dict with 'networth', 'accounts' (grouped), and 'total_accounts'.
    Accounts with a closedDate are always excluded.
    Zero-balance accounts are excluded when hide_zero_balance is True (default).
    """
    accounts = accounts_data.get("accounts", [])
    networth = accounts_data.get("networth", 0)

    groups: dict[str, list] = {
        "cash": [],
        "credit": [],
        "investment": [],
        "loan": [],
        "other": [],
    }

    included = 0
    for acct in accounts:
        # Skip closed accounts
        if acct.get("closedDate"):
            continue

        balance = _safe_float(acct.get("balance")) or 0.0

        # Skip zero-balance accounts when requested
        if hide_zero_balance and balance == 0.0:
            continue

        grp = (acct.get("accountTypeGroup") or "").upper()
        prod = (acct.get("productType") or "").upper()

        # Build a human-readable name: prefer the user-assigned 'name' field,
        # fall back to firmName + accountType.
        display_name = acct.get("name") or ""
        if not display_name:
            firm = acct.get("firmName") or ""
            acct_type = acct.get("accountType") or ""
            display_name = f"{firm} {acct_type}".strip()

        last4 = _extract_last4(acct.get("originalName"))
        if last4:
            display_name = f"{display_name} (…{last4})"

        entry = {
            "name": display_name,
            "firm": acct.get("firmName", ""),
            "type": acct.get("accountType", ""),
            "subtype": acct.get("accountTypeSubtype", ""),
            "balance": balance,
            "is_asset": acct.get("isAsset", True),
            "is_manual": acct.get("isManual", False),
            "currency": acct.get("currency", "USD"),
            "last_refreshed": acct.get("lastRefreshed", ""),
            # Credit card fields
            "credit_limit": _safe_float(acct.get("creditLimit")),
            "available_credit": _safe_float(acct.get("availableCredit")),
            "min_payment": _safe_float(acct.get("minPayment")),
            "payment_due_date": acct.get("paymentDueDate"),
            # Loan fields
            "interest_rate": _safe_float(acct.get("interestRate")),
            "original_loan_amount": _safe_float(acct.get("originalLoanAmount")),
        }

        if grp == "BANK" or prod in ("BANK", "CHECKING", "SAVINGS", "CD", "MONEY_MARKET"):
            groups["cash"].append(entry)
        elif grp == "CREDIT_CARD" or prod == "CREDIT_CARD":
            groups["credit"].append(entry)
        elif grp in ("RETIREMENT", "INVESTMENT", "EDUCATIONAL", "HEALTH") or prod in (
            "INVESTMENT", "401K", "IRA", "ROTH_IRA", "BROKERAGE", "529", "SEP_IRA",
            "SIMPLE_IRA", "403B", "PENSION", "ANNUITY", "STOCK_PLAN", "HSA",
        ):
            groups["investment"].append(entry)
        elif grp in ("LOAN", "MORTGAGE") or prod in (
            "LOAN", "MORTGAGE", "AUTO_LOAN", "STUDENT_LOAN", "HOME_EQUITY", "PERSONAL_LOAN",
        ):
            groups["loan"].append(entry)
        else:
            groups["other"].append(entry)

        included += 1

    return {
        "networth": networth,
        "accounts": groups,
        "total_accounts": included,
    }


def summarize_holdings(holdings: list) -> dict:
    """
    Compute asset class allocation and per-account holdings breakdown.

    Returns:
        {
            "total_value": float,
            "allocation": {asset_class: {"value": float, "pct": float}},
            "by_account": {account_name: [holding_dict, ...]},
        }
    """
    asset_classes: dict[str, float] = {}
    by_account: dict[str, list] = {}
    total_value = 0.0

    for h in holdings:
        value = h.get("value", 0) or 0
        asset_class = h.get("assetClass", "Unknown") or "Unknown"
        account_name = h.get("accountName", "Unknown") or "Unknown"

        total_value += value
        asset_classes[asset_class] = asset_classes.get(asset_class, 0) + value

        if account_name not in by_account:
            by_account[account_name] = []
        by_account[account_name].append({
            "ticker": h.get("ticker", ""),
            "description": h.get("description", ""),
            "shares": h.get("quantity", 0),
            "price": h.get("price", 0),
            "value": value,
            "asset_class": asset_class,
        })

    allocation = {
        ac: {
            "value": v,
            "pct": (v / total_value * 100) if total_value else 0,
        }
        for ac, v in sorted(asset_classes.items(), key=lambda x: -x[1])
    }

    return {
        "total_value": total_value,
        "allocation": allocation,
        "by_account": by_account,
    }
