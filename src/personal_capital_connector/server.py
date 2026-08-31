"""FastMCP server exposing Personal Capital financial data as Claude tools."""

import logging
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP

from .auth import SESSION_FILE, create_authenticated_client
from .client import (
    PersonalCapitalAPI,
    account_name,
    categorize_accounts,
    summarize_holdings,
)

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
    hide_zero_balance: Annotated[
        bool,
        "Hide accounts with a $0 balance (e.g. old/inactive accounts). Default false.",
    ] = False,
) -> str:
    """
    List all Personal Capital / Empower accounts with current balances.
    Accounts are grouped by type: cash (checking/savings), credit (credit cards),
    investment (brokerage/401k/IRA), loan (mortgage/auto/student), and other.
    Closed accounts are always excluded. Zero-balance accounts are shown by default.
    Use type_filter to narrow to a specific category.
    """
    api = _get_api()
    data = api.get_accounts()
    categorized = categorize_accounts(data, hide_zero_balance=hide_zero_balance)
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

            credit_limit = acct.get("credit_limit")
            if credit_limit:
                util = abs(bal) / credit_limit * 100
                line += f" | limit ${credit_limit:,.0f} ({util:.0f}% used)"
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
    categorized = categorize_accounts(data, hide_zero_balance=False)
    groups = categorized["accounts"]

    # Empower signs both sides positive: an asset balance and an amount owed are
    # both reported as positive numbers. Do not coerce the sign here — a credit
    # card carrying a credit, or an overdrawn checking account, legitimately comes
    # back negative and has to net against its own side of the balance sheet.
    #
    # Which side an account lands on is decided per account, not per group. The
    # credit and loan groups are liabilities by construction; anywhere else, a
    # false is_asset marks a liability (a manual mortgage, an escrow balance)
    # that would otherwise be missing from both totals.
    total_assets = 0.0
    total_liabilities = 0.0
    for grp, accts in groups.items():
        for a in accts:
            if grp in ("credit", "loan") or not a.get("is_asset", True):
                total_liabilities += a["balance"]
            else:
                total_assets += a["balance"]

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
    days: Annotated[
        int, "Days to look back from today. Ignored when start_date is set. Default 30."
    ] = 30,
    start_date: Annotated[
        str, "Start of an explicit date range, YYYY-MM-DD. Overrides `days`."
    ] = "",
    end_date: Annotated[
        str, "End of an explicit date range, YYYY-MM-DD. Defaults to today."
    ] = "",
    search: Annotated[
        str,
        "Match description, original description, or merchant. Case-insensitive "
        "substring. Comma-separated terms are ORed: 'avis, hertz, national'.",
    ] = "",
    account: Annotated[
        str, "Limit to one account. Case-insensitive substring of the account name."
    ] = "",
    category: Annotated[
        str, "Limit to one Personal Capital category, e.g. 'travel'. Substring match."
    ] = "",
    min_amount: Annotated[
        float, "Only transactions with absolute amount >= this value."
    ] = 0.0,
    max_amount: Annotated[
        float, "Only transactions with absolute amount <= this value. 0 means no cap."
    ] = 0.0,
    limit: Annotated[int, "Maximum rows to return. Default 100."] = 100,
    offset: Annotated[
        int, "Skip this many rows first. Page through results with limit + offset."
    ] = 0,
    oldest_first: Annotated[bool, "Sort oldest first instead of newest first."] = False,
) -> str:
    """
    Fetch transactions from all connected accounts.

    Query either a date range (start_date/end_date) or a lookback window (days).
    Filter by keyword, account, category, and amount range. Results are paged with
    limit and offset, and the header always reports how many matched in total, so
    truncation is never silent.

    Amounts are signed: money out is negative, money in is positive.
    """
    api = _get_api()
    txns = api.get_transactions(
        days=days,
        start_date=start_date or None,
        end_date=end_date or None,
    )

    terms = [t.strip().lower() for t in search.split(",") if t.strip()]
    if terms:
        txns = [
            t for t in txns
            if any(
                term in (t.get("description") or "").lower()
                or term in (t.get("originalDescription") or "").lower()
                or term in (t.get("merchant") or "").lower()
                for term in terms
            )
        ]

    if account:
        needle = account.lower()
        txns = [t for t in txns if needle in (t.get("accountName") or "").lower()]

    if category:
        needle = category.lower()
        txns = [t for t in txns if needle in (t.get("categoryName") or "").lower()]

    if min_amount > 0:
        txns = [t for t in txns if abs(_amount(t)) >= min_amount]
    if max_amount > 0:
        txns = [t for t in txns if abs(_amount(t)) <= max_amount]

    if not txns:
        return f"No transactions found. ({_range_label(days, start_date, end_date)})"

    txns.sort(key=lambda x: x.get("transactionDate") or "", reverse=not oldest_first)

    matched = len(txns)
    offset = max(offset, 0)
    page = txns[offset:offset + limit] if limit > 0 else txns[offset:]

    # Magnitude from the amount, direction from isCredit — the same rule the rows
    # below use, so the header total always equals the sum of what is displayed.
    money_in = sum(abs(_amount(t)) for t in txns if t.get("isCredit"))
    money_out = sum(abs(_amount(t)) for t in txns if not t.get("isCredit"))

    header = f"Transactions — {_range_label(days, start_date, end_date)}"
    lines = [header]

    shown = f"{len(page)} of {matched} matched"
    if offset:
        shown += f", starting at {offset}"
    lines.append(shown)
    lines.append(
        f"Matched total: net ${money_in - money_out:,.2f}  "
        f"(in ${money_in:,.2f} / out ${money_out:,.2f})"
    )
    if search:
        lines.append(f'Search: "{search}"')
    lines.append("")

    for txn in page:
        date = (txn.get("transactionDate") or "")[:10]
        desc = txn.get("description") or txn.get("originalDescription") or ""
        amount = abs(_amount(txn))
        signed = amount if txn.get("isCredit") else -amount
        acct = txn.get("accountName") or ""
        cat = txn.get("categoryName") or ""
        flags = ""
        if txn.get("isPending"):
            flags += " (pending)"
        if txn.get("isDuplicate"):
            flags += " (duplicate)"
        cat_part = f"  ({cat})" if cat else ""
        lines.append(
            f"{date}  {signed:>12,.2f}  {desc}  [{acct}]{cat_part}{flags}"
        )

    remaining = matched - (offset + len(page))
    if remaining > 0:
        lines.append(
            f"\n{remaining} more. Re-run with offset={offset + len(page)} to continue."
        )

    return "\n".join(lines)


