# Assessment rules

## Effective date

The assessed price changes take effect on 1 February 2027.

## Virtual machine classification

Normalize the Azure VM size and region to lowercase.

Use these expressions to reproduce the reference implementation:

```text
isV1:
^standard_(b[0-9]+(ls|s|ms)|d[0-9]+(-[0-9]+)?|ds[0-9]+(-[0-9]+)?|f[0-9]+s?|g[0-9]+|gs[0-9]+(-[0-9]+)?|l[0-9]+s|np[0-9]+s|hc[0-9]+(-[0-9]+)?rs)(_promo)?$

isV2:
^standard_(a[0-9]+m?|ds?[0-9]+(-[0-9]+)?|f[0-9]+s|l[0-9]+s)_v2(_promo)?$

isIntelV7:
^standard_(d[0-9]+(-[0-9]+)?(s|ds|ls|lds)|e[0-9]+(-[0-9]+)?(s|ds))_v7$
```

Apply exactly one regime:

| Condition | Expected increase | Regime |
|---|---:|---|
| `isV1` or `isV2` | 25% in every region | `v1/v2 series increase` |
| Not v1/v2, not Intel v7, and in an affected region | Regional percentage | `Regional increase` |
| Intel v7 | 0% | Not affected |
| Any other resource outside the affected regions | 0% | Not affected |

Do not stack the 25% v1/v2 increase with a regional increase.

## Regional percentages

| Azure region | Increase |
|---|---:|
| `northeurope` | 17% |
| `francecentral`, `francesouth` | 11% |
| `westeurope`, `norwayeast`, `norwaywest` | 9% |
| `southeastasia` | 8% |
| All other regions | 0% |

The regional notice names `northeurope` "EU North (Dublin)" and `westeurope` "EU West (Amsterdam)". Treat each pair of names as the same region.

## Storage scope

In affected regions, include:

- `microsoft.compute/disks`
- `microsoft.compute/snapshots`
- `microsoft.storage/storageaccounts`
- `microsoft.netapp/netappaccounts/capacitypools`
- `microsoft.elasticsan/elasticsans`

Exclude on-demand cost rows whose meter subcategory contains, case-insensitively:

- `Container Storage`
- `Managed Lustre`
- `Cross Region Replication`
- `Blob Features`
- `Change Feed`
- `General Block Blob`
- `Import/Export`
- `Queues`
- `Standard Page Blob`
- `Storage Actions`
- `Bandwidth`
- `Storage Discovery`
- `Tables`
- `Transaction Optimized`

These patterns also exclude names with a `v2` suffix. The notice lists only the excluded services, not the affected ones: treat every other billed meter of these resource types as affected and say that this is an interpretation. Exclude meter subcategories beginning with `Blob Storage` only when the user explicitly enables the assumed legacy Blob Storage exclusions.

## Cost Management aggregation

The billed cost comes from the Cost Details tools. `AzureCostDetails-Read` with `view="resources"` returns, for each resource and currency, the total cost, the pricing models, and the service names present. It does not split meter subcategories: do that with `view="rows"` for one resource at a time (see the skill `azure-cost-advisor`, `references/deep-dive-playbooks.md`). In bulk, the baseline of storage accounts, Azure NetApp Files, and Elastic SAN is therefore an upper bound.

Filter Cost Management data to the `Virtual Machines` and `Storage` meter categories. Ignore other charges associated with the same resource ID, such as Microsoft Defender. In bulk, a resource whose service names include other categories is flagged because its baseline may be overstated.

For each lowercase resource ID, aggregate:

- all Actual Cost `OnDemand` rows as `OnDemand`
- Actual Cost `OnDemand` rows not matching a storage exclusion as `OnDemandSubject`
- Amortized Cost rows separately for `OnDemand`, `Reservation`, `SavingsPlan`, and any other returned pricing model

Divide every aggregate by the number of complete months in the assessment window.

```text
AmortizedMonthlyTotal = sum(all amortized pricing models) / CostMonths
SavingsPlanMonthly = SavingsPlan amortized cost / CostMonths
OnDemandSharePct = 100 * OnDemand amortized cost / total amortized cost
ReservationSharePct = 100 * Reservation amortized cost / total amortized cost
SavingsPlanSharePct = 100 * SavingsPlan amortized cost / total amortized cost
```

## Baseline selection

Choose one source in this order:

1. If `ActualSubjectMonthly + SavingsPlanMonthly > 0`:
   - `CostSource = Cost Management (OnDemand + Savings Plan)`
   - `BaseMonthlyCost = ActualSubjectMonthly + SavingsPlanMonthly`
2. Otherwise, if amortized cost is positive and Reserved Instance share is at least 99.9%:
   - `CostSource = Cost Management (fully covered by RI)`
   - `BaseMonthlyCost = 0`
3. Otherwise, if on-demand cost is positive and subject on-demand cost is zero:
   - `CostSource = Cost Management (excluded meters only)`
   - `BaseMonthlyCost = 0`
4. Otherwise, if a supported retail list price was found:
   - `CostSource = Retail list price (<currency>)`
   - `BaseMonthlyCost = ListPriceMonthly`
5. Otherwise:
   - `CostSource = None`
   - leave `BaseMonthlyCost` and increase values empty

Reserved Instance-covered usage is excluded: the price changes when the reservation expires. Savings Plan usage is included: the covered VMs cost more, so the commitment is consumed sooner.

```text
EstimatedMonthlyIncrease = round(BaseMonthlyCost * ExpectedIncreasePct / 100, 2)
EstimatedAnnualIncrease = EstimatedMonthlyIncrease * 12
```

## Retail list-price fallback

Use the Azure Retail Prices API only when no billed usage is available.

### Virtual machines and scale sets

- Filter `serviceName = Virtual Machines`, `priceType = Consumption`, exact region, and exact ARM SKU.
- Exclude Spot and Low Priority meters.
- Match Windows products for Windows resources and non-Windows products for Linux resources.
- Select the most recent `effectiveStartDate`.
- Monthly price is hourly price multiplied by 730 and by the VM or scale-set instance quantity.

### Managed disks

- For Premium SSD, Standard SSD, and Standard HDD, map provisioned size to the matching P/E/S disk tier and use the monthly disk meter.
- For Premium SSD v2, calculate capacity plus IOPS above 3,000 plus throughput above 125 MBps, multiplied by 730 hours.
- For Ultra Disk, calculate capacity plus IOPS plus throughput, multiplied by 730 hours. The vCPU reservation charge is not included.

Do not use a list-price fallback for disk snapshots, Storage Accounts, Azure NetApp Files, or Elastic SAN because their costs are usage-based.

## Limitations

- Past consumption is treated as the forecast; growth, decommissioning, and autoscale changes are not projected.
- Cost Management data can lag by 24 to 48 hours.
- Spot usage is not included in the billed baseline.
- Reservation expiry dates are not read; manually verify reservations that expire before 1 February 2027.
- Dedicated Hosts, Azure Batch pools, Azure File Sync, Azure Backup, Azure VMware Solution, and PaaS compute are outside scope.
- Storage estimates depend on the billed meter mix.
- If the Retail Prices API starts returning prices already effective on or after 1 February 2027, applying the increase again can double count. Flag such rows for manual review.
