# Data acquisition

Use the Azure SRE Agent built-in Azure CLI read tool for Azure Resource Graph. All operations below are read-only.

## Azure Resource Graph

Run each query with `az graph query -q "<QUERY>" --first 1000`. Follow the returned skip token until all pages are retrieved. Preserve the full resource ID.

### Virtual machines and virtual machine scale sets

```kusto
Resources
| where type in~ ("microsoft.compute/virtualmachines", "microsoft.compute/virtualmachinescalesets")
| extend isVmss = type =~ "microsoft.compute/virtualmachinescalesets"
| extend vmSize = iff(isVmss, tostring(sku.name), tostring(properties.hardwareProfile.vmSize))
| extend instances = iff(isVmss, toint(sku.capacity), 1)
| extend osType = iff(isVmss, tostring(properties.virtualMachineProfile.storageProfile.osDisk.osType), tostring(properties.storageProfile.osDisk.osType))
| extend powerState = iff(isVmss, "n/a", tostring(properties.extended.instanceView.powerState.code))
| extend s = tolower(vmSize), region = tolower(location)
| extend isV1 = s matches regex "^standard_(b[0-9]+(ls|s|ms)|d[0-9]+(-[0-9]+)?|ds[0-9]+(-[0-9]+)?|f[0-9]+s?|g[0-9]+|gs[0-9]+(-[0-9]+)?|l[0-9]+s|np[0-9]+s|hc[0-9]+(-[0-9]+)?rs)(_promo)?$"
| extend isV2 = s matches regex "^standard_(a[0-9]+m?|ds?[0-9]+(-[0-9]+)?|f[0-9]+s|l[0-9]+s)_v2(_promo)?$"
| extend isIntelV7 = s matches regex "^standard_(d[0-9]+(-[0-9]+)?(s|ds|ls|lds)|e[0-9]+(-[0-9]+)?(s|ds))_v7$"
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| extend expectedIncreasePct = case(isV1 or isV2, 25, regionalPct > 0 and isIntelV7 == false, regionalPct, 0)
| extend increaseRegime = case(isV1 or isV2, "v1/v2 series increase", expectedIncreasePct > 0, "Regional increase", "Not affected")
| where expectedIncreasePct > 0
| project subscriptionId, resourceGroup, name, type, location, vmSize, instances, osType, powerState, increaseRegime, expectedIncreasePct, id
```

### Managed disks

```kusto
Resources
| where type =~ "microsoft.compute/disks"
| extend region = tolower(location)
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| where regionalPct > 0
| project subscriptionId, resourceGroup, name, type, location, diskSku = tostring(sku.name), diskSizeGB = toint(properties.diskSizeGB), diskTier = tostring(properties.tier), diskState = tostring(properties.diskState), diskIops = tolong(properties.diskIOPSReadWrite), diskMbps = tolong(properties.diskMBpsReadWrite), regionalPct, id
```

### Disk snapshots

```kusto
Resources
| where type =~ "microsoft.compute/snapshots"
| extend region = tolower(location)
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| where regionalPct > 0
| project subscriptionId, resourceGroup, name, type, location, snapSku = tostring(sku.name), diskSizeGB = toint(properties.diskSizeGB), incremental = tobool(properties.incremental), regionalPct, id
```

### Storage accounts, Azure NetApp Files, and Elastic SAN

```kusto
Resources
| where type in~ ("microsoft.storage/storageaccounts", "microsoft.netapp/netappaccounts/capacitypools", "microsoft.elasticsan/elasticsans")
| extend region = tolower(location)
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| where regionalPct > 0
| extend skuName = tostring(sku.name)
| project subscriptionId, resourceGroup, name, type, location, kind, skuName, regionalPct, id
```

When the user supplies a narrower scope, add subscription or resource-group filters without changing the classification expressions.

## Billed cost

Billed cost comes from the Cost Details tools. When they are throttled (HTTP 429) or not supported for the subscription, `AzureCostDetails-Query` is the only permitted second source: it calls the same Cost Management Query API as the PowerShell script, through a Python tool. The read-only CLI channel rejects Cost Management Query API POST requests, so do not retry them through the CLI, do not route them through a write tool, and do not replace them with an estimate.

| What the PowerShell script reads | Cost Details equivalent | Query equivalent (`AzureCostDetails-Query`) |
|---|---|---|
| Query A: actual on-demand cost by resource | `AzureCostDetails-Report` with `metric="ActualCost"`, then `AzureCostDetails-Read` with `view="resources"`: cost, `pricing_models`, and `service_names` per resource | `group_by="resource"` with `meter_categories="Virtual Machines,Storage"`: `cost` and `pricing_models` per resource, with the other meters left out at the source |
| Query A: split by meter subcategory | `AzureCostDetails-Read` with `view="rows"` and `resource_id`, for one resource at a time (storage meter exclusions) | `group_by="meter"` with `resource_id`: cost per meter subcategory and pricing model |
| Query B: amortized cost by pricing model | The same tools with `metric="AmortizedCost"` | `metric="both"`: `amortized_cost` in the same record |

One report covers one calendar month of one subscription, so request one report per month. One Query call covers any span up to 366 days. The cost and calculation steps are in the skill `azure-cost-advisor` (`references/overview-workflow.md`, sections 2 and 3). The tool mechanics and the choice between the two sources are in the skill `azure-cost-details`.

## Azure Retail Prices API

The public endpoint requires no Azure credential:

```text
https://prices.azure.com/api/retail/prices?currencyCode='<CURRENCY>'&$filter=<ODATA_FILTER>
```

Follow `NextPageLink` until empty. Cache responses by filter during an assessment. Select the applicable item using the matching rules in `assessment-rules.md` and the latest `effectiveStartDate`.
