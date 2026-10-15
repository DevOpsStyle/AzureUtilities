# Overview workflow

Goal: a short and correct answer to "what do the affected resources cost today, and after the change", built from the inventory and the billed cost, with the full per-resource detail in a CSV instead of in the chat.

## 1. Exposure from Azure Resource Graph

Run queries with the Azure CLI read tool: `az graph query -q '<QUERY>' --first 1000`. Pass the query on one line inside single quotes (the KQL itself uses double quotes) and follow the skip token for more pages. Do not change the classification logic.

Build each query from the base followed by one tail.

### Base

```kusto
Resources
| where type in~ ("microsoft.compute/virtualmachines", "microsoft.compute/virtualmachinescalesets", "microsoft.compute/disks", "microsoft.compute/snapshots", "microsoft.storage/storageaccounts", "microsoft.netapp/netappaccounts/capacitypools", "microsoft.elasticsan/elasticsans")
| extend region = tolower(location)
| extend isVm = type in~ ("microsoft.compute/virtualmachines", "microsoft.compute/virtualmachinescalesets")
| extend isVmss = type =~ "microsoft.compute/virtualmachinescalesets"
| extend vmSize = iff(isVmss, tostring(sku.name), tostring(properties.hardwareProfile.vmSize))
| extend s = tolower(vmSize)
| extend isV1 = isVm and s matches regex "^standard_(b[0-9]+(ls|s|ms)|d[0-9]+(-[0-9]+)?|ds[0-9]+(-[0-9]+)?|f[0-9]+s?|g[0-9]+|gs[0-9]+(-[0-9]+)?|l[0-9]+s|np[0-9]+s|hc[0-9]+(-[0-9]+)?rs)(_promo)?$"
| extend isV2 = isVm and s matches regex "^standard_(a[0-9]+m?|ds?[0-9]+(-[0-9]+)?|f[0-9]+s|l[0-9]+s)_v2(_promo)?$"
| extend isIntelV7 = isVm and s matches regex "^standard_(d[0-9]+(-[0-9]+)?(s|ds|ls|lds)|e[0-9]+(-[0-9]+)?(s|ds))_v7$"
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| extend expectedIncreasePct = case(isV1 or isV2, 25, regionalPct > 0 and isIntelV7 == false, regionalPct, 0)
| extend category = case(isVm, "Virtual Machine", type =~ "microsoft.compute/disks", "Managed Disk", type =~ "microsoft.compute/snapshots", "Disk Snapshot", type =~ "microsoft.storage/storageaccounts", "Storage Account", type =~ "microsoft.netapp/netappaccounts/capacitypools", "Azure NetApp Files", "Elastic SAN")
| extend regime = case(isV1 or isV2, "v1/v2 series", isIntelV7, "Intel v7 (not affected)", regionalPct > 0, "Regional", "Not affected")
| extend instances = iff(isVmss, toint(sku.capacity), 1)
```

### Summary tail

Counts only, including resources that are not affected. Run this first.

```kusto
| extend regionGroup = iff(expectedIncreasePct > 0, region, "n/a")
| summarize resources = count(), instances = sum(instances) by subscriptionId, category, regime, expectedIncreasePct, regionGroup
| order by subscriptionId asc, category asc, expectedIncreasePct desc
```

### List tail

One row per affected resource. It feeds the cost join, the shortlist, and the CSV.

```kusto
| where expectedIncreasePct > 0
| extend skuLabel = case(isVm, vmSize, type =~ "microsoft.compute/disks" or type =~ "microsoft.compute/snapshots", tostring(sku.name), strcat(tostring(kind), " ", tostring(sku.name)))
| extend osType = iff(isVmss, tostring(properties.virtualMachineProfile.storageProfile.osDisk.osType), tostring(properties.storageProfile.osDisk.osType))
| extend state = case(isVmss, "n/a", isVm, tostring(properties.extended.instanceView.powerState.code), type =~ "microsoft.compute/disks", tostring(properties.diskState), "")
| extend created = iff(isnotempty(tostring(properties.timeCreated)), tostring(properties.timeCreated), tostring(properties.creationTime))
| project id, name, type, subscriptionId, resourceGroup, location = region, category, skuLabel, osType, state, created, instances, regime, expectedIncreasePct
```