def _amount(txn: dict) -> float:
    """A transaction's amount. Pending rows can carry a null, which reads as 0."""
    return txn.get("amount") or 0.0


def _range_label(days: int, start_date: str, end_date: str) -> str:
    """Human-readable description of the window that was queried."""
    if start_date:
        return f"{start_date} to {end_date or 'today'}"
    if end_date:
        return f"{days} days ending {end_date}"
    return f"last {days} days"


def _with_account_names(api: PersonalCapitalAPI, holdings: list) -> list:
    """
    Fill in accountName on holdings that carry only a userAccountId.

    Empower omits accountName on some holdings (cash sweep rows in particular),
    which would otherwise collect them all under a single "Unknown" account and
    put them out of reach of account_filter. Resolving costs one extra request,
    so it is skipped when every holding already names its account.
    """
    if all(h.get("accountName") for h in holdings):
        return holdings

    try:
        accounts = api.get_accounts().get("accounts") or []
    except Exception as e:
        # The names are an enrichment; a failure here should not sink the tool.
        logger.warning("Could not resolve account names for holdings: %s", e)
        return holdings

    # Deliberately the undecorated name, without the "(…1234)" suffix that
    # list_accounts adds: it has to match the names the holdings feed supplies
    # for the same account, or one account renders as two buckets.
    names = {
        str(a["userAccountId"]): account_name(a)
        for a in accounts
        if a.get("userAccountId") is not None and account_name(a)
    }

    resolved = []
    for h in holdings:
        if not h.get("accountName"):
            name = names.get(str(h.get("userAccountId")))
            if name:
                h = {**h, "accountName": name}
        resolved.append(h)
    return resolved


@mcp.tool()
def get_asset_allocation(
    account_filter: Annotated[
        str,
        "Partial account name to filter to a single account (case-insensitive). Leave blank for all accounts.",
    ] = "",
) -> str:
    """
    Show investment holdings and how the portfolio is spread across them.

    Empower's holdings feed carries no asset class, so this breaks down by
    individual holding (fund or security) with dollar values and percentages,
    then lists holdings per account. It cannot report a stock/bond/international
    split: funds are not decomposed into their components.

    Use account_filter to focus on retirement accounts, a specific brokerage, etc.
    """
    api = _get_api()
    holdings = _with_account_names(api, api.get_holdings())

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
        "Allocation by holding:",
    ]

    for name, info in summary["allocation"].items():
        bar = "█" * int(info["pct"] / 2)
        # Fund names run long; keep the value column aligned.
        label = name if len(name) <= 40 else name[:39] + "…"
        lines.append(
            f"  {label:<40} ${info['value']:>12,.2f}  {info['pct']:5.1f}%  {bar}"
        )

    lines.append("")
    lines.append("Holdings by Account:")

    for acct_name, acct_holdings in summary["by_account"].items():
        acct_total = sum(h["value"] for h in acct_holdings)
        lines.append(f"\n  {acct_name} (${acct_total:,.2f})")

        for h in sorted(acct_holdings, key=lambda x: -x["value"])[:25]:
            ticker = f"[{h['ticker']}] " if h["ticker"] else ""
            kind = f" ({h['holding_type']})" if h["holding_type"] else ""
            lines.append(
                f"    • {ticker}{h['description']}: ${h['value']:,.2f}{kind}"
            )

    return "\n".join(lines)


def main() -> None:
    mcp.run(transport="stdio")
