# Deep-dive playbooks

Use after the customer picked a resource or named one. Lead the answer with the verdict. Reuse the reports, `operation_url` values, and CSV column names already obtained in the conversation, and the same three months as the overview unless the customer asks for another period.

## 1. Resolve the resource

Run through the Azure CLI read tool, with the same quoting rules as in the [overview workflow](./overview-workflow.md#1-exposure-from-azure-resource-graph).

### Resolve by name or ID

```kusto
Resources
| where id =~ "<RESOURCE_ID>" or name =~ "<RESOURCE_NAME>"
| project id, name, type, subscriptionId, resourceGroup, location, kind, skuName = tostring(sku.name), tags
| take 10
```

- No row: say it was not found in the accessible subscriptions and ask for the subscription or the resource ID.
- More than one row: show at most 5 with subscription, resource group, and region, and ask which one.
- A type outside the table in section 3: it is not covered by the active profile. Say so, and give the general deep dive (section 9).

## 2. Read the facts

### Rule classification

Build the query from the base in [overview-workflow](./overview-workflow.md#base) followed by this tail. It returns the facts used by the rule, also for resources that are not affected.

```kusto
| where id =~ "<RESOURCE_ID>"
| project id, name, type, category, location = region, vmSize, isV1, isV2, isIntelV7, regionalPct, expectedIncreasePct, regime
```

### Virtual machine

```kusto
Resources
| where type =~ "microsoft.compute/virtualmachines" and id =~ "<RESOURCE_ID>"
| project id, name, subscriptionId, resourceGroup, location, zones, vmSize = tostring(properties.hardwareProfile.vmSize), osType = tostring(properties.storageProfile.osDisk.osType), licenseType = tostring(properties.licenseType), priority = tostring(properties.priority), powerState = tostring(properties.extended.instanceView.powerState.code), scaleSetId = tostring(properties.virtualMachineScaleSet.id), created = tostring(properties.timeCreated), tags
```

### Virtual machine scale set

```kusto
Resources
| where type =~ "microsoft.compute/virtualmachinescalesets" and id =~ "<RESOURCE_ID>"
| project id, name, subscriptionId, resourceGroup, location, vmSize = tostring(sku.name), capacity = toint(sku.capacity), orchestrationMode = tostring(properties.orchestrationMode), priority = tostring(properties.virtualMachineProfile.priority), osType = tostring(properties.virtualMachineProfile.storageProfile.osDisk.osType), licenseType = tostring(properties.virtualMachineProfile.licenseType), tags
```

### Managed disk

```kusto
Resources
| where type =~ "microsoft.compute/disks" and id =~ "<RESOURCE_ID>"
| project id, name, subscriptionId, resourceGroup, location, diskSku = tostring(sku.name), diskSizeGB = toint(properties.diskSizeGB), tier = tostring(properties.tier), diskState = tostring(properties.diskState), attachedTo = tostring(managedBy), iops = tolong(properties.diskIOPSReadWrite), mbps = tolong(properties.diskMBpsReadWrite), created = tostring(properties.timeCreated), tags
```

### Disk snapshot

```kusto
Resources
| where type =~ "microsoft.compute/snapshots" and id =~ "<RESOURCE_ID>"
| project id, name, subscriptionId, resourceGroup, location, snapSku = tostring(sku.name), diskSizeGB = toint(properties.diskSizeGB), incremental = tobool(properties.incremental), created = tostring(properties.timeCreated), tags
```

### Storage account

```kusto
Resources
| where type =~ "microsoft.storage/storageaccounts" and id =~ "<RESOURCE_ID>"
| project id, name, subscriptionId, resourceGroup, location, kind, skuName = tostring(sku.name), accessTier = tostring(properties.accessTier), isHns = tobool(properties.isHnsEnabled), created = tostring(properties.creationTime), tags
```

### NetApp capacity pool and Elastic SAN

```kusto
Resources
| where type in~ ("microsoft.netapp/netappaccounts/capacitypools", "microsoft.elasticsan/elasticsans") and id =~ "<RESOURCE_ID>"
| project id, name, type, subscriptionId, resourceGroup, location, skuName = tostring(sku.name), properties, tags
```

### Disks attached to a virtual machine

```kusto
Resources
| where type =~ "microsoft.compute/disks" and managedBy =~ "<VM_RESOURCE_ID>"
| project id, name, location, diskSku = tostring(sku.name), diskSizeGB = toint(properties.diskSizeGB), diskState = tostring(properties.diskState)
```

## 3. Decide which rule applies

Explain the result of the classification in one or two sentences that name the matched rule and the facts behind it.

| Classification | How to explain it |
|---|---|
| `v1/v2 series` | The size belongs to the v1/v2 families: +25% in every region. The region plays no role and the regional percentage is not added |
| `Regional` | Not a v1/v2 size and not Intel v7, and the region is one of the 7 affected: +the regional percentage |
| `Intel v7 (not affected)` | Intel v7 sizes are excluded in every region |
| `Not affected` | The region is not one of the 7 and the size is not in the v1/v2 families: no increase |
| No row | The type is not covered by the active profile: no known increase, so give the general deep dive |

Cases to evaluate by type:

| Type | What to check |
|---|---|
| Virtual machine | Size family and region as above. Spot (`priority`): not addressed, no estimate. Deallocated: see section 4. Azure Hybrid Benefit (`licenseType`): informational, the billed cost already reflects it |
| Scale set | Cost sits on the scale set for Uniform orchestration, and on the instances for Flexible orchestration (do not count both). A scale set in a resource group named `MC_*`, or with an `aks-managed-poolName` tag, is an AKS node pool: its size is changed through the node pool |
| Managed disk | Region only. `Unattached` or `Reserved` state still bills. Premium SSD v2 and Ultra bill capacity, IOPS, and throughput as separate meters |
| Disk snapshot | Region only. Incremental snapshots bill on used size. Usage based: no list-price estimate |
| Storage account | Region only, but only the meters that are not excluded. Split the billed meters (section 4) |
| Azure NetApp Files pool, Elastic SAN | Region only. Split the billed meters; Cross Region Replication is excluded |

If the size does not match a documented family, apply "any other series" and say that the classification rests on the name pattern.

## 4. Billing evidence

1. **Report**: reuse the `operation_url` of the subscription and month from the overview. Otherwise generate it (`ActualCost`, the three complete months). If a savings plan appears, also use the `AmortizedCost` report of the same month.
2. **Column names**, once per subscription: call `AzureCostDetails-Read` with `view="rows"`, `resource_id=<the resource ID>`, `page_size=1`, and read `available_columns`.
3. **Rows**: for each month call `AzureCostDetails-Read` with `view="rows"`, the same `resource_id`, `page_size=200`, and `columns` limited to those of this list that exist in `available_columns`: Date, MeterCategory, MeterSubCategory, MeterName, Quantity, UnitOfMeasure, UnitPrice, EffectivePrice, CostInBillingCurrency, BillingCurrencyCode, PricingModel, ChargeType, ReservationId, ReservationName, ProductOrderId, Term, BenefitId, BenefitName. For a virtual machine also add `AdditionalInfo`.
4. **Summarize in the sandbox** with these helpers. Paste the rows, never add them by hand.

```python
import re
from collections import defaultdict
from decimal import Decimal as D

EXCLUDED = ["Container Storage", "Managed Lustre", "Cross Region Replication", "Blob Features", "Change Feed",
            "General Block Blob", "Import/Export", "Queues", "Standard Page Blob", "Storage Actions", "Bandwidth",
            "Storage Discovery", "Tables", "Transaction Optimized"]


def pick(row, *names):
    return next((row[n] for n in names if row.get(n) not in (None, "")), "")


def breakdown(rows, legacy_blob_excluded=False):
    patterns = EXCLUDED + (["^Blob Storage"] if legacy_blob_excluded else [])
    groups = defaultdict(lambda: {"cost": D(0), "quantity": D(0), "days": set()})
    for r in rows:
        category, sub = pick(r, "MeterCategory"), pick(r, "MeterSubCategory")
        excluded = category.lower() == "storage" and any(re.search(p, sub, re.I) for p in patterns)
        key = (category, sub, pick(r, "PricingModel"), pick(r, "ChargeType"),
               pick(r, "BillingCurrencyCode", "BillingCurrency", "Currency"), excluded)
        g = groups[key]
        g["cost"] += D(pick(r, "CostInBillingCurrency", "Cost", "PreTaxCost") or 0)
        g["quantity"] += D(pick(r, "Quantity") or 0)
        g["days"].add(pick(r, "Date"))
    return [{"meter_category": k[0], "meter_subcategory": k[1], "pricing_model": k[2], "charge_type": k[3],
             "currency": k[4], "excluded_meter": k[5], "cost": str(v["cost"]), "quantity": str(v["quantity"]),
             "days": len(v["days"])} for k, v in sorted(groups.items())]


def reservation_share(groups, category="Virtual Machines"):
    total = sum(D(g["quantity"]) for g in groups if g["meter_category"] == category)
    reserved = sum(D(g["quantity"]) for g in groups
                   if g["meter_category"] == category and g["pricing_model"] == "Reservation")
    return (reserved / total * 100).quantize(D("0.1")) if total else None


def size_key(value):
    value = re.sub(r"\b(spot|low priority)\b", "", value, flags=re.I).lower().strip()
    return re.sub(r"^(standard|basic)_", "", value).replace(" ", "").replace("_", "").replace("-", "")


def sku_changed(vm_size, rows):
    billed = set()
    for r in rows:
        match = re.search(r'"ServiceType"\s*:\s*"([^"]+)"', pick(r, "AdditionalInfo"))
        if match:
            billed.add(size_key(match.group(1)))
        elif pick(r, "MeterCategory").lower() == "virtual machines":
            billed.update(size_key(part) for part in pick(r, "MeterName").split("/") if part.strip())
    current = size_key(vm_size)
    return {"current": current, "billed": sorted(billed), "possible_resize": bool(billed) and current not in billed}
```

5. **Baseline** for the resource, as a monthly average over the months read: the sum of `cost` of the groups with `pricing_model == "OnDemand"`, `charge_type == "Usage"`, a `meter_category` in the profile scope, and `excluded_meter` false, divided by the number of months. When `SavingsPlan` groups exist, add the cost of those groups from the `AmortizedCost` report. A reservation group is not part of the baseline.
6. **Compare** the billed size with the current size (`sku_changed`) for a virtual machine, and flag a possible resize: the cost may not reflect the current size. The rule always follows the current size.
7. **Deallocated VM** (`powerState` is `PowerState/deallocated`): compute is not billed while it stays stopped. Show the historical cost as a separate running-cost scenario, "if restarted at the historical level", and never as an expected bill. With no billed history, a list-price figure is allowed only as a hypothetical scenario (rules in skill `azure-cost-increase-assessment`, `references/assessment-rules.md`, "Retail list-price fallback"), labelled "hypothetical, not billed". The disks of that VM keep billing.
8. **Missing data**: say plainly which months, meters, or columns were unavailable and what that changes.

## 5. Commitments and timeline

| Evidence in the rows | Rule |
|---|---|
| No `Reservation` or `SavingsPlan` rows | The increase starts on the effective date |
| `SavingsPlan` rows | The increase applies from the effective date: covered usage reflects the new underlying rates. Name the plan (`BenefitName`); no expiry lookup is needed |
| `Reservation` rows | The increase on the reserved part is deferred until the reservation expires |
| `Spot` rows | Not addressed by the announcement. No estimate |

For a reservation, size the coverage with `reservation_share` and read its expiry (best effort) with a read-only call:

```text
az rest --method get --url "https://management.azure.com/providers/Microsoft.Capacity/reservations?api-version=2022-11-01&take=100"
```

Follow `nextLink` if present. Match the item whose `id` ends with the `ReservationId` from the rows, or whose `properties.displayName` equals `ReservationName`. Read `properties.expiryDate`, `properties.term`, `properties.reservedResourceType`, `properties.renew`, and `properties.userFriendlyRenewState`. The call needs reservation read access: if it is denied, say the expiry could not be verified and point to Portal > Reservations.

Timeline for the reserved part:

- Expiry known: it is affected from the later of the effective date and the day after the expiry. If the expiry is before the effective date and the reservation is not renewed, the part becomes pay-as-you-go and follows the increase from the effective date.
- Expiry unknown: say "deferred until expiry, not verified".
- Renewal and exchange terms are not estimated.
- Give two phases when both apply: the on-demand part from the effective date, and the reserved part from its own date. Do not estimate the reserved part after expiry; the reservation price is no longer the reference.

## 6. Related resources

For a virtual machine, run the attached-disks query and report the number of disks, their region, state, and SKU. Offer to add their cost and increase. If the customer agrees, read `view="resources"` as in the overview and filter by those disk IDs. Disks are separate resources: a deallocated VM keeps billing its disks, and an unattached disk is a cleanup candidate to confirm with its owner.

## 7. Verdict

Render in the customer's language and lead with the first line.

```text
<name> - <category>, <region>, <size or SKU>
Impact: <Affected | Not affected | Deferred | Partly affected> - <one sentence: the matched rule, the facts, the percentage>.
Cost: <today>/month -> <after>/month from <date> (+<delta>/month, about +<delta_year>/year). Basis: <months, metric, meters counted>.
Commitments: <none | reservation <name> covering <x>%, expires <date or "not verified"> | savings plan <name>>.
Related resources: <attached disks, with cost and increase if the customer asked>.
To check: <resize signal, deallocated scenario, unattached state, missing data, assumptions about future usage>.
Options to evaluate: <only those supported by the facts>.
```

## 8. Options to evaluate

Offer only options derived from the facts, never a promised saving, and never a change to a resource.

- **Series**: Intel v7 sizes listed in the profile are not affected; a move needs validation of availability, quota, performance, and licensing.
- **Region**: for a regional-regime resource, regions outside the seven are not affected; for v1/v2 sizes the region does not matter. A region change involves latency, residency, and compliance: raise it only if the customer asks.
- **Idle resources**: a deallocated VM still bills its disks; an unattached disk or an old snapshot still bills. Suggest confirming with the owner, not deleting.
- **Commitments**: plan around the reservation expiry date and check the commitment options for the target series with the account team.
- **Agreement**: a negotiated discount can change the real impact; validate with the account team.

## 9. General deep dive (no price change)

For a resource outside the profile, or when the customer asks only about cost:

1. Read its rows (section 4, steps 1 to 3) and summarize with `breakdown`.
2. Report the monthly average, the split by meter category and subcategory, and the share by pricing model.
3. Show the daily trend: sum `CostInBillingCurrency` by `Date` in the sandbox and name the 3 highest days with their difference from the median.
4. Report commitment coverage (section 5) and any `EffectivePrice` versus `UnitPrice` gap.
5. State that no known price increase applies to this type under the active profile, and offer the related resources.