### Subscriptions

Use it when no subscription was established in the conversation.

```kusto
ResourceContainers
| where type =~ "microsoft.resources/subscriptions"
| project subscriptionId, name, state = tostring(properties.state)
```

Sizing gates. Stop and ask; never shorten silently.

- **Nothing affected**: say what was checked (resource types, regions, series) and stop. Do not request cost reports.
- **More than 3 subscriptions with affected resources**: show subscription and affected count, and ask which to start with (default: the highest count).
- **More than 200 affected resources in one run**: an in-chat calculation is not reliable at that size. Offer a narrower scope (subscription, resource group, region, or category), or the local script `CostIncrease_Assessment_v6.ps1` for the full export. If the customer wants a partial overview anyway, label it partial and state what it covers.

## 2. Billed cost from Cost Details

Cost comes only from the Cost Details tools. The read-only CLI channel rejects Cost Management POST requests: do not retry them through the CLI, and never through a write tool or a silent estimate.

1. **Period**: the last three complete calendar months, unless the customer asks otherwise. Name the first and last day in the first answer. One report per subscription and month.
2. **ActualCost**: for each subscription with affected resources and each month, call `AzureCostDetails-Report` with `metric="ActualCost"`. Run up to 3 reports at a time. Tell the customer once that generation can take a few minutes.
3. **Status handling** (details in skill `azure-cost-details`): `pending` or `retry` repeat with the same parameters and unchanged `resume_state`; `ready` continue; `empty` means no data, not zero cost; `error` follows "When something fails" in the skill.
4. **Read every page** of every report: `AzureCostDetails-Read` with `view="resources"`, `page_size=200`, `offset=0`, then `next_offset` until `has_more` is false.
   - Confirm that the `returned_count` values of the pages add up to `total_items`. A report counts as read only then.
   - Records are sorted by currency, then by cost, largest first. Each page re-reads the whole report, so do not reread pages.
   - When `total_items` is above 600 for one month, a complete in-chat calculation is not practical: apply the last gate above (narrow scope, the script, or a labelled partial result with the cumulative cost read compared with `totals_by_currency`).
5. **AmortizedCost**: also request it, for the same months, when any record shows `Reservation` or `SavingsPlan` in `pricing_models`, or when `pricing_models` is empty for every record (the column is missing). Read it the same way, then build `amortized = {(resource_id.lower(), currency): Decimal(cost)}` for the affected resources with commitments, summing the months.
6. **Interpret** each record:

| Signal in the record | Meaning | Action |
|---|---|---|
| `pricing_models` is only `OnDemand` | Fully on demand | The cost is the baseline |
| includes `Reservation` | Part of the usage is covered by a reservation and shows as 0 in actual cost | The baseline is the on-demand part. Flag it and check expiry in the deep dive |
| includes `SavingsPlan` | Covered usage is hidden in actual cost but is affected | The calculation uses the amortized total |
| includes `Spot` | Not addressed by the announcement | Flag it and do not estimate it |
| `service_names` has more than `Virtual Machines` or `Storage` | Other meters on the same resource, for example licenses, Defender, or bandwidth | The baseline may be overstated. Flag it |
| Resource absent from the report | No billed usage in the period | List it as unbilled and do not estimate it |

7. **Keep** `operation_url`, `subscription_id`, month, and metric for the deep dive. Generate a new report only after `valid_till`, or when the reader says the link expired.

## 3. Calculate in the sandbox

Never add or multiply these numbers by hand. Paste the rows of the Resource Graph list query as `exposure` and the `records` of every page read as `costs` (all months together, only the records of the affected resources), then run:

