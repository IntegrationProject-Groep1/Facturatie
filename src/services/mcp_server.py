"""
Facturatie MCP Server — full read access to FossBilling + MySQL.

Two data sources:
  FossBilling REST API  — clients, invoices, revenue stats
  MySQL                 — pending_consumptions, invoice_registry, company_accounts

Run standalone:
    python mcp_server.py
or via fastmcp:
    fastmcp run mcp_server.py:mcp --transport streamable-http --port 8007

Environment variables:
    BILLING_API_URL         FossBilling API base URL (default: http://fossbilling/api)
    BILLING_API_USERNAME    API basic-auth username (default: admin)
    BILLING_API_TOKEN       API basic-auth password / token
    DB_HOST                 MySQL host (default: mysql)
    MYSQL_DATABASE          MySQL database name (default: fossbilling)
    MYSQL_USER              MySQL user
    MYSQL_PASSWORD          MySQL password
    PORT                    HTTP port (default: 8007)
"""
import os
from typing import Any

import aiomysql
import httpx
from fastmcp import FastMCP

mcp = FastMCP("facturatie")

# ── FossBilling ───────────────────────────────────────────────────
_API_URL  = os.getenv("BILLING_API_URL",      "http://fossbilling/api").rstrip("/")
_API_USER = os.getenv("BILLING_API_USERNAME", "admin")
_API_TOKEN = os.getenv("BILLING_API_TOKEN",   "")
_HEADERS  = {"X-Forwarded-Proto": "https"}

_http = httpx.AsyncClient(timeout=15.0)

# ── MySQL ─────────────────────────────────────────────────────────
_DB_HOST = os.getenv("DB_HOST",        os.getenv("MYSQL_HOST", "mysql"))
_DB_NAME = os.getenv("MYSQL_DATABASE", "fossbilling")
_DB_USER = os.getenv("MYSQL_USER",     "")
_DB_PASS = os.getenv("MYSQL_PASSWORD", "")

_pool: aiomysql.Pool | None = None


async def _get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=_DB_HOST, db=_DB_NAME,
            user=_DB_USER, password=_DB_PASS,
            autocommit=True, minsize=1, maxsize=5,
        )
    return _pool


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _err(msg: str, **extra) -> dict:
    return {"error": str(msg), **extra}


