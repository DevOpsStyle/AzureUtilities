# Data acquisition

Use the Azure SRE Agent built-in Azure CLI read tool for all authenticated Azure calls. All operations below are read-only.

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

## Cost Management Query API

For each subscription containing an affected resource, POST to:

```text
https://management.azure.com/subscriptions/<subscription-id>/providers/Microsoft.CostManagement/query?api-version=2023-03-01
```

Use `az rest --method post --url "<URL>" --body '<BODY>'`.

Replace `<FROM>` and `<TO>` with ISO dates.

### Actual on-demand cost by resource and meter

```json
{
  "type": "ActualCost",
  "timeframe": "Custom",
  "timePeriod": {
    "from": "<FROM>",
    "to": "<TO>"
  },
  "dataset": {
    "granularity": "None",
    "aggregation": {
      "totalCost": {
        "name": "Cost",
        "function": "Sum"
      }
    },
    "grouping": [
      {
        "type": "Dimension",
        "name": "ResourceId"
      },
      {
        "type": "Dimension",
        "name": "MeterSubcategory"
      }
    ],
    "filter": {
      "and": [
        {
          "dimensions": {
            "name": "MeterCategory",
            "operator": "In",
            "values": [
              "Virtual Machines",
              "Storage"
            ]
          }
        },
        {
          "dimensions": {
            "name": "PricingModel",
            "operator": "In",
            "values": [
              "OnDemand"
            ]
          }
        }
      ]
    }
  }
}
```

### Amortized cost by resource and pricing model

```json
{
  "type": "AmortizedCost",
  "timeframe": "Custom",
  "timePeriod": {
    "from": "<FROM>",
    "to": "<TO>"
  },
  "dataset": {
    "granularity": "None",
    "aggregation": {
      "totalCost": {
        "name": "Cost",
        "function": "Sum"
      }
    },
    "grouping": [
      {
        "type": "Dimension",
        "name": "ResourceId"
      },
      {
        "type": "Dimension",
        "name": "PricingModel"
      }
    ],
    "filter": {
      "dimensions": {
        "name": "MeterCategory",
        "operator": "In",
        "values": [
          "Virtual Machines",
          "Storage"
        ]
      }
    }
  }
}
```

Read column positions from `properties.columns`; do not assume a fixed row order. Continue POST requests against `properties.nextLink` with the same body until no next link remains.

## Azure Retail Prices API

The public endpoint requires no Azure credential:

```text
https://prices.azure.com/api/retail/prices?currencyCode='<CURRENCY>'&$filter=<ODATA_FILTER>
```

Follow `NextPageLink` until empty. Cache responses by filter during an assessment. Select the applicable item using the matching rules in `assessment-rules.md` and the latest `effectiveStartDate`.
