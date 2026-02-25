"""FastMCP server exposing Personal Capital financial data as Claude tools."""

import logging
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP

from .auth import SESSION_FILE, create_authenticated_client
from .client import PersonalCapitalAPI, categorize_accounts, summarize_holdings

logger = logging.getLogger(__name__)

mcp = FastMCP(name="personal-capital")

# Lazily initialized API client — created on first tool call.
_api: Optional[PersonalCapitalAPI] = None


def _get_api() -> PersonalCapitalAPI:
    global _api
    if _api is None:
        pc = create_authenticated_client()
        if pc is None:
            raise RuntimeError(
                "Not authenticated or session expired. "
                "Run: personal-capital-connector auth"
            )
        _api = PersonalCapitalAPI(pc)
    return _api


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def check_auth_status() -> str:
    """
    Check whether the Personal Capital session is authenticated and valid.
    Returns a status message. If not authenticated, explains how to fix it.
    """
    if not SESSION_FILE.exists():
        return (
            "No session found. Run `personal-capital-connector auth` to log in."
        )
    try:
        pc = create_authenticated_client()
        if pc:
            return "✓ Authenticated — session is valid."
        return (
            "Session exists but is expired or invalid. "
            "Run `personal-capital-connector auth` to re-authenticate."
        )
    except Exception as e:
        return f"Auth check failed: {e}"


@mcp.tool()
def list_accounts(
    type_filter: Annotated[
        Literal["all", "cash", "credit", "investment", "loan", "other"],
        "Filter by account category. Use 'all' to see everything.",
    ] = "all",
) -> str:
    """
    List all Personal Capital / Empower accounts with current balances.
    Accounts are grouped by type: cash (checking/savings), credit (credit cards),
    investment (brokerage/401k/IRA), loan (mortgage/auto/student), and other.
    Use type_filter to narrow to a specific category.
    """
    api = _get_api()
    data = api.get_accounts()
    categorized = categorize_accounts(data)
    groups = categorized["accounts"]

    group_labels = {
        "cash": "Cash & Bank Accounts",
        "credit": "Credit Cards",
        "investment": "Investment Accounts",
        "loan": "Loans & Mortgages",
        "other": "Other",
    }

    lines = [f"Net Worth: ${categorized['networth']:,.2f}\n"]

    for key, label in group_labels.items():
        if type_filter != "all" and type_filter != key:
            continue
        accounts = groups.get(key, [])
        if not accounts:
            continue

        group_total = sum(a["balance"] for a in accounts)
        lines.append(f"\n{label} (${group_total:,.2f} total)")

        for acct in accounts:
            bal = acct["balance"]
            line = f"  • {acct['name']}: ${bal:,.2f}"

            if acct.get("credit_limit"):
                util = abs(bal) / acct["credit_limit"] * 100
                line += f" | limit ${acct['credit_limit']:,.0f} ({util:.0f}% used)"
            if acct.get("available_credit") is not None:
                line += f" | available ${acct['available_credit']:,.2f}"
            if acct.get("payment_due_date"):
                line += f" | due {acct['payment_due_date']}"
            if acct.get("min_payment"):
                line += f" | min payment ${acct['min_payment']:,.2f}"
            if acct.get("interest_rate"):
                line += f" | {acct['interest_rate']:.2f}% APR"

            lines.append(line)

    return "\n".join(lines)


@mcp.tool()
def get_net_worth() -> str:
    """
    Get a summary of current net worth broken down into total assets vs total liabilities,
    with subtotals by account category (cash, investments, credit cards, loans).
    """
    api = _get_api()
    data = api.get_accounts()
    categorized = categorize_accounts(data)
    groups = categorized["accounts"]

    total_assets = sum(
        a["balance"]
        for grp in ("cash", "investment", "other")
        for a in groups[grp]
        if a.get("is_asset", True) and a["balance"] > 0
    )
    total_liabilities = sum(
        abs(a["balance"])
        for grp in ("credit", "loan")
        for a in groups[grp]
    )

    lines = [
        "Net Worth Summary",
        "=================",
        f"Net Worth:         ${categorized['networth']:,.2f}",
        f"Total Assets:      ${total_assets:,.2f}",
        f"Total Liabilities: ${total_liabilities:,.2f}",
        "",
        "By category:",
    ]
    for grp, label in [
        ("cash", "Cash & Bank"),
        ("investment", "Investments"),
        ("loan", "Loans"),
        ("credit", "Credit Cards"),
        ("other", "Other"),
    ]:
        accts = groups[grp]
        if accts:
            total = sum(a["balance"] for a in accts)
            lines.append(f"  {label}: ${total:,.2f}")

    return "\n".join(lines)


