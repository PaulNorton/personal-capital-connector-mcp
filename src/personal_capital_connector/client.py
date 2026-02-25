"""Personal Capital API wrapper and data formatting helpers."""

from datetime import datetime, timedelta

from personalcapital import PersonalCapital


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

def categorize_accounts(accounts_data: dict) -> dict:
    """
    Group accounts from spData into cash / credit / investment / loan / other.
    Returns a dict with 'networth', 'accounts' (grouped), and 'total_accounts'.
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

    for acct in accounts:
        grp = acct.get("accountTypeGroup", "").upper()
        prod = acct.get("productType", "").upper()

        entry = {
            "name": f"{acct.get('firmName', '')} {acct.get('accountName', '')}".strip(),
            "firm": acct.get("firmName", ""),
            "account_name": acct.get("accountName", ""),
            "type": acct.get("accountType", ""),
            "balance": acct.get("balance", 0),
            "is_asset": acct.get("isAsset", True),
            "currency": acct.get("currency", "USD"),
            "last_refreshed": acct.get("lastRefreshed", ""),
            # Credit card fields
            "credit_limit": acct.get("creditLimit"),
            "available_credit": acct.get("availableCredit"),
            "min_payment": acct.get("minPayment"),
            "payment_due_date": acct.get("paymentDueDate"),
            # Loan fields
            "interest_rate": acct.get("interestRate"),
            "original_loan_amount": acct.get("originalLoanAmount"),
        }

        if grp == "BANK" or prod in ("CHECKING", "SAVINGS", "CD", "MONEY_MARKET"):
            groups["cash"].append(entry)
        elif grp == "CREDIT_CARD" or prod == "CREDIT_CARD":
            groups["credit"].append(entry)
        elif grp == "INVESTMENT" or prod in (
            "401K", "IRA", "ROTH_IRA", "BROKERAGE", "529", "SEP_IRA",
            "SIMPLE_IRA", "403B", "PENSION", "ANNUITY", "STOCK_PLAN", "HSA",
        ):
            groups["investment"].append(entry)
        elif grp == "LOAN" or prod in (
            "MORTGAGE", "AUTO_LOAN", "STUDENT_LOAN", "HOME_EQUITY", "PERSONAL_LOAN",
        ):
            groups["loan"].append(entry)
        else:
            groups["other"].append(entry)

    return {
        "networth": networth,
        "accounts": groups,
        "total_accounts": len(accounts),
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
