# Azure SRE Agent Canvas tool name: AzureCostDetails-Report
# Identity tab: enable ARM. Set the tool timeout to 900 seconds.
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()


def main(
    subscription_id: str,
    start_date: str = "",
    end_date: str = "",
    metric: str = "ActualCost",
    resume_state: str = "",
    max_wait_seconds: int = 720,
) -> dict:
    """Request or resume a Cost Details report, using the Logic App API.

    Dates are YYYY-MM-DD, inclusive; blank dates select yesterday in UTC.
    Keep the returned resume_state unchanged when status is pending/retry.
    On ready, pass operation_url to AzureCostDetails-Read. Never restart a
    report merely because it is pending. Returns JSON only, without blob SAS.
    """
    import json
    import math
    import re
    import time
    from datetime import datetime, timedelta, timezone
    from email.utils import parsedate_to_datetime
    from urllib.parse import urljoin, urlparse
    from uuid import UUID

    import requests

    api_version = "2023-03-01"  # Same version as the supplied Logic App.
    arm = "https://management.azure.com"
    # ClientType identifies this caller to Cost Management, so it does not share the rate-limit bucket of anonymous callers.
    client_type = "DevOpsStyle-AzureUtilities-CostDetails"
    deadline = time.monotonic() + max(0, min(int(max_wait_seconds), 840))
    state = {}

    def checked_url(value):
        url = urljoin(arm + "/", value)
        parsed = urlparse(url)
        prefix = f"/subscriptions/{sid}/providers/microsoft.costmanagement/"
        if (parsed.scheme != "https" or parsed.netloc.lower() != "management.azure.com"
                or not parsed.path.lower().startswith(prefix)
                or "/../" in parsed.path or parsed.fragment):
            raise ValueError("URL ARM fuori dalla subscription richiesta")
        return url

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
        return max([1] + delays) if delays else 15

    def rate_limit(headers):
        return {k.lower(): v for k, v in headers.items()
                if k.lower() == "retry-after" or k.lower().startswith("x-ms-ratelimit-")}

    def paused(status, message, **extra):
        return {
            "status": status, "subscription_id": sid,
            "period": {"start": state["start"], "end": state["end"]},
            "metric": state["metric"], "operation_url": state["url"],
            "retry_after_seconds": max(0, math.ceil(state["not_before"] - time.time())),
            "resume_state": json.dumps(state, separators=(",", ":")),
            "message": message, **extra,
        }

    try:
        sid = str(UUID(re.sub(r"^/subscriptions/", "", subscription_id.strip(), flags=re.I).strip("/")))
        if metric not in ("ActualCost", "AmortizedCost"):
            raise ValueError("metric deve essere ActualCost o AmortizedCost")
        if resume_state:
            state = json.loads(resume_state)
            if (state["subscription_id"] != sid or state["metric"] != metric
                    or (start_date and start_date != state["start"])
                    or (end_date and end_date != state["end"])):
                raise ValueError("resume_state non corrisponde ai parametri della richiesta")
            if state["url"]:
                state["url"] = checked_url(state["url"])
            if not math.isfinite(float(state["not_before"])):
                raise ValueError("Attesa non valida nel resume_state")
        else:
            if bool(start_date) != bool(end_date):
                raise ValueError("Specificare entrambe le date oppure lasciarle entrambe vuote")
            yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
            start_date, end_date = start_date or yesterday, end_date or yesterday
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            # Conservative partitioning: one calendar month per request.
            if start > end or (start.year, start.month) != (end.year, end.month):
                raise ValueError("Usare date ordinate nello stesso mese; suddividere i periodi più lunghi")
            state = {"subscription_id": sid, "start": start.isoformat(), "end": end.isoformat(),
                     "metric": metric, "url": "", "not_before": 0}

        while True:
            delay = max(0, state["not_before"] - time.time())
            # Spend this invocation's budget waiting. Later calls continue the
            # same wait without polling Azure before not_before.
            while delay > 0:
                remaining = deadline - time.monotonic() - 5
                if remaining <= 0:
                    return paused("pending" if state["url"] else "retry", "Richiamare con lo stesso resume_state per continuare l'attesa")
                time.sleep(min(delay, remaining, 60))
                delay = max(0, state["not_before"] - time.time())

            url = state["url"] or (
                f"{arm}/subscriptions/{sid}/providers/Microsoft.CostManagement/"
                f"generateCostDetailsReport?api-version={api_version}"
            )
            method = "GET" if state["url"] else "POST"
            kwargs = {} if method == "GET" else {"json": {
                "metric": metric, "timePeriod": {"start": state["start"], "end": state["end"]}
            }}
            # Obtain/refresh the token inside main; do not read a different env variable.
            token = credential.get_token(arm + "/.default").token
            with requests.request(method, checked_url(url), headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json", "ClientType": client_type
            }, timeout=(10, 30), allow_redirects=False, **kwargs) as response:
                code, headers = response.status_code, response.headers
                if code == 204:
                    return {"status": "empty", "subscription_id": sid, "metric": metric,
                            "period": {"start": state["start"], "end": state["end"]},
                            "message": "Nessun dato restituito; non è una prova che il costo sia zero"}
                if code in (429, 500, 502, 503, 504):
                    state["not_before"] = time.time() + retry_seconds(headers)
                    return paused("retry", f"HTTP {code}; rispettare Retry-After",
                                  http_status=code, rate_limit=rate_limit(headers))
                if code not in (200, 201, 202):
                    try:
                        err = response.json().get("error", {})
                    except ValueError:
                        err = {}
                    detail = re.sub(r"https?://\S+", "[URL omesso]", str(err.get("message", "")))[:1000]
                    return {"status": "error", "http_status": code, "azure_code": err.get("code"),
                            "message": detail or "Richiesta Cost Details non riuscita", "operation_url": state["url"]}
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                body = body.get("properties", body)
                if str(body.get("status", "")).lower() in ("failed", "canceled", "cancelled"):
                    return {"status": "error", "message": "Generazione del report fallita", "operation_url": state["url"]}
                if headers.get("Location"):
                    state["url"] = checked_url(headers["Location"])
                if "manifest" in body:
                    if not state["url"] and body.get("id"):
                        state["url"] = checked_url(body["id"])
                        if "?" not in state["url"]:
                            state["url"] += "?api-version=" + api_version
                    if not state["url"]:
                        raise ValueError("Report pronto ma Azure non ha restituito Location o id per rileggerlo")
                    manifest = body["manifest"]
                    return {
                        "status": "ready", "subscription_id": sid, "metric": metric,
                        "period": {"start": state["start"], "end": state["end"]},
                        "operation_url": state["url"], "valid_till": body.get("validTill"),
                        "resume_state": json.dumps(state, separators=(",", ":")),
                        "blob_count": manifest.get("blobCount", len(manifest.get("blobs", []))),
                        "byte_count": manifest.get("byteCount"),
                        "next_tool": "AzureCostDetails-Read",
                    }
                if not state["url"]:
                    raise ValueError("Azure non ha restituito il link Location per il polling")
                state["not_before"] = time.time() + retry_seconds(headers)
                if time.monotonic() >= deadline:
                    return paused("pending", "Report in preparazione; riprendere lo stesso report")
    except requests.RequestException:
        return {"status": "error", "message": "Errore di rete ARM; nessuna risposta conclusiva",
                "operation_url": state.get("url", ""),
                "resume_state": json.dumps(state) if state.get("url") else "",
                "creation_outcome_unknown": not bool(state.get("url"))}
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "message": re.sub(r"https?://\S+", "[URL omesso]", str(exc))[:1000]}
    except Exception as exc:
        return {"status": "error", "message": "Verificare Identity ARM e permessi Cost Management",
                "error_type": type(exc).__name__}