@mcp.tool()
def get_transactions(
    days: Annotated[int, "Number of days to look back (default 30)"] = 30,
    search: Annotated[
        str,
        "Filter by description or merchant name (case-insensitive, partial match)",
    ] = "",
    min_amount: Annotated[
        float,
        "Only return transactions with an absolute amount >= this value",
    ] = 0.0,
) -> str:
    """
    Fetch recent transactions from all connected accounts.
    Supports filtering by keyword (merchant/description) and minimum dollar amount.
    Returns up to 100 transactions sorted most-recent first.
    """
    api = _get_api()
    txns = api.get_transactions(days=days)

    search_lower = search.lower()
    if search_lower:
        txns = [
            t for t in txns
            if search_lower in (t.get("description") or "").lower()
            or search_lower in (t.get("originalDescription") or "").lower()
            or search_lower in (t.get("merchant") or "").lower()
            or search_lower in (t.get("accountName") or "").lower()
        ]

    if min_amount > 0:
        txns = [t for t in txns if abs(t.get("amount", 0)) >= min_amount]

    if not txns:
        return f"No transactions found for the past {days} days."

    txns.sort(key=lambda x: x.get("transactionDate", ""), reverse=True)

    header = f"Transactions — last {days} days ({len(txns)} found)"
    if search:
        header += f' matching "{search}"'
    lines = [header, ""]

    for txn in txns[:100]:
        date = (txn.get("transactionDate") or "")[:10]
        desc = txn.get("description") or txn.get("originalDescription") or ""
        amount = txn.get("amount", 0)
        acct = txn.get("accountName", "")
        pending = " (pending)" if txn.get("isPending") else ""
        sign = "+" if amount > 0 else ""
        lines.append(f"{date}  {sign}${amount:,.2f}  {desc}  [{acct}]{pending}")

    if len(txns) > 100:
        lines.append(f"\n(showing 100 of {len(txns)} transactions)")

    return "\n".join(lines)


@mcp.tool()
def get_asset_allocation(
    account_filter: Annotated[
        str,
        "Partial account name to filter to a single account (case-insensitive). Leave blank for all accounts.",
    ] = "",
) -> str:
    """
    Show investment holdings and asset allocation breakdown.
    Displays allocation by asset class (US Stocks, International Stocks, Bonds, etc.)
    with dollar values and percentages, then lists holdings per account.
    Use account_filter to focus on retirement accounts, a specific brokerage, etc.
    """
    api = _get_api()
    holdings = api.get_holdings()

    if account_filter:
        filter_lower = account_filter.lower()
        holdings = [
            h for h in holdings
            if filter_lower in (h.get("accountName") or "").lower()
        ]

    if not holdings:
        msg = "No holdings found."
        if account_filter:
            msg += f' (filter: "{account_filter}")'
        return msg

    summary = summarize_holdings(holdings)
    total = summary["total_value"]

    lines = [
        f"Investment Holdings — ${total:,.2f} total",
        "",
        "Asset Allocation:",
    ]

    for asset_class, info in summary["allocation"].items():
        bar = "█" * int(info["pct"] / 2)
        lines.append(
            f"  {asset_class:<30} ${info['value']:>12,.2f}  {info['pct']:5.1f}%  {bar}"
        )

    lines.append("")
    lines.append("Holdings by Account:")

    for acct_name, acct_holdings in summary["by_account"].items():
        acct_total = sum(h["value"] for h in acct_holdings)
        lines.append(f"\n  {acct_name} (${acct_total:,.2f})")

        for h in sorted(acct_holdings, key=lambda x: -x["value"])[:25]:
            ticker = f"[{h['ticker']}] " if h["ticker"] else ""
            lines.append(
                f"    • {ticker}{h['description']}: ${h['value']:,.2f} ({h['asset_class']})"
            )

    return "\n".join(lines)


def main() -> None:
    mcp.run(transport="stdio")