async def _fb(endpoint: str, data: dict | None = None) -> dict:
    """POST to the FossBilling admin API. Returns the parsed result dict."""
    resp = await _http.post(
        f"{_API_URL}/{endpoint}",
        data=data or {},
        auth=(_API_USER, _API_TOKEN),
        headers=_HEADERS,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        msg = body["error"].get("message", "unknown FossBilling error")
        raise ValueError(f"FossBilling: {msg}")
    return body.get("result", {})


async def _query(sql: str, args: tuple = ()) -> list[dict]:
    """Run a SELECT on MySQL, returns list of row dicts."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


async def _scalar(sql: str, args: tuple = ()):
    """Run a SELECT that returns a single scalar value."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            row = await cur.fetchone()
            return row[0] if row else None


# ─────────────────────────────────────────────
#  FOSSBILLING — CLIENTS (internal helpers only)
#  For person identity queries use CRM (crm__get_member_by_email etc.)
# ─────────────────────────────────────────────

async def _get_client_by_email(email: str) -> dict[str, Any]:
    """Internal: resolve FossBilling client record by email."""
    result = await _fb("admin/client/get_list", {"search": email, "per_page": 50})
    clients = result.get("list", [])
    match = next((c for c in clients if c.get("email", "").lower() == email.lower()), None)
    if not match:
        raise ValueError(f"No FossBilling client found with email '{email}'")


@mcp.tool()
async def get_client_invoices(
    client_id: int,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Get all invoices for a specific FossBilling client.
    Optionally filter by status: 'unpaid', 'paid', 'cancelled', 'refunded'.
    """
    payload: dict = {"client_id": client_id, "per_page": min(limit, 200)}
    if status:
        payload["status"] = status
    try:
        result = await _fb("admin/invoice/get_list", payload)
        return {
            "invoices": result.get("list", []),
            "total":    result.get("total", 0),
            "client_id": client_id,
        }
    except Exception as exc:
        return _err(exc, client_id=client_id, invoices=[])


@mcp.tool()
async def get_invoices_by_email(
    email: str,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Get invoices for a person by their email address.
    status: 'unpaid' | 'paid' | 'cancelled' | 'refunded' | None (all).
    Resolves the FossBilling client internally — no separate client lookup needed.
    Primary tool for 'what invoices does X have?' or 'does X have outstanding bills?'
    """
    try:
        client = await _get_client_by_email(email)
        client_id = client.get("id")
        return await get_client_invoices(client_id, status=status, limit=limit)
    except Exception as exc:
        return _err(exc, email=email, invoices=[])


@mcp.tool()
async def get_client_balance(client_id: int) -> dict[str, Any]:
    """Get the outstanding (unpaid) invoice total for a client."""
    try:
        result = await _fb("admin/invoice/get_list", {
            "client_id": client_id,
            "status": "unpaid",
            "per_page": 200,
        })
        invoices = result.get("list", [])
        total_unpaid = sum(float(inv.get("total", 0)) for inv in invoices)
        return {
            "client_id":      client_id,
            "unpaid_invoices": len(invoices),
            "total_outstanding_eur": round(total_unpaid, 2),
        }
    except Exception as exc:
        return _err(exc, client_id=client_id)


# ─────────────────────────────────────────────
#  FOSSBILLING — INVOICES
# ─────────────────────────────────────────────

@mcp.tool()
async def list_invoices(
    status: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> dict[str, Any]:
    """
    List invoices from FossBilling.
    status: 'unpaid' | 'paid' | 'cancelled' | 'refunded' | None (all)

    Use status='unpaid' for outstanding receivables, status='paid' for paid
    invoices, etc. Authoritative FossBilling data.
    """
    payload: dict = {"per_page": min(limit, 200), "page": page}
    if status:
        payload["status"] = status
    try:
        result = await _fb("admin/invoice/get_list", payload)
        return {
            "invoices": result.get("list", []),
            "total":    result.get("total", 0),
            "page":     page,
        }
    except Exception as exc:
        return _err(exc, invoices=[])


@mcp.tool()
async def get_invoice(invoice_id: int) -> dict[str, Any]:
    """Get the full invoice object from FossBilling, including line items."""
    try:
        return await _fb("admin/invoice/get", {"id": invoice_id})
    except Exception as exc:
        return _err(exc, invoice_id=invoice_id)


@mcp.tool()
async def get_recent_invoices(limit: int = 20) -> dict[str, Any]:
    """Get the most recently created invoices (all statuses)."""
    return await list_invoices(limit=limit, page=1)


@mcp.tool()
async def get_invoices_by_date_range(
    start_date: str,
    end_date: str,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Get invoices created between start_date and end_date (format: 'YYYY-MM-DD').
    Optionally filter by status: 'unpaid' | 'paid' | 'cancelled' | 'refunded'.
    Useful for 'invoices from last month' or 'invoices from the event weekend'.
    """
    payload: dict = {"per_page": min(limit, 200), "date_from": start_date, "date_to": end_date}
    if status:
        payload["status"] = status
    try:
        result = await _fb("admin/invoice/get_list", payload)
        invoices = result.get("list", [])
        return {
            "invoices": invoices,
            "total": result.get("total", len(invoices)),
            "start_date": start_date,
            "end_date": end_date,
            "status": status,
        }
    except Exception as exc:
        return _err(exc, invoices=[])


@mcp.tool()
async def get_overdue_invoices(limit: int = 50) -> dict[str, Any]:
    """
    Get unpaid invoices whose due date has already passed.
    Sorted oldest-first so the most overdue appear first.
    Use this for collections, chasing payments, or overdue reporting.
    """
    from datetime import date
    today = date.today().isoformat()
    try:
        result = await _fb("admin/invoice/get_list", {
            "status": "unpaid",
            "per_page": min(limit, 200),
        })
        invoices = result.get("list", [])
        overdue = [
            inv for inv in invoices
            if inv.get("due_date") and inv["due_date"] < today
        ]
        overdue.sort(key=lambda inv: inv.get("due_date", ""))
        total_eur = round(sum(float(inv.get("total", 0)) for inv in overdue), 2)
        return {
            "invoices": overdue,
            "count": len(overdue),
            "total_overdue_eur": total_eur,
            "as_of": today,
        }
    except Exception as exc:
        return _err(exc, invoices=[])


@mcp.tool()
async def get_invoice_line_items(invoice_id: int) -> dict[str, Any]:
    """Get the individual line items (products/services) on a specific invoice."""
    try:
        invoice = await _fb("admin/invoice/get", {"id": invoice_id})
        lines   = invoice.get("lines", [])
        return {
            "invoice_id": invoice_id,
            "status":     invoice.get("status"),
            "total":      invoice.get("total"),
            "client_id":  invoice.get("client_id"),
            "lines":      lines,
            "line_count": len(lines),
        }
    except Exception as exc:
        return _err(exc, invoice_id=invoice_id)


@mcp.tool()
async def get_revenue_summary() -> dict[str, Any]:
    """
    Revenue summary across all invoices:
    total paid, total unpaid (outstanding), total cancelled, invoice counts per status.

    AUTHORITATIVE for invoiced/accounting revenue. For real-time on-site POS
    revenue use `kassa__get_sales_summary`; for log-derived revenue proxy
    use `monitoring__get_payment_revenue` (NOT authoritative).
    """
    statuses = ["paid", "unpaid", "cancelled", "refunded"]
    summary: dict[str, Any] = {}
    total_paid = 0.0
    total_outstanding = 0.0

    for status in statuses:
        try:
            result   = await _fb("admin/invoice/get_list", {"status": status, "per_page": 200})
            invoices = result.get("list", [])
            total    = result.get("total", len(invoices))
            amount   = sum(float(inv.get("total", 0)) for inv in invoices)
            summary[status] = {"count": total, "amount_eur": round(amount, 2)}
            if status == "paid":
                total_paid = amount
            if status == "unpaid":
                total_outstanding = amount
        except Exception as exc:
            summary[status] = {"error": str(exc)}

    return {
        "by_status":           summary,
        "total_paid_eur":      round(total_paid, 2),
        "total_outstanding_eur": round(total_outstanding, 2),
    }


@mcp.tool()
async def get_registration_invoices(status: str | None = None, limit: int = 100) -> dict[str, Any]:
    """
    Get invoices that contain registration fees ('Inschrijvingskosten').
    These come from new_registration messages. Optionally filter by status.
    """
    try:
        payload: dict = {"per_page": min(limit, 200)}
        if status:
            payload["status"] = status
        result   = await _fb("admin/invoice/get_list", payload)
        invoices = result.get("list", [])
        reg_invs = []
        for inv in invoices:
            # Check if invoice_type from registry is registration, or try to detect from lines
            lines = inv.get("lines", [])
            is_reg = any("inschrijvingskosten" in str(l.get("title", "")).lower() for l in lines)
            if is_reg:
                reg_invs.append(inv)
        return {"invoices": reg_invs, "count": len(reg_invs)}
    except Exception as exc:
        return _err(exc, invoices=[])


@mcp.tool()
async def get_payment_gateways() -> dict[str, Any]:
    """List all configured payment gateways in FossBilling."""
    try:
        result = await _fb("admin/invoice/gateway_get_list", {})
        return {"gateways": result.get("list", [])}
    except Exception as exc:
        return _err(exc, gateways=[])


@mcp.tool()
async def check_fossbilling_status() -> dict[str, Any]:
    """Check if the FossBilling API is reachable and credentials are valid."""
    try:
        result = await _fb("admin/client/get_list", {"per_page": 1})
        return {"status": "online", "api_url": _API_URL}
    except Exception as exc:
        return {"status": "offline", "error": str(exc), "api_url": _API_URL}


# ─────────────────────────────────────────────
#  MYSQL — PENDING CONSUMPTIONS
# ─────────────────────────────────────────────

@mcp.tool()
async def get_pending_consumptions(company_id: str | None = None) -> dict[str, Any]:
    """
    Get all pending consumption items waiting to be invoiced after event end.
    Optionally filter by company_id. These are accumulated bar/catering orders per company.

    Consumption items in the post-event invoicing staging area (MySQL).
    For LIVE POS orders during the event use `kassa__get_recent_orders`;
    for the CRM master record use `crm__list_consumptions`.
    """
    try:
        if company_id:
            rows = await _query(
                "SELECT * FROM pending_consumptions WHERE company_id = %s ORDER BY received_at DESC",
                (company_id,),
            )
        else:
            rows = await _query("SELECT * FROM pending_consumptions ORDER BY received_at DESC")
        # Convert Decimal/datetime to serializable types
        clean = [_clean_row(r) for r in rows]
        return {"items": clean, "count": len(clean)}
    except Exception as exc:
        return _err(exc, items=[])


@mcp.tool()
async def get_companies_with_pending() -> dict[str, Any]:
    """
    List all companies that currently have pending consumption items
    that haven't been invoiced yet.

    Staging-area data from MySQL pending_consumptions. For live event sales
    use Kassa; for member-linked consumption history use CRM Consumption__c.
    """
    try:
        rows = await _query("""
            SELECT
                company_id,
                company_name,
                COUNT(*)          AS item_count,
                SUM(price * quantity) AS total_amount,
                MIN(received_at)  AS first_item_at,
                MAX(received_at)  AS last_item_at
            FROM pending_consumptions
            GROUP BY company_id, company_name
            ORDER BY total_amount DESC
        """)
        clean = [_clean_row(r) for r in rows]
        total_value = sum(float(r.get("total_amount") or 0) for r in clean)
        return {"companies": clean, "count": len(clean), "total_pending_eur": round(total_value, 2)}
    except Exception as exc:
        return _err(exc, companies=[])


@mcp.tool()
async def get_pending_summary_by_company() -> dict[str, Any]:
    """
    Summary of pending consumption value per company:
    item count, total amount, description breakdown.
    """
    try:
        rows = await _query("""
            SELECT
                company_id,
                company_name,
                description,
                SUM(quantity)         AS total_qty,
                AVG(price)            AS unit_price,
                SUM(price * quantity) AS line_total
            FROM pending_consumptions
            GROUP BY company_id, company_name, description
            ORDER BY company_id, line_total DESC
        """)
        clean = [_clean_row(r) for r in rows]
        # Group by company
        companies: dict[str, dict] = {}
        for r in clean:
            cid = r["company_id"]
            if cid not in companies:
                companies[cid] = {
                    "company_id":   cid,
                    "company_name": r["company_name"],
                    "items": [],
                    "total_eur": 0.0,
                }
            companies[cid]["items"].append({
                "description": r["description"],
                "total_qty":   r["total_qty"],
                "unit_price":  float(r["unit_price"] or 0),
                "line_total":  float(r["line_total"] or 0),
            })
            companies[cid]["total_eur"] += float(r["line_total"] or 0)

        result = list(companies.values())
        for c in result:
            c["total_eur"] = round(c["total_eur"], 2)
        result.sort(key=lambda c: c["total_eur"], reverse=True)
        return {"companies": result, "count": len(result)}
    except Exception as exc:
        return _err(exc, companies=[])


@mcp.tool()
async def get_pending_consumption_stats() -> dict[str, Any]:
    """
    Overall pending consumption statistics: total items, total value, number of companies.

    Aggregated from MySQL pending_consumptions (staging for post-event invoicing).
    Not the same as live POS revenue (Kassa) or member-linked consumption history (CRM).
    """
    try:
        row = await _query("""
            SELECT
                COUNT(*)                      AS total_items,
                COUNT(DISTINCT company_id)    AS total_companies,
                SUM(price * quantity)         AS total_value,
                MIN(received_at)              AS oldest_item,
                MAX(received_at)              AS newest_item
            FROM pending_consumptions
        """)
        if not row:
            return {"total_items": 0, "total_companies": 0, "total_value": 0}
        r = _clean_row(row[0])
        return {
            "total_items":     r.get("total_items", 0),
            "total_companies": r.get("total_companies", 0),
            "total_value_eur": round(float(r.get("total_value") or 0), 2),
            "oldest_item_at":  r.get("oldest_item"),
            "newest_item_at":  r.get("newest_item"),
        }
    except Exception as exc:
        return _err(exc, total_items=0, total_companies=0, total_value_eur=0)


# ─────────────────────────────────────────────
#  MYSQL — INVOICE REGISTRY
# ─────────────────────────────────────────────

@mcp.tool()
async def get_invoice_registry(limit: int = 50) -> dict[str, Any]:
    """
    List entries from the local invoice registry (invoice_id ↔ correlation_id mappings).
    This tracks which FossBilling invoice corresponds to each integration message.

    The ONLY way to trace a specific RabbitMQ message through to its invoice.
    """
    try:
        rows = await _query(
            "SELECT * FROM invoice_registry ORDER BY created_at DESC LIMIT %s",
            (min(limit, 500),),
        )
        clean = [_clean_row(r) for r in rows]
        return {"registry": clean, "count": len(clean)}
    except Exception as exc:
        return _err(exc, registry=[])


@mcp.tool()
async def lookup_invoice_by_correlation(correlation_id: str) -> dict[str, Any]:
    """
    Look up the FossBilling invoice_id linked to a specific RabbitMQ correlation_id.

    The ONLY way to trace a specific message through to its invoice.
    """
    try:
        rows = await _query(
            "SELECT * FROM invoice_registry WHERE correlation_id = %s LIMIT 1",
            (correlation_id,),
        )
        if not rows:
            return _err(f"No invoice found for correlation_id '{correlation_id}'")
        return _clean_row(rows[0])
    except Exception as exc:
        return _err(exc, correlation_id=correlation_id)


@mcp.tool()
async def get_registry_by_type(invoice_type: str) -> dict[str, Any]:
    """
    Get registry entries filtered by invoice type.
    invoice_type: 'registration' | 'consumption'
    """
    try:
        rows = await _query(
            "SELECT * FROM invoice_registry WHERE invoice_type = %s ORDER BY created_at DESC",
            (invoice_type,),
        )
        clean = [_clean_row(r) for r in rows]
        return {"registry": clean, "count": len(clean), "invoice_type": invoice_type}
    except Exception as exc:
        return _err(exc, registry=[])


@mcp.tool()
async def get_registry_stats() -> dict[str, Any]:
    """Registry statistics: total invoices tracked, split by type, oldest and newest."""
    try:
        rows = await _query("""
            SELECT
                invoice_type,
                COUNT(*)     AS count,
                MIN(created_at) AS first_at,
                MAX(created_at) AS last_at
            FROM invoice_registry
            GROUP BY invoice_type
        """)
        clean = [_clean_row(r) for r in rows]
        total = await _scalar("SELECT COUNT(*) FROM invoice_registry")
        return {"by_type": clean, "total": total or 0}
    except Exception as exc:
        return _err(exc)


# ─────────────────────────────────────────────
#  MYSQL — COMPANY ACCOUNTS
# ─────────────────────────────────────────────

@mcp.tool()
async def get_company_billing_accounts() -> dict[str, Any]:
    """
    List all companies that have a FossBilling billing account mapped.
    These are companies whose consumption orders are invoiced after event end.

    Maps Kassa/CRM company_id → FossBilling client_id. The bridge between the
    member-side (CRM) and billing-side (FossBilling) views of a company.
    """
    try:
        rows = await _query(
            "SELECT * FROM company_accounts ORDER BY created_at DESC"
        )
        clean = [_clean_row(r) for r in rows]
        return {"company_accounts": clean, "count": len(clean)}
    except Exception as exc:
        return _err(exc, company_accounts=[])


@mcp.tool()
async def get_company_billing_account(company_id: str) -> dict[str, Any]:
    """
    Look up the FossBilling client mapping for a specific company_id.

    Bridges Kassa/CRM company_id → FossBilling client_id.
    """
    try:
        rows = await _query(
            "SELECT * FROM company_accounts WHERE company_id = %s LIMIT 1",
            (company_id,),
        )
        if not rows:
            return _err(f"No billing account found for company_id '{company_id}'")
        return _clean_row(rows[0])
    except Exception as exc:
        return _err(exc, company_id=company_id)


@mcp.tool()
async def get_company_pending_and_billing(company_id: str) -> dict[str, Any]:
    """
    Combined view for a company: their FossBilling client ID, pending
    consumption items and total outstanding amount.
    """
    billing_task = get_company_billing_account(company_id)
    pending_task = get_pending_consumptions(company_id)

    import asyncio
    billing, pending = await asyncio.gather(billing_task, pending_task)

    total = sum(
        float(item.get("price", 0)) * int(item.get("quantity", 1))
        for item in pending.get("items", [])
    )
    return {
        "company_id":       company_id,
        "billing_account":  billing,
        "pending_items":    pending.get("items", []),
        "pending_count":    pending.get("count", 0),
        "pending_total_eur": round(total, 2),
    }


# ─────────────────────────────────────────────
#  PLATFORM OVERVIEW
# ─────────────────────────────────────────────

@mcp.tool()
async def get_facturatie_overview() -> dict[str, Any]:
    """
    Full billing platform overview:
    FossBilling invoice counts/revenue + pending consumptions + registry stats.
    Single call for a complete admin dashboard.
    """
    import asyncio
    revenue, pending_stats, registry_stats, companies_pending = await asyncio.gather(
        get_revenue_summary(),
        get_pending_consumption_stats(),
        get_registry_stats(),
        get_companies_with_pending(),
    )
    return {
        "invoices":            revenue.get("by_status", {}),
        "total_paid_eur":      revenue.get("total_paid_eur"),
        "total_outstanding_eur": revenue.get("total_outstanding_eur"),
        "pending_consumptions": {
            "items":     pending_stats.get("total_items"),
            "companies": pending_stats.get("total_companies"),
            "value_eur": pending_stats.get("total_value_eur"),
        },
        "registry":            registry_stats,
        "companies_pending":   companies_pending.get("companies", []),
    }


# ─────────────────────────────────────────────
#  Utility
# ─────────────────────────────────────────────

def _clean_row(row: dict) -> dict:
    """Convert MySQL Decimal and datetime values to JSON-serializable types."""
    import decimal, datetime
    out = {}
    for k, v in row.items():
        if isinstance(v, decimal.Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime.datetime, datetime.date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8007")),
    )
