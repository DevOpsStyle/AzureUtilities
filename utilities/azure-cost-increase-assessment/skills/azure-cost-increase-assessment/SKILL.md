---
name: azure-cost-increase-assessment
description: Assess the estimated impact of Azure compute and storage price increases effective 1 February 2027. Use when asked which Azure resources are affected, to forecast monthly or annual increases, to compare Cost Management baselines with public list prices, or to produce a cost-increase CSV or report.
---

# Azure Cost Increase Assessment

## Purpose

Identify affected Azure virtual machines, virtual machine scale sets, managed disks, storage accounts, Azure NetApp Files capacity pools, and Elastic SAN resources. Estimate the monthly and annual increase by using recent billed cost where possible and public list prices only as a fallback.

This is an estimate, not a bill or commercial quote. Always state that the final impact depends on actual usage, commercial agreements, Reserved Instances, Savings Plans, and future price publication.

## Operating rules

- Use only read operations.
- Use the built-in Azure CLI read tool for Azure Resource Graph and Cost Management requests.
- Use sandboxed code execution only to parse responses, calculate results, and generate the report.
- Never resize, stop, delete, retag, or otherwise modify a resource.
- Never invent missing cost, currency, reservation, or price data.
- Preserve each source currency. Do not combine totals from different currencies without separating and warning about them.
- Respond in the user's language unless the user requests another language.

## Inputs and defaults

Capture these values from the request:

| Input | Default | Rule |
|---|---|---|
| Scope | All subscriptions accessible to the agent | Honor a narrower subscription or resource-group scope when supplied |
| Cost months | 3 | Use complete past calendar months only |
| Fallback currency | Billing currency when known | Used only for Azure Retail Prices fallback |
| List-price fallback | Enabled | Disable when the user requests billed data only |
| Assumed legacy Blob Storage exclusions | Disabled | Enable only when explicitly requested |

If list-price fallback is needed and the fallback currency is unknown, ask the user for it before obtaining retail prices. Cost Management values keep the currency returned by the API.

## Procedure

### 1. Establish the assessment window

Use the last `Cost months` complete calendar months:

- `from`: first day of the earliest included month
- `to`: last day of the previous month

State the scope, date range, and fallback currency before presenting results.

### 2. Inventory potentially affected resources

Run the three paginated Azure Resource Graph queries in [data acquisition](./references/data-acquisition.md):

1. Virtual machines and virtual machine scale sets
2. Managed disks
3. Storage accounts, Azure NetApp Files capacity pools, and Elastic SAN

Apply the classifications in [assessment rules](./references/assessment-rules.md). Keep only affected resources. If none are found, report the checked scope and rules, then stop without issuing Cost Management queries.

### 3. Retrieve billed cost

For every subscription containing an affected resource, run both Cost Management queries in [data acquisition](./references/data-acquisition.md):

1. Actual cost for `PricingModel = OnDemand`, grouped by resource ID and meter subcategory
2. Amortized cost grouped by resource ID and pricing model

Follow every `nextLink`. On HTTP 429, honor `Retry-After` when present, otherwise wait 30 seconds, and retry up to six times. Surface any subscription that fails because of permissions or another API error; do not treat it as zero cost.

Normalize resource IDs to lowercase before joining inventory and cost rows.

### 4. Calculate the baseline

Apply the formulas and commitment handling in [assessment rules](./references/assessment-rules.md):

- exclude nonaffected storage meters from the on-demand baseline
- include Savings Plan amortized cost
- exclude Reserved Instance cost until reservation expiry
- use retail list price only when no billed usage is available

If a retail price cannot be matched confidently, set the cost source to `None` and explain why. Do not substitute a similar SKU or region.

### 5. Validate the result

Before reporting:

1. Confirm every result has a positive expected increase percentage.
2. Confirm v1/v2 and regional percentages were not stacked.
3. Confirm Intel v7 exclusions were applied.
4. Confirm each Cost Management row was divided by the number of assessed months.
5. Confirm monthly increase equals baseline multiplied by the percentage.
6. Confirm annual increase equals monthly increase multiplied by 12.
7. Confirm totals are grouped by currency.
8. Flag list-price rows, fully Reserved Instance-covered rows, deallocated VMs, and unattached disks.

### 6. Produce the report

Follow [the output contract](./references/output-contract.md).

Return:

- assessment scope and period
- affected-resource counts by category
- estimated monthly and annual increases by currency
- top affected resources
- data-quality summary showing billed, list-price, fully Reserved Instance-covered, missing-price, and failed-subscription counts
- limitations and the estimate disclaimer
- a UTF-8 CSV attachment when file generation is available

## Failure handling

- If Azure Resource Graph access fails, stop and report that Reader access is required.
- If Cost Management access fails for some subscriptions, continue with successful subscriptions and clearly list incomplete ones. Use retail fallback only where its matching rules apply.
- If all Cost Management requests fail, label the result as list-price-only and do not imply that discounts, Azure Hybrid Benefit, commitments, or actual running hours were considered.
- If the Azure CLI read tool or code execution is unavailable, explain which capability is missing and provide the equivalent local command from the utility documentation. Do not return a success-shaped estimate.

## Local reference implementation

The repository also contains `CostIncrease_Assessment_v5.ps1` for local execution with `Az.Accounts` and `Az.ResourceGraph`. The SRE workflow should use the agent's managed identity and built-in read tools; it does not require PowerShell in the SRE sandbox.