```python
import csv
import json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
IN_SCOPE = {"virtual machines", "storage"}
UPPER_BOUND = {"Storage Account", "Azure NetApp Files", "Elastic SAN"}
DISCLAIMER = [
    "DISCLAIMER: This file contains an ESTIMATE of the impact of the Azure price changes effective 1 February 2027.",
    "It is intended to give an indication of the potential impact only. Values are based on past consumption (or on public list prices where no billed usage is available) and assume constant volumes.",
    "The final impact depends on the actual usage of the resources, on the applicable commercial agreement and on commitments (Reserved Instances, Savings Plans), and must be verified against actual billing.",
]
COLUMNS = ["Category", "SubscriptionId", "ResourceGroup", "Name", "Type", "Location", "Sku", "Quantity", "OsType",
           "State", "IncreaseRegime", "ExpectedIncreasePct", "CostSource", "Currency", "BaseMonthlyCost",
           "EstimatedMonthlyIncrease", "EstimatedAnnualIncrease", "ActualOnDemandMonthly", "ActualSubjectMonthly",
           "SavingsPlanMonthly", "AmortizedMonthlyTotal", "OnDemandSharePct", "ReservationSharePct",
           "SavingsPlanSharePct", "ListPriceMonthly", "Notes", "ResourceId"]
NOTES = {
    "reservation_covered_usage": "Part of the usage is covered by a reservation and is excluded until it expires",
    "savings_plan_not_sized": "Savings-plan usage is not included: AmortizedCost was not read",
    "mixed_commitments": "Reservation and savings-plan usage on the same resource: savings-plan part not sized",
    "spot": "Spot usage: not addressed by the announcement",
    "other_meters_on_resource": "Billed cost includes other meters (licenses, Defender, bandwidth): baseline may be overstated",
    "upper_bound": "Upper bound: excluded storage meters are not split here",
    "unattached_disk": "Unattached disk: still billed",
    "created_in_period": "Created during the analysed period: the average understates its monthly cost",
    "no_billed_usage": "No billed usage in the period",
    "currently_deallocated": "Currently deallocated: scenario only, not an expected bill while it stays stopped",
}


def cents(value):
    return D(value).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def build_row(res, currency, item, months, period_start, amortized):
    rid, pct, category = res["id"].lower(), D(str(res["expectedIncreasePct"])), res["category"]
    row = dict.fromkeys(COLUMNS, "")
    row.update({
        "Category": category, "SubscriptionId": res["subscriptionId"], "ResourceGroup": res["resourceGroup"],
        "Name": res["name"], "Type": res["type"], "Location": res["location"], "Sku": res["skuLabel"],
        "Quantity": res["instances"] if category == "Virtual Machine"
        else (1 if category in ("Managed Disk", "Disk Snapshot") else ""),
        "OsType": res.get("osType", ""), "State": res.get("state", ""), "IncreaseRegime": res["regime"],
        "ExpectedIncreasePct": str(pct), "CostSource": "None", "Currency": currency, "ResourceId": res["id"]})
    flags, base, delta, scenario = [], None, None, False
    if res.get("state") == "Unattached":
        flags.append("unattached_disk")
    if period_start and (res.get("created") or "")[:10] > period_start:
        flags.append("created_in_period")
    if item is None:
        flags.append("no_billed_usage")
    else:
        models, actual, key = item["models"], item["cost"], (rid, currency)
        savings, reservation = "SavingsPlan" in models, "Reservation" in models
        total = actual
        if savings and reservation:
            flags.append("mixed_commitments")
        elif savings and key in amortized:
            total = amortized[key]
            row["SavingsPlanMonthly"] = cents((total - actual) / months)
        elif savings:
            flags.append("savings_plan_not_sized")
        if key in amortized:
            row["AmortizedMonthlyTotal"] = cents(amortized[key] / months)
        if reservation:
            flags.append("reservation_covered_usage")
        if "Spot" in models:
            flags.append("spot")
        if item["services"] - IN_SCOPE:
            flags.append("other_meters_on_resource")
        if category in UPPER_BOUND:
            flags.append("upper_bound")
        row["ActualOnDemandMonthly"] = row["ActualSubjectMonthly"] = cents(actual / months)
        covered = actual == 0 and reservation
        row["CostSource"] = ("Cost Management (fully covered by RI)" if covered
                             else "Cost Management (OnDemand + Savings Plan)")
        base = D(0) if covered else cents(total / months)
        delta = cents(base * pct / 100)
        if category == "Virtual Machine" and res.get("state") == "PowerState/deallocated" and base > 0:
            scenario = True
            flags.append("currently_deallocated")
            row["CostSource"] = "Scenario - currently deallocated"
        else:
            row["BaseMonthlyCost"], row["EstimatedMonthlyIncrease"] = base, delta
            row["EstimatedAnnualIncrease"] = delta * 12
    notes = [NOTES[f] for f in flags]
    if scenario:
        notes.append(f"Scenario if restarted at the historical level: {base} -> {base + delta} per month")
    row["Notes"] = "; ".join(notes)
    return {"csv": row, "currency": currency, "base": base, "delta": delta, "flags": flags,
            "scenario": scenario, "res": res, "pct": pct}


def overview(exposure, costs, months=1, period_start="", amortized=None, top=5):
    months, amortized = D(months), amortized or {}
    billed = defaultdict(dict)
    for rec in costs:
        rid = (rec.get("resource_id") or "").lower()
        if rid:
            item = billed[rid].setdefault(rec["currency"], {"cost": D(0), "models": set(), "services": set()})
            item["cost"] += D(rec["cost"])
            item["models"].update(rec.get("pricing_models") or [])
            item["services"].update(str(s).lower() for s in rec.get("service_names") or [])
    rows = [build_row(res, currency, item, months, period_start, amortized)
            for res in exposure
            for currency, item in (billed.get(res["id"].lower()) or {"": None}).items()]
    counted = [r for r in rows if r["base"] is not None and not r["scenario"]]

    def group(items, key):
        out = defaultdict(lambda: {"ids": set(), "base": D(0), "delta": D(0)})
        for r in items:
            g = out[key(r)]
            g["ids"].add(r["res"]["id"].lower())
            g["base"] += r["base"]
            g["delta"] += r["delta"]
        return [{"key": list(k), "resources": len(v["ids"]), "today": str(v["base"]),
                 "after": str(v["base"] + v["delta"]), "delta_month": str(v["delta"]),
                 "delta_year": str(v["delta"] * 12)}
                for k, v in sorted(out.items(), key=lambda kv: (kv[0][0], -kv[1]["delta"]))]

    flagged = defaultdict(lambda: {"ids": set(), "base": D(0)})
    for r in rows:
        for flag in r["flags"]:
            flagged[flag]["ids"].add(r["res"]["id"].lower())
            flagged[flag]["base"] += r["base"] or D(0)
    shortlist = defaultdict(list)
    for r in sorted(counted, key=lambda r: (r["currency"], -r["delta"])):
        if len(shortlist[r["currency"]]) < top:
            res = r["res"]
            shortlist[r["currency"]].append({
                "id": res["id"], "name": res["name"], "category": res["category"], "location": res["location"],
                "sku": res["skuLabel"], "regime": res["regime"], "pct": str(r["pct"]), "base": str(r["base"]),
                "delta": str(r["delta"]), "flags": r["flags"]})
    unbilled = [r["res"]["name"] for r in rows if "no_billed_usage" in r["flags"]]
    summary = {
        "headline": group(counted, lambda r: (r["currency"],)),
        "by_category": group(counted, lambda r: (r["currency"], r["res"]["category"])),
        "by_driver": group(counted, lambda r: (
            r["currency"], r["res"]["regime"],
            r["res"]["location"] if r["res"]["regime"] == "Regional" else "all regions", str(r["pct"]))),
        "scenario_deallocated": group([r for r in rows if r["scenario"]], lambda r: (r["currency"],)),
        "flags": {k: {"resources": len(v["ids"]), "base": str(v["base"])} for k, v in flagged.items()},
        "shortlist": dict(shortlist),
        "no_billed_usage": {"count": len(unbilled), "examples": unbilled[:10]},
        "rows": len(rows),
    }
    return summary, rows


def write_csv(rows, path, disclaimer=True):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        if disclaimer:
            for line in DISCLAIMER:
                handle.write('"' + line.replace('"', '""') + '"\r\n')
            handle.write("\r\n")
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for r in sorted(rows, key=lambda r: (r["csv"]["Category"], r["currency"], -(r["delta"] or D(0)))):
            writer.writerow(r["csv"])


def verify_csv(path, summary):
    with open(path, newline="", encoding="utf-8") as handle:
        lines = list(csv.reader(handle))
    start = next(i for i, line in enumerate(lines) if line[:1] == ["Category"])
    header, data = lines[start], [line for line in lines[start + 1:] if line]
    col = {name: header.index(name) for name in ("Currency", "BaseMonthlyCost", "EstimatedMonthlyIncrease")}
    sums = defaultdict(lambda: [D(0), D(0)])
    for line in data:
        if line[col["BaseMonthlyCost"]]:
            sums[line[col["Currency"]]][0] += D(line[col["BaseMonthlyCost"]])
            sums[line[col["Currency"]]][1] += D(line[col["EstimatedMonthlyIncrease"]])
    expected = {h["key"][0]: [D(h["today"]), D(h["delta_month"])] for h in summary["headline"]}
    problems = [] if len(data) == summary["rows"] else [f"rows: file has {len(data)}, expected {summary['rows']}"]
    for currency in sorted(set(sums) | set(expected)):
        if sums.get(currency, [D(0), D(0)]) != expected.get(currency, [D(0), D(0)]):
            problems.append(f"{currency}: file {sums.get(currency)} differs from summary {expected.get(currency)}")
    return {"rows": len(data), "totals": {c: [str(v[0]), str(v[1])] for c, v in sums.items()}, "problems": problems}
```

