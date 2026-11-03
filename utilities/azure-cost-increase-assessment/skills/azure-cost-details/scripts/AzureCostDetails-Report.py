# Bundled skill script: the skill runs it in the agent sandbox with --input and --output, as the
# agent's managed identity (DefaultAzureCredential), or with --token-file as a fallback (see
# SKILL.md). main() still works unchanged as an Agent Canvas Python tool.
credential = None  # Set by --token-file; otherwise DefaultAzureCredential on first use.


class _AuthUnavailable(Exception):
    pass


class _FileToken:
    def __init__(self, token):
        self.token = token

    def get_token(self, *scopes, **kwargs):
        return self


def _arm_token():
    global credential
    try:
        if credential is None:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
        return credential.get_token("https://management.azure.com/.default").token
    except Exception as exc:
        raise _AuthUnavailable(type(exc).__name__) from None


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
            token = _arm_token()
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
                    failure = {"status": "error", "http_status": code, "azure_code": err.get("code"),
                               "message": detail or "Richiesta Cost Details non riuscita", "operation_url": state["url"]}
                    if code == 401:
                        # Expired or invalid token: resume the same report with a new token.
                        failure.update(auth_required=True, resume_state=json.dumps(
                            state, separators=(",", ":")) if state["url"] else "")
                    return failure
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
    except _AuthUnavailable as exc:
        return {"status": "error", "auth_required": True, "error_type": str(exc),
                "message": "Nessuna credenziale Azure disponibile: passare un token ARM con --token-file",
                "resume_state": json.dumps(state, separators=(",", ":")) if state.get("url") else ""}
    except requests.RequestException:
        return {"status": "error", "message": "Errore di rete ARM; nessuna risposta conclusiva",
                "operation_url": state.get("url", ""),
                "resume_state": json.dumps(state) if state.get("url") else "",
                "creation_outcome_unknown": not bool(state.get("url"))}
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "message": re.sub(r"https?://\S+", "[URL omesso]", str(exc))[:1000]}
    except Exception as exc:
        return {"status": "error", "message": "Verificare credenziali Azure e permessi Cost Management",
                "error_type": type(exc).__name__}


if __name__ == "__main__":
    import argparse
    import inspect
    import json
    import os
    import re
    import sys

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--input", type=argparse.FileType("r", encoding="utf-8"),
                        default=sys.stdin, help="JSON parameters file; defaults to stdin")
    parser.add_argument("--output", help="write the full JSON result to this file and print only a short summary")
    parser.add_argument("--token-file", help="file that contains only an ARM access token; without it the "
                                             "script uses DefaultAzureCredential")
    args = parser.parse_args()
    with args.input as stream:
        try:
            parameters = json.load(stream)
        except (ValueError, OSError):
            parser.error("Input must contain valid JSON")
    if not isinstance(parameters, dict):
        parser.error("Input must be a JSON object")
    try:
        inspect.signature(main).bind(**parameters)
    except TypeError:
        parser.error("Input parameters do not match main(); see the skill input contract")
    for budget in ("max_wait_seconds", "max_seconds"):
        # Terminal commands stop after 120 seconds by default: keep each run well within that.
        if budget in inspect.signature(main).parameters:
            parameters.setdefault(budget, 80)
    if args.token_file:
        try:
            with open(args.token_file, "rb") as stream:
                raw = stream.read()
            # Accept the UTF-8 BOM and UTF-16 files that PowerShell redirection can write.
            text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
            token = re.sub(r"(?i)^bearer\s+", "", text.strip().strip("\"'").strip())
        except (OSError, UnicodeError):
            parser.error("--token-file cannot be read")
        if not re.fullmatch(r"[\w-]+(\.[\w-]+){2,4}", token, re.ASCII):
            parser.error("--token-file must contain only an ARM access token")
        credential = _FileToken(token)
    result = main(**parameters)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output + ".tmp", "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
        os.replace(args.output + ".tmp", args.output)
        # The terminal shows little output: keep records, internal URLs and resume state in the file.
        hidden = {"operation_url", "resume_state", "records", "request", "available_columns", "notes", "limitations"}
        result = {**{k: v for k, v in result.items() if k not in hidden}, "output": args.output}
    print(json.dumps(result, ensure_ascii=False))
