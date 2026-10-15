# Azure SRE Agent Canvas tool name: AzureCostDetails-Read
# Identity tab: enable ARM. Set the tool timeout to 900 seconds.
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()


def main(
    subscription_id: str,
    operation_url: str,
    view: str = "resources",
    offset: int = 0,
    page_size: int = 100,
    resource_id: str = "",
    columns: str = "",
    max_seconds: int = 720,
    max_download_mb: int = 512,
    not_before_unix: int = 0,
) -> dict:
    """Read every CSV blob in a completed Azure Cost Details report.

    resources: one row per ResourceId and billing currency; empty IDs remain
    in an explicit unattributed bucket. rows: original CSV records, optionally
    select column names separated by commas. Exact ResourceId filter optional.
    Follow next_offset until has_more is false. Each call reads the full report
    again because Agent Canvas containers have no persistent state.
    Totals cover ALL matching records, not only the returned page. Do not add
    totals from different pages of the same report. Monetary values are strings
    calculated with Decimal. Blob SAS URLs and ARM tokens are never returned.
    On retry/pending, repeat the same call with the returned not_before_unix;
    the tool enforces that wait before making another HTTP request.
    """
    import csv
    import gzip
    import io
    import json
    import os
    import re
    import sqlite3
    import tempfile
    import time
    from collections import defaultdict
    from contextlib import closing
    from decimal import Decimal, InvalidOperation, localcontext
    from email.utils import parsedate_to_datetime
    from urllib.parse import unquote, urlparse
    from uuid import UUID

    import requests

    # ClientType identifies this caller to Cost Management, so it does not share the rate-limit bucket of anonymous callers.
    client_type = "DevOpsStyle-AzureUtilities-CostDetails"

    class LimitReached(Exception):
        pass

    def normalize(value):
        return re.sub(r"[\s_-]+", "", value).lower()

    def check_time():
        if time.monotonic() > deadline:
            raise LimitReached("Tempo disponibile esaurito; richiedere un report su un periodo più breve")

    def retry_seconds(headers):
        delays = []
        for key, value in headers.items():
            if key.lower() == "retry-after" or (key.lower().startswith("x-ms-ratelimit-") and key.lower().endswith("retry-after")):
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

    class DecimalSum:
        def __init__(self):
            self.value = Decimal(0)

        def step(self, value):
            self.value += Decimal(value)

        def finalize(self):
            return str(self.value)

    class JsonSet:
        def __init__(self):
            self.values = set()

        def step(self, value):
            if value:
                self.values.add(value)

        def finalize(self):
            return json.dumps(sorted(self.values))

    try:
        sid = str(UUID(re.sub(r"^/subscriptions/", "", subscription_id.strip(), flags=re.I).strip("/")))
        parsed = urlparse(operation_url)
        prefix = f"/subscriptions/{sid}/providers/microsoft.costmanagement/"
        if (parsed.scheme != "https" or parsed.netloc.lower() != "management.azure.com"
                or not parsed.path.lower().startswith(prefix) or "/../" in parsed.path or parsed.fragment):
            raise ValueError("operation_url deve essere il link ARM della subscription restituito dal primo tool")
        if view not in ("resources", "rows") or int(offset) < 0 or not 1 <= int(page_size) <= 200:
            raise ValueError("Usare view resources/rows, offset >= 0 e page_size tra 1 e 200")
        offset, page_size = int(offset), int(page_size)
        deadline = time.monotonic() + max(5, min(int(max_seconds), 840))
        byte_limit = max(1, min(int(max_download_mb), 2048)) * 1024 * 1024
        output_limit = 60_000  # Bound the page sent to the LLM, not the input CSV.
        selected = [x.strip() for x in columns.split(",") if x.strip()]
        resource_filter = resource_id.strip().rstrip("/").lower()
        while time.time() < int(not_before_unix):
            remaining = deadline - time.monotonic() - 5
            if remaining <= 0:
                return {"status": "retry", "data_complete": False,
                        "not_before_unix": int(not_before_unix),
                        "retry_after_seconds": max(1, int(not_before_unix - time.time())),
                        "message": "Ripetere la stessa chiamata per continuare l'attesa"}
            time.sleep(min(60, remaining, not_before_unix - time.time()))
        token = credential.get_token("https://management.azure.com/.default").token
        with requests.get(operation_url, headers={"Authorization": "Bearer " + token, "ClientType": client_type},
                          timeout=(10, 30), allow_redirects=False) as response:
            if response.status_code in (202, 429, 500, 502, 503, 504):
                delay = retry_seconds(response.headers)
                return {"status": "pending" if response.status_code == 202 else "retry",
                        "operation_url": operation_url, "http_status": response.status_code,
                        "retry_after_seconds": delay, "not_before_unix": int(time.time()) + delay + 1,
                        "rate_limit": rate_limit(response.headers), "data_complete": False}
            if response.status_code != 200:
                return {"status": "error", "http_status": response.status_code, "data_complete": False,
                        "message": "Impossibile leggere il report ARM; verificare permessi o scadenza"}
            body = response.json()
        body = body.get("properties", body)
        if str(body.get("status", "")).lower() in ("failed", "canceled", "cancelled"):
            raise ValueError("Azure segnala un report fallito")
        manifest = body.get("manifest")
        if not isinstance(manifest, dict) or "blobs" not in manifest:
            raise ValueError("Manifest/blobs assente: il report non è leggibile")
        blobs = manifest["blobs"]
        if int(manifest.get("blobCount", len(blobs))) != len(blobs):
            raise ValueError("Manifest incompleto: blobCount diverso dal numero di blob")
        if str(manifest.get("dataFormat", "Csv")).lower() != "csv":
            raise ValueError("Questo tool gestisce report CSV e CSV gzip")
        context = manifest.get("requestContext", {})
        scope = str(context.get("requestScope", "")).strip("/").lower()
        if scope and scope != "subscriptions/" + sid:
            raise ValueError("Lo scope del manifest non corrisponde alla subscription")
        if int(manifest.get("byteCount", 0)) > byte_limit:
            raise LimitReached("Report oltre max_download_mb; suddividere la richiesta per giorno o settimana")

        totals = defaultdict(Decimal)
        missing_resource_totals = defaultdict(Decimal)
        rows_scanned = matching_rows = downloaded = 0
        returned, output_bytes, page_closed = [], 0, False
        fieldnames_all, cost_columns = set(), set()
        temp_root = "/mnt/data" if os.path.isdir("/mnt/data") else None
        csv.field_size_limit(16 * 1024 * 1024)

        # SQLite and temporary downloads live only for this invocation.
        with tempfile.TemporaryDirectory(prefix="sre_cost_", dir=temp_root) as folder, \
                closing(sqlite3.connect(os.path.join(folder, "costs.sqlite"))) as db, \
                localcontext() as decimal_context:
            decimal_context.prec = 50
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA journal_mode=OFF")
            db.create_aggregate("decimal_sum", 1, DecimalSum)
            db.create_aggregate("json_set", 1, JsonSet)
            db.execute("CREATE TABLE costs (rid TEXT, name TEXT, rg TEXT, currency TEXT, cost TEXT, service TEXT, model TEXT, charge TEXT)")
            db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)

            for blob_index, blob in enumerate(blobs):
                check_time()
                blob_url = blob["blobLink"]
                address = urlparse(blob_url)
                if (address.scheme != "https" or not (address.hostname or "").endswith(".blob.core.windows.net")
                        or address.port not in (None, 443) or address.username or address.password):
                    raise ValueError("Il manifest non contiene un URL Blob Azure pubblico valido")
                # Separate unauthenticated download: the SAS authorizes access.
                # Do NOT forward the ARM bearer token to Blob Storage.
                with requests.get(blob_url, stream=True, timeout=(10, 30), allow_redirects=False) as download:
                    if download.status_code in (429, 500, 502, 503, 504):
                        delay = retry_seconds(download.headers)
                        return {"status": "retry", "data_complete": False, "operation_url": operation_url,
                                "retry_after_seconds": delay, "not_before_unix": int(time.time()) + delay + 1,
                                "message": "Download temporaneamente non disponibile; ripetere la stessa lettura"}
                    if download.status_code != 200:
                        return {"status": "error", "data_complete": False, "http_status": download.status_code,
                                "blob_index": blob_index, "message": "Download blob fallito; il link potrebbe essere scaduto"}
                    with tempfile.TemporaryFile(dir=folder) as binary:
                        for chunk in download.iter_content(chunk_size=1024 * 1024):
                            check_time()
                            downloaded += len(chunk)
                            if downloaded > byte_limit:
                                raise LimitReached("Download oltre max_download_mb; richiedere intervalli più brevi")
                            binary.write(chunk)
                        binary.seek(0)
                        magic = binary.read(2)
                        binary.seek(0)
                        stream = gzip.GzipFile(fileobj=binary) if magic == b"\x1f\x8b" else binary
                        with io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text_stream:
                            reader = csv.DictReader(text_stream, strict=True)
                            fields = reader.fieldnames or []
                            if not fields:
                                raise ValueError("Blob CSV senza intestazione")
                            if len(set(fields)) != len(fields):
                                raise ValueError("Intestazioni CSV duplicate")
                            fieldnames_all.update(fields)
                            normalized = {normalize(k): k for k in fields}

                            def field(*aliases):
                                return next((normalized[normalize(x)] for x in aliases if normalize(x) in normalized), None)

                            cost_key = field("CostInBillingCurrency", "Cost", "PreTaxCost")
                            currency_key = field("BillingCurrencyCode", "BillingCurrency", "Currency")
                            rid_key = field("ResourceId", "InstanceId")
                            name_key = field("ResourceName")
                            rg_key = field("ResourceGroup", "ResourceGroupName")
                            service_key = field("ServiceName", "MeterCategory", "ConsumedService", "ServiceFamily")
                            model_key, charge_key = field("PricingModel"), field("ChargeType")
                            if not cost_key or not currency_key:
                                raise ValueError("Colonna costo/valuta di fatturazione assente; nessuna sostituzione con prezzi o USD")
                            cost_columns.add(cost_key)
                            if selected and any(k not in fields for k in selected):
                                raise ValueError("Una colonna richiesta non esiste nel CSV; usare i nomi esatti di available_columns")

                            for row in reader:
                                check_time()
                                if None in row or any(value is None for value in row.values()):
                                    raise ValueError("CSV incompleto o numero di colonne incoerente")
                                rows_scanned += 1
                                rid = (row.get(rid_key, "") or "").strip().rstrip("/").lower()
                                if rid in ("not applicable", "unassigned", "others", "n/a"):
                                    rid = ""
                                if resource_filter and rid != resource_filter:
                                    continue
                                matching_rows += 1
                                try:
                                    amount = Decimal(row[cost_key])
                                except InvalidOperation:
                                    raise ValueError(f"Costo non numerico nel record {rows_scanned}")
                                if not amount.is_finite():
                                    raise ValueError(f"Costo non finito nel record {rows_scanned}")
                                currency = row[currency_key].strip()
                                if not currency:
                                    raise ValueError(f"Valuta mancante nel record {rows_scanned}")
                                totals[currency] += amount
                                if not rid:
                                    missing_resource_totals[currency] += amount
                                if view == "resources":
                                    name = row.get(name_key, "") or (unquote(rid.split("/")[-1]) if rid else "Unattributed")
                                    db.execute("INSERT INTO costs VALUES (?,?,?,?,?,?,?,?)", (
                                        rid, name, row.get(rg_key, ""), currency, str(amount),
                                        row.get(service_key, ""), row.get(model_key, ""), row.get(charge_key, "")))
                                elif matching_rows > offset and not page_closed:
                                    record = {k: row[k] for k in selected} if selected else row
                                    size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
                                    if not returned and size > output_limit:
                                        raise ValueError("Una riga supera 60 KB: scegliere poche colonne con il parametro columns")
                                    if len(returned) >= page_size or output_bytes + size > output_limit:
                                        page_closed = True
                                    else:
                                        returned.append(record)
                                        output_bytes += size

            if view == "resources":
                db.execute("""CREATE TABLE grouped AS SELECT rid, currency, MIN(name) AS name,
                    MIN(rg) AS rg, decimal_sum(cost) AS cost, COUNT(*) AS cost_rows,
                    json_set(service) AS services, json_set(model) AS models, json_set(charge) AS charges
                    FROM costs GROUP BY rid, currency""")
                total_items = db.execute("SELECT COUNT(*) FROM grouped").fetchone()[0]
                resource_count = db.execute("SELECT COUNT(DISTINCT rid) FROM grouped WHERE rid <> ''").fetchone()[0]
                page = db.execute("SELECT * FROM grouped ORDER BY currency, CAST(cost AS REAL) DESC, rid LIMIT ? OFFSET ?", (page_size, offset))
                for rid, currency, name, rg, cost, count, services, models, charges in page:
                    record = {"resource_id": rid or None, "resource_name": name, "resource_group": rg,
                              "currency": currency, "cost": cost, "cost_rows": count,
                              "service_names": json.loads(services), "pricing_models": json.loads(models),
                              "charge_types": json.loads(charges), "unattributed": not bool(rid)}
                    size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
                    if not returned and size > output_limit:
                        raise ValueError("Riepilogo risorsa troppo grande: usare view=rows e columns")
                    if output_bytes + size > output_limit:
                        break
                    returned.append(record)
                    output_bytes += size
            else:
                total_items, resource_count = matching_rows, None

        next_offset = offset + len(returned)
        has_more = next_offset < total_items
        return {
            "status": "ok", "data_complete": True, "subscription_id": sid,
            "operation_url": operation_url, "request": context.get("requestBody", {}),
            "view": view, "resource_id_filter": resource_id or None,
            "blob_count": len(blobs), "downloaded_bytes": downloaded,
            "rows_scanned": rows_scanned, "matching_rows": matching_rows,
            "totals_by_currency": {k: str(v) for k, v in sorted(totals.items())},
            "unattributed_totals_by_currency": {k: str(v) for k, v in sorted(missing_resource_totals.items())},
            "resource_count": resource_count, "total_items": total_items,
            "offset": offset, "returned_count": len(returned),
            "has_more": has_more, "next_offset": next_offset if has_more else None,
            "cost_columns_used": sorted(cost_columns), "available_columns": sorted(fieldnames_all),
            "records": returned,
            "notes": ["Totali su tutte le righe corrispondenti al filtro, prima di crediti e imposte.",
                      "Non sommare i totali di pagine diverse dello stesso report.",
                      "Ogni pagina rilegge il report: per grandi volumi usare intervalli giornalieri e filtri mirati.",
                      "I dati recenti possono essere incompleti; assenza di righe non significa costo zero."],
        }
    except LimitReached as exc:
        return {"status": "split_required", "data_complete": False, "message": str(exc)}
    except requests.RequestException:
        return {"status": "error", "data_complete": False,
                "message": "Download interrotto o errore di rete; nessun totale parziale restituito"}
    except sqlite3.OperationalError as exc:
        return {"status": "split_required" if "interrupted" in str(exc) else "error",
                "data_complete": False, "message": "Limite di tempo o spazio locale; richiedere intervalli più brevi"}
    except (ValueError, KeyError, TypeError, csv.Error, UnicodeError, EOFError, OSError) as exc:
        return {"status": "error", "data_complete": False,
                "message": re.sub(r"https?://\S+", "[URL omesso]", str(exc))[:1000]}
    except Exception as exc:
        return {"status": "error", "data_complete": False, "error_type": type(exc).__name__,
                "message": "Verificare Identity ARM, schema CSV e dipendenze Python"}
