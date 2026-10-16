# Azure SRE Agent Canvas tool name: AzureCostDetails-Query
# Identity tab: enable ARM. Set the tool timeout to 300 seconds.
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()


def main(
    subscription_id: str,
    start_date: str,
    end_date: str,
    metric: str = "both",
    group_by: str = "resource",
    resource_id: str = "",
    meter_categories: str = "",
    offset: int = 0,
    page_size: int = 100,
    max_wait_seconds: int = 90,
) -> dict:
    """Aggregated billed cost from the Cost Management Query API.

    Second path for billed cost, next to AzureCostDetails-Report and -Read: use it when
    Cost Details is rate limited (HTTP 429) or not supported for the subscription, or for
    a period of more than three months. Dates are YYYY-MM-DD, inclusive, any span up to
    366 days in one call. metric: ActualCost, AmortizedCost, or both. group_by: resource
    (one record per ResourceId and currency, with pricing models) or meter (one record per
    meter subcategory and pricing model; pass resource_id to analyze one resource).
    meter_categories: comma-separated MeterCategory filter, for example
    "Virtual Machines,Storage". No raw rows, quantities, prices, tags, or reservation IDs.
    A wait up to 30 seconds is retried here; a longer wait is returned as retry.
    Read only; JSON only.
    """
    import re
    import time
    from datetime import datetime
    from decimal import Decimal
    from email.utils import parsedate_to_datetime
    from urllib.parse import unquote, urlparse
    from uuid import UUID

    import requests

    arm = "https://management.azure.com"
    api_version = "2023-03-01"
    # ClientType identifies this caller to Cost Management, so it does not share the rate-limit bucket of anonymous callers.
    client_type = "DevOpsStyle-AzureUtilities-CostDetails"
    max_pages, output_limit = 20, 60_000
    deadline = 0.0
    dimensions = {"resource": ["ResourceId", "PricingModel"], "meter": ["MeterSubcategory", "PricingModel"]}
    fields = {"ActualCost": "cost", "AmortizedCost": "amortized_cost"}
    sid = ""

    def retry_seconds(headers):
        delays = []
        for key, value in headers.items():
            key = key.lower()
            if key == "retry-after" or (key.startswith("x-ms-ratelimit-") and key.endswith("retry-after")):
                try:
                    delays.append(float(value))
                except ValueError:
                    try:
                        delays.append(parsedate_to_datetime(value).timestamp() - time.time())
                    except (ValueError, TypeError):
                        pass
        return int(max([1] + delays)) + 1 if delays else 30

    def rate_limit(headers):
        return {k.lower(): v for k, v in headers.items()
                if k.lower() == "retry-after" or k.lower().startswith("x-ms-ratelimit-")}

    def checked_link(link):
        parsed = urlparse(link)
        prefix = f"/subscriptions/{sid}/providers/microsoft.costmanagement/query"
        if (parsed.scheme != "https" or parsed.netloc.lower() != "management.azure.com"
                or not parsed.path.lower().startswith(prefix) or "/../" in parsed.path or parsed.fragment):
            raise ValueError("Unexpected nextLink returned by Azure")
        return link

    def post(url, body):
        attempt = 0
        while True:
            attempt += 1
            token = credential.get_token(arm + "/.default").token
            with requests.post(url, json=body, headers={"Authorization": "Bearer " + token, "ClientType": client_type},
                               timeout=(10, 60), allow_redirects=False) as response:
                code = response.status_code
                if code == 204:
                    return {"status": "ok", "body": {}}
                if code == 200:
                    try:
                        return {"status": "ok", "body": response.json()}
                    except ValueError:
                        return {"status": "error", "message": "Azure returned a response that is not JSON"}
                if code in (429, 500, 502, 503, 504):
                    delay = retry_seconds(response.headers)
                    if delay <= 30 and attempt < 3 and time.monotonic() + delay + 5 < deadline:
                        time.sleep(delay)
                        continue
                    return {"status": "retry", "http_status": code, "retry_after_seconds": delay,
                            "rate_limit": rate_limit(response.headers),
                            "message": f"HTTP {code}; wait before repeating the call"}
                if code in (401, 403):
                    return {"status": "forbidden", "http_status": code,
                            "message": "The identity cannot read the costs of this subscription. Assign Cost Management "
                                       "Reader on the subscription and check that ARM is enabled in the Identity tab."}
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                detail = re.sub(r"https?://\S+", "[URL omitted]", str(error.get("message", "")))[:500]
                return {"status": "error", "http_status": code, "azure_code": error.get("code"),
                        "message": detail or "The Cost Management query was not successful"}

    def run(metric_type):
        dataset = {"granularity": "None", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                   "grouping": [{"type": "Dimension", "name": name} for name in dimensions[group_by]]}
        filters = []
        if categories:
            filters.append({"dimensions": {"name": "MeterCategory", "operator": "In", "values": categories}})
        if resource_filter:
            filters.append({"dimensions": {"name": "ResourceId", "operator": "In", "values": [resource_filter]}})
        if filters:
            dataset["filter"] = filters[0] if len(filters) == 1 else {"and": filters}
        body = {"type": metric_type, "timeframe": "Custom", "timePeriod": {"from": start, "to": end}, "dataset": dataset}
        url, pages, rows = f"{arm}/subscriptions/{sid}/providers/Microsoft.CostManagement/query?api-version={api_version}", 0, []
        while url:
            pages += 1
            result = post(url, body)
            if result["status"] != "ok":
                return result
            props = result["body"].get("properties") or {}
            columns = {c["name"].lower(): i for i, c in enumerate(props.get("columns") or [])}
            needed = ["cost", "currency"] + [name.lower() for name in dimensions[group_by]]
            if props.get("rows") and any(name not in columns for name in needed):
                return {"status": "error", "message": "The response has no column "
                        + ", ".join(n for n in needed if n not in columns)}
            for row in props.get("rows") or []:
                rows.append({name: row[columns[name]] for name in needed})
            link = props.get("nextLink")
            url = checked_link(link) if link else ""
            if url and pages >= max_pages:
                return {"status": "error", "message": f"The result has more than {max_pages} pages: narrow the period or "
                        "the filters, or use AzureCostDetails-Report and -Read"}
        return {"status": "ok", "rows": rows, "row_count": len(rows)}

    try:
        deadline = time.monotonic() + max(10, min(int(max_wait_seconds), 240))
        sid = str(UUID(re.sub(r"^/subscriptions/", "", subscription_id.strip(), flags=re.I).strip("/")))
        start = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        stop = datetime.strptime(end_date.strip(), "%Y-%m-%d").date()
        if start > stop or (stop - start).days > 365:
            raise ValueError("Use ordered dates spanning at most 366 days; split a longer period into several calls")
        start, end = start.isoformat(), stop.isoformat()
        wanted = {"actual": ["ActualCost"], "actualcost": ["ActualCost"], "amortized": ["AmortizedCost"],
                  "amortizedcost": ["AmortizedCost"], "both": ["ActualCost", "AmortizedCost"]}.get((metric or "").strip().lower())
        if not wanted:
            raise ValueError("metric must be ActualCost, AmortizedCost, or both")
        group_by = (group_by or "").strip().lower()
        if group_by not in dimensions:
            raise ValueError("group_by must be resource or meter")
        if int(offset) < 0 or not 1 <= int(page_size) <= 200:
            raise ValueError("Use offset >= 0 and page_size between 1 and 200")
        offset, page_size = int(offset), int(page_size)
        categories = [part.strip() for part in (meter_categories or "").split(",") if part.strip()]
        resource_filter = (resource_id or "").strip().rstrip("/")
        if resource_filter and not resource_filter.lower().startswith(f"/subscriptions/{sid}/"):
            raise ValueError("resource_id must be a resource of the same subscription")

        results = {}
        for metric_type in wanted:
            results[metric_type] = run(metric_type)
            if results[metric_type]["status"] in ("retry", "forbidden"):
                break
        for metric_type in wanted:
            results.setdefault(metric_type, {"status": "skipped", "message": "Not requested after the previous failure"})
        sources = {m: {k: v for k, v in r.items() if k != "rows"} for m, r in results.items()}
        failed = [m for m, r in results.items() if r["status"] != "ok"]
        if len(failed) == len(wanted):
            first = next((m for m in failed if results[m]["status"] != "skipped"), failed[0])
            top = {k: results[first][k] for k in ("http_status", "azure_code", "retry_after_seconds", "rate_limit")
                   if k in results[first]}
            return {"status": results[first]["status"], "data_complete": False, "source": "cost_management_query",
                    "sources": sources, "message": results[first].get("message"), **top}

        merged, totals = {}, {field: {} for field in fields.values()}
        for metric_type, result in results.items():
            if result["status"] != "ok":
                continue
            field = fields[metric_type]
            for row in result["rows"]:
                amount, currency = Decimal(str(row["cost"] or 0)), str(row["currency"] or "").strip()
                model = str(row["pricingmodel"] or "").strip() or "Unknown"
                if group_by == "resource":
                    rid = str(row["resourceid"] or "").strip().rstrip("/").lower()
                    key = (rid, currency)
                else:
                    key = (str(row["metersubcategory"] or "").strip(), model, currency)
                entry = merged.setdefault(key, {"models": set(), "cost": None, "amortized_cost": None})
                entry[field] = (entry[field] or Decimal(0)) + amount
                entry["models"].add(model)
                totals[field][currency] = totals[field].get(currency, Decimal(0)) + amount

        def text(value):
            return None if value is None else format(value, "f")

        records = []
        for key, entry in merged.items():
            if group_by == "resource":
                rid, currency = key
                parts = rid.split("/")
                records.append({
                    "resource_id": rid or None,
                    "resource_name": unquote(parts[-1]) if rid else "Unattributed",
                    "resource_group": parts[parts.index("resourcegroups") + 1] if "resourcegroups" in parts[:-1] else "",
                    "currency": currency, "cost": text(entry["cost"]), "amortized_cost": text(entry["amortized_cost"]),
                    "pricing_models": sorted(entry["models"]), "service_names": [], "unattributed": not rid,
                    "_rank": max(entry["cost"] or Decimal(0), entry["amortized_cost"] or Decimal(0)), "_key": rid})
            else:
                meter, model, currency = key
                records.append({
                    "meter_subcategory": meter, "pricing_model": model, "currency": currency,
                    "cost": text(entry["cost"]), "amortized_cost": text(entry["amortized_cost"]),
                    "_rank": max(entry["cost"] or Decimal(0), entry["amortized_cost"] or Decimal(0)), "_key": meter})
        records.sort(key=lambda r: (r["currency"], -r["_rank"], r["_key"]))
        for record in records:
            del record["_rank"], record["_key"]

        page, size = [], 0
        for record in records[offset:offset + page_size]:
            record_size = len(str(record).encode("utf-8"))
            if page and size + record_size > output_limit:
                break
            page.append(record)
            size += record_size
        next_offset = offset + len(page)
        has_more = next_offset < len(records)
        limitations = [
            "Aggregated by Cost Management: no individual rows, quantities, unit prices, tags, ReservationId, or BenefitId.",
            "ActualCost shows usage covered by a reservation or savings plan as zero or leaves it out: AmortizedCost, "
            "requested with metric=both, is what reveals it.",
            "Recent days can be incomplete; no data does not mean zero cost.",
        ]
        if group_by == "resource":
            limitations.append("service_names is empty: restrict the meters at the source with meter_categories. Meters outside "
                               "the filter are not in the cost. Excluded storage meters are not split: use group_by=meter with "
                               "resource_id for one resource.")
        else:
            limitations.append("A query allows two groupings, so the meter category is not returned. Match excluded storage "
                               "meters by their subcategory name. A VM size change is visible only as a change of family.")
        empty = not records and not failed
        silent = [m for m in wanted if results[m]["status"] == "ok" and results[m]["row_count"] == 0]
        if silent and not empty:
            limitations.append(f"{' and '.join(silent)} returned no rows for this period and filters, so its values are null: "
                               "report them as not available, which is not proof that the cost is zero.")
        return {
            "status": "empty" if empty else ("ok" if not failed else "partial"), "data_complete": not failed and not empty,
            "source": "cost_management_query", "subscription_id": sid, "period": {"start": start, "end": end},
            "metric": "both" if len(wanted) == 2 else wanted[0], "group_by": group_by,
            "filters": {"meter_categories": categories, "resource_id": resource_filter or None},
            "sources": sources, "total_items": len(records),
            "resource_count": len({r["resource_id"] for r in records if r.get("resource_id")}) if group_by == "resource" else None,
            "offset": offset, "returned_count": len(page), "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "totals_by_currency": {fields[m]: {c: format(v, "f") for c, v in sorted(totals[fields[m]].items())}
                                   for m in wanted if results[m]["status"] == "ok"},
            "records": page, "limitations": limitations,
            **({"message": "Cost Management returned no data for this period and filters. This does not prove that the "
                           "cost is zero: recent days can still be missing."} if empty else {}),
        }
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "data_complete": False, "source": "cost_management_query",
                "message": re.sub(r"https?://\S+", "[URL omitted]", str(exc))[:500]}
    except requests.RequestException:
        return {"status": "error", "data_complete": False, "source": "cost_management_query",
                "message": "Network error while calling Azure Resource Manager"}
    except Exception as exc:
        return {"status": "error", "data_complete": False, "source": "cost_management_query",
                "error_type": type(exc).__name__, "message": "Check that ARM is enabled in the Identity tab of the tool"}
