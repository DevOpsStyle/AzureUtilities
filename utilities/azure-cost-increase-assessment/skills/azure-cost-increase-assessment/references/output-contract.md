# Output contract

## Executive summary

Start with:

- estimate disclaimer
- assessed scope and complete-month date range
- affected-resource count
- estimated monthly and annual increase, grouped by currency
- number of subscriptions with complete and incomplete cost data

Do not present a single cross-currency total.

## Findings

Show the highest estimated monthly increases first:

| Resource | Category | Region | SKU | Regime | Baseline source | Monthly increase | Annual increase | Notes |
|---|---|---|---|---|---|---:|---:|---|

Include at least the top 10 rows when available. Highlight:

- list-price fallback
- partial or full Reserved Instance coverage
- Savings Plan coverage
- excluded-meter-only rows
- deallocated VMs
- unattached disks
- incremental disk snapshots
- missing billed or retail price data

## CSV

When file generation is available, create `azure_price_increase_impact.csv` in UTF-8 with these columns:

`Category` can be `Virtual Machine`, `Managed Disk`, `Disk Snapshot`, `Storage Account`, `Azure NetApp Files`, or `Elastic SAN`.

```text
Category
SubscriptionId
ResourceGroup
Name
Type
Location
Sku
Quantity
OsType
State
IncreaseRegime
ExpectedIncreasePct
CostSource
Currency
BaseMonthlyCost
EstimatedMonthlyIncrease
EstimatedAnnualIncrease
ActualOnDemandMonthly
ActualSubjectMonthly
SavingsPlanMonthly
AmortizedMonthlyTotal
OnDemandSharePct
ReservationSharePct
SavingsPlanSharePct
ListPriceMonthly
Notes
ResourceId
```

By default, place these quoted disclaimer lines before the CSV table, followed by one empty line:

```text
DISCLAIMER: This file contains an ESTIMATE of the impact of the Azure price changes effective 1 February 2027.
It is intended to give an indication of the potential impact only. Values are based on past consumption (or on public list prices where no billed usage is available) and assume constant volumes.
The final impact depends on the actual usage of the resources, on the applicable commercial agreement and on commitments (Reserved Instances, Savings Plans), and must be verified against actual billing.
```

If the user requests a machine-readable CSV, omit these lines and put the disclaimer in the chat response instead.

Use a comma delimiter and dot decimal separator. Leave unavailable numeric values empty rather than writing zero.

## Data-quality summary

Report counts for:

- `Cost Management (OnDemand + Savings Plan)`
- `Cost Management (fully covered by RI)`
- `Cost Management (excluded meters only)`
- `Retail list price (<currency>)`
- `None`
- subscriptions whose Resource Graph or Cost Management requests failed

List every incomplete subscription and its error. Distinguish missing permissions from throttling or service errors.

## Required closing note

State that the result is an estimate based on historical consumption or public list prices and must be validated against actual billing and applicable commercial agreements.