Run it like this, passing `months` (the number of monthly reports summed into `costs`) and `period_start` (the first day of the first month):

```python
summary, rows = overview(exposure, costs, months=3, period_start="2026-07-01", amortized=amortized)
write_csv(rows, "/mnt/data/azure_price_increase_impact.csv")
print(verify_csv("/mnt/data/azure_price_increase_impact.csv", summary))
print(json.dumps(summary, default=str, indent=2))
```

- `base` is the average monthly billed cost (the summed months divided by `months`), `delta` is `base x pct / 100`, and the annual figure is the monthly figure times 12. Every row is rounded to the cent, and the totals add the rounded rows, so the file and the summary match exactly.
- Share the CSV only when `problems` is empty: the file exists, has one row per result, and its totals per currency equal the summary. Otherwise fix the cause first. Save the file with the code tool (the Python tool docs use `/mnt/data`) and confirm that it exists before giving the download link.
- Rows with `upper_bound` (storage accounts, Azure NetApp Files, Elastic SAN) are labelled as an upper bound: the excluded meters can be split only in the deep dive.
- A currently deallocated VM with historical billing is a **scenario**, reported in `scenario_deallocated` and never added to the totals. A resource with no billed usage appears in the CSV with `CostSource` `None` and no amounts.
- Adapt `DISCLAIMER` when the active profile is not the 1 February 2027 one.

