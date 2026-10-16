# Azure SRE Agent Canvas tool name: AzureCommitments-List
# Identity tab: enable ARM. The default 120-second timeout is enough.
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()


def main(
    kind: str = "all",
    subscription_id: str = "",
    reserved_resource_type: str = "",
    location: str = "",
    sku: str = "",
    commitment_id: str = "",
    effective_date: str = "",
    include_inactive: bool = False,
    offset: int = 0,
    page_size: int = 50,
) -> dict:
    """List the active Azure reservations and savings plans that this identity can read.

    kind: all, reservations, or savings_plans. Reservations and savings plans are
    tenant-level resources: the APIs return only the ones the identity can read
    (Reservations Reader on /providers/Microsoft.Capacity, Savings plan reader on
    /providers/Microsoft.BillingBenefits), and an empty list does not prove that
    there are none. effective_date (YYYY-MM-DD) adds covers_effective_date to every
    item. subscription_id adds applies_to_subscription. reserved_resource_type, location,
    and sku filter reservations only: savings plans always stay. Read only; JSON only.
    """
    import re
    import time
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    from urllib.parse import urlparse

    import requests

    arm = "https://management.azure.com"
    max_pages = 10
    sources = {
        "reservations": {
            "path": "/providers/Microsoft.Capacity/reservations", "api_version": "2022-11-01",
            "role": "Reservations Reader", "scope": "/providers/Microsoft.Capacity"},
        "savings_plans": {
            "path": "/providers/Microsoft.BillingBenefits/savingsPlans", "api_version": "2022-11-01",
            "role": "Savings plan reader", "scope": "/providers/Microsoft.BillingBenefits"},
    }
    now = datetime.now(timezone.utc)
    effective_on = None

    def parse_dt(value):
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2}))?", str(value or ""))
        if not match:
            return None
        year, month, day_, hour, minute, second = (int(part or 0) for part in match.groups())
        return datetime(year, month, day_, hour, minute, second, tzinfo=timezone.utc)

    def day(value):
        parsed = parse_dt(value)
        return parsed.date().isoformat() if parsed else None

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

    def next_page(link, path):
        parsed = urlparse(link)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "management.azure.com"
                or not parsed.path.lower().startswith(path.lower()) or "/../" in parsed.path or parsed.fragment):
            raise ValueError("Unexpected nextLink returned by Azure")
        return link

    def fetch(name):
        source = sources[name]
        url, pages, items = f"{arm}{source['path']}?api-version={source['api_version']}&take=100", 0, []
        while url:
            pages += 1
            token = credential.get_token(arm + "/.default").token
            with requests.get(url, headers={"Authorization": "Bearer " + token},
                              timeout=(10, 30), allow_redirects=False) as response:
                code = response.status_code
                if code in (401, 403):
                    return {"status": "forbidden", "http_status": code,
                            "message": f"The identity cannot read {name.replace('_', ' ')}. Assign "
                                       f"{source['role']} at {source['scope']} (tenant scope)."}
                if code in (429, 500, 502, 503, 504):
                    delay = retry_seconds(response.headers)
                    return {"status": "retry", "http_status": code, "retry_after_seconds": delay,
                            "rate_limit": rate_limit(response.headers),
                            "message": f"HTTP {code}; wait before repeating the call"}
                if code != 200:
                    try:
                        error = response.json().get("error", {})
                    except ValueError:
                        error = {}
                    detail = re.sub(r"https?://\S+", "[URL omitted]", str(error.get("message", "")))[:400]
                    return {"status": "error", "http_status": code, "azure_code": error.get("code"),
                            "message": detail or "The request was not successful"}
                try:
                    body = response.json()
                except ValueError:
                    return {"status": "error", "message": "Azure returned a response that is not JSON"}
            items += body.get("value") or []
            url = next_page(body["nextLink"], source["path"]) if body.get("nextLink") else ""
            if url and pages >= max_pages:
                return {"status": "ok", "items": items, "truncated": True}
        return {"status": "ok", "items": items, "truncated": False}

    def utilization(props):
        data = props.get("utilization") or {}
        by_days, unit = {}, ""
        for aggregate in data.get("aggregates") or []:
            if str(aggregate.get("grainUnit", "")).lower() == "days" and aggregate.get("value") is not None:
                by_days[str(int(aggregate["grain"]))] = aggregate["value"]
                unit = aggregate.get("valueUnit") or unit
        if not by_days and not data.get("trend"):
            return None
        return {"by_days": by_days, "unit": unit, "trend": data.get("trend") or ""}

    def lifecycle(record, props):
        expiry = parse_dt(props.get("expiryDateTime") or props.get("expiryDate"))
        effective = parse_dt(props.get("effectiveDateTime"))
        record["effective_date"] = day(props.get("effectiveDateTime"))
        record["expiry_date"] = day(props.get("expiryDateTime") or props.get("expiryDate"))
        record["days_to_expiry"] = (expiry.date() - now.date()).days if expiry else None
        expired = bool(expiry and expiry <= now)
        started = effective is None or effective <= now
        record["active"] = (record["state"] == "Succeeded" and not props.get("archived") and started and not expired)
        if effective_on:
            start = datetime(effective_on.year, effective_on.month, effective_on.day, tzinfo=timezone.utc)
            record["covers_effective_date"] = bool(
                record["state"] == "Succeeded" and (expiry is None or expiry > start))
        return record

    def scope_list(props):
        # The 2022-11-01 responses carry a single scope in appliedScopeProperties; appliedScopes is the older array form.
        extra = props.get("appliedScopeProperties") or {}
        found = [scope for scope in (props.get("appliedScopes") or []) if isinstance(scope, str) and scope]
        for value in (extra.get("subscriptionId"), extra.get("resourceGroupId"), extra.get("managementGroupId")):
            if isinstance(value, str) and value and value not in found:
                found.append(value)
        return found, extra.get("displayName")

    def scope_fields(record, props, scopes):
        scope_type = props.get("appliedScopeType")
        record["applied_scope_type"], record["applied_scopes"] = scope_type, scopes
        if not subscription_id:
            return record
        wanted = f"/subscriptions/{subscription_id.lower()}"
        lowered = [scope.lower().rstrip("/") for scope in scopes]
        if scope_type == "Shared":
            verdict = "possible"
        elif scope_type == "ManagementGroup" or scope_type is None or not scopes:
            verdict = "unknown"
        elif wanted in lowered:
            verdict = "yes"
        elif any(scope.startswith(wanted + "/resourcegroups/") for scope in lowered):
            verdict = "resource_group"
        else:
            verdict = "no"
        record["applies_to_subscription"] = verdict
        return record

    def reservation(item):
        props = item.get("properties") or {}
        found = re.search(r"/reservationOrders/([^/]+)/reservations/([^/]+)", item.get("id") or "", re.I)
        scopes, scope_name = scope_list(props)
        record = {
            "kind": "reservation", "id": item.get("id"),
            "order_id": found.group(1) if found else "", "commitment_id": found.group(2) if found else "",
            "name": props.get("displayName"), "state": props.get("provisioningState") or props.get("displayProvisioningState"),
            "reserved_resource_type": props.get("reservedResourceType"), "sku": (item.get("sku") or {}).get("name"),
            "sku_description": props.get("skuDescription"), "location": item.get("location"),
            "quantity": props.get("quantity"), "instance_flexibility": props.get("instanceFlexibility"),
            "term": props.get("term"), "billing_plan": props.get("billingPlan"),
            "purchase_date": day(props.get("purchaseDateTime") or props.get("purchaseDate")),
            "renew": props.get("renew"), "renew_state": props.get("userFriendlyRenewState"),
            "billing_scope": props.get("billingScopeId"), "applied_scope_name": scope_name, "utilization": utilization(props),
        }
        scope_fields(record, props, scopes)
        return lifecycle(record, props)

    def savings_plan(item):
        props = item.get("properties") or {}
        found = re.search(r"/savingsPlanOrders/([^/]+)/savingsPlans/([^/]+)", item.get("id") or "", re.I)
        commitment = props.get("commitment") or {}
        scopes, scope_name = scope_list(props)
        record = {
            "kind": "savings_plan", "id": item.get("id"),
            "order_id": found.group(1) if found else "", "commitment_id": found.group(2) if found else "",
            "name": props.get("displayName"), "state": props.get("provisioningState") or props.get("displayProvisioningState"),
            "sku": (item.get("sku") or {}).get("name"),
            "commitment": {"amount": commitment.get("amount"), "currency": commitment.get("currencyCode"),
                           "grain": commitment.get("grain")} if commitment else None,
            "term": props.get("term"), "billing_plan": props.get("billingPlan"),
            "purchase_date": day(props.get("purchaseDateTime")), "renew": props.get("renew"),
            "billing_scope": props.get("billingScopeId"), "applied_scope_name": scope_name,
            "utilization": utilization(props),
        }
        scope_fields(record, props, scopes)
        return lifecycle(record, props)

    try:
        kind = (kind or "all").strip().lower()
        if kind not in ("all", "reservations", "savings_plans"):
            raise ValueError("kind must be all, reservations, or savings_plans")
        if int(offset) < 0 or not 1 <= int(page_size) <= 100:
            raise ValueError("Use offset >= 0 and page_size between 1 and 100")
        offset, page_size = int(offset), int(page_size)
        subscription_id = re.sub(r"^/subscriptions/", "", (subscription_id or "").strip(), flags=re.I).strip("/")
        if subscription_id and not re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", subscription_id):
            raise ValueError("subscription_id must be a GUID")
        if effective_date:
            effective_on = datetime.strptime(effective_date.strip(), "%Y-%m-%d").date()

        wanted_kinds = ["reservations", "savings_plans"] if kind == "all" else [kind]
        report, records = {}, []
        for name in wanted_kinds:
            result = fetch(name)
            report[name] = {k: v for k, v in result.items() if k != "items"}
            if result["status"] != "ok":
                continue
            build = reservation if name == "reservations" else savings_plan
            built = [build(item) for item in result["items"]]
            report[name]["visible"] = len(built)
            report[name]["api_version"] = sources[name]["api_version"]
            records += built

        failed = [n for n in wanted_kinds if report[n]["status"] != "ok"]
        if len(failed) == len(wanted_kinds):
            worst = "retry" if any(report[n]["status"] == "retry" for n in failed) else report[failed[0]]["status"]
            return {"status": worst, "data_complete": False, "sources": report,
                    "message": "; ".join(report[n]["message"] for n in failed if report[n].get("message"))}

        want_type, want_place = (reserved_resource_type or "").strip().lower(), re.sub(r"\s+", "", location or "").lower()
        want_sku, want_id = (sku or "").strip().lower(), (commitment_id or "").strip().lower()

        def keep(record):
            if not include_inactive and not record["active"]:
                return False
            if record["kind"] == "reservation":
                if want_type and (record.get("reserved_resource_type") or "").lower() != want_type:
                    return False
                if want_place and re.sub(r"\s+", "", record.get("location") or "").lower() != want_place:
                    return False
                if want_sku and want_sku not in f"{record.get('sku') or ''} {record.get('sku_description') or ''}".lower():
                    return False
            return not want_id or want_id in (record["id"] or "").lower()

        matching = sorted((r for r in records if keep(r)),
                          key=lambda r: (r["kind"], r["expiry_date"] or "9999-12-31", r["name"] or ""))
        page = matching[offset:offset + page_size]
        notes = ["Totals cover the commitments this identity can read, not necessarily all those of the tenant."]
        if not records:
            notes.append("No commitment is visible. Reservations and savings plans are tenant-level resources, so a "
                         "Reader role on a subscription shows none. An empty list also happens when the identity lacks "
                         "Reservations Reader or Savings plan reader: it does not prove that none exist.")
        if (want_type or want_place or want_sku) and any(r["kind"] == "savings_plan" for r in records):
            notes.append("reserved_resource_type, location, and sku filter reservations only. Savings plans are not tied "
                         "to a resource type, region, or size, so they stay in the result.")
        truncated = any(report[n].get("truncated") for n in wanted_kinds if report[n]["status"] == "ok")
        if truncated:
            notes.append(f"The list was cut after {max_pages} pages.")
        notes.append("Neither API lists the resources a commitment covers: match by the ReservationId or BenefitId "
                     "in the billing rows, or treat a match on type, SKU, and region as a candidate only.")
        has_more = offset + len(page) < len(matching)
        return {
            "status": "ok" if not failed else "partial", "data_complete": not failed and not truncated,
            "as_of_date": now.date().isoformat(), "effective_date": effective_on.isoformat() if effective_on else None,
            "sources": report, "visible_count": len(records), "matching_count": len(matching),
            "returned_count": len(page), "offset": offset, "has_more": has_more,
            "next_offset": offset + len(page) if has_more else None, "records": page, "notes": notes,
        }
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "data_complete": False, "message": re.sub(r"https?://\S+", "[URL omitted]", str(exc))[:500]}
    except requests.RequestException:
        return {"status": "error", "data_complete": False, "message": "Network error while calling Azure Resource Manager"}
    except Exception as exc:
        return {"status": "error", "data_complete": False, "error_type": type(exc).__name__,
                "message": "Check that ARM is enabled in the Identity tab of the tool"}