## 4. Answer shape

Render in the customer's language. Keep it short: one table row per category and a shortlist of 5.

```text
Overview: <subscription(s)>, <first day> to <last day> (3 complete months), billed cost (ActualCost[ and AmortizedCost], <currency>)
With the change effective <date>, the affected resources go from <today>/month to <after>/month: +<delta>/month, about +<delta_year>/year.

| Category | Resources | Today/month | After/month | Difference/month |

What drives it:
- v1/v2 series (+25% in every region): <n> resources, +<delta>/month
- <region> (+<pct>%): <n> resources, +<delta>/month

Not included in those totals:
- <n> currently deallocated VMs. If they were restarted at the historical level they would cost <x>/month, <y> after the change. Shown as a scenario only.
- <n> affected resources with no billed usage in the period.
To verify: <each remaining flag with its resource count and amount; for example unattached disks, reservation-covered usage, savings plans, Spot, upper-bound storage>
Estimate from past billed cost; it assumes constant consumption and depends on your commercial agreement and commitments.
Full per-resource detail: <download link to the CSV>

Which resource should I look into? The 5 with the largest increase:
1. <name> (<category>, <region>, <sku>): +<delta>/month
You can also give me a name, a resource ID, a category, a region, or a resource group.
```

## 5. General cost overview (no price change)

1. Request `AzureCostDetails-Report` (`ActualCost`) for each subscription in scope, for the last three complete months or the period asked.
2. For each month call `AzureCostDetails-Read` with `view="resources"`, `page_size=10`, `offset=0`. Use `totals_by_currency` for the monthly total and `records` for the top 10. Derive the resource type from the segment after `/providers/` in the resource ID.
3. Answer with the monthly average per currency, the top 10 (name, type, average cost, share of the total, pricing models), the unattributed bucket when present, and the period stated explicitly.
4. Offer a deep dive on one resource, or the impact of an announced price change on the same resources.
