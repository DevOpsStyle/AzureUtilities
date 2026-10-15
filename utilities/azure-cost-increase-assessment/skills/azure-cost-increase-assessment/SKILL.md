---
name: azure-cost-increase-assessment
description: Canonical rules, inventory queries, and CSV format for the Azure compute and storage price increases effective 1 February 2027, plus a bulk full-inventory CSV assessment. Use for an explicit CSV or full inventory export, or when another skill needs the increase rules. For conversational questions about costs or the impact of a price increase (overview first, then a chosen resource), use azure-cost-advisor.
---

# Azure Cost Increase Assessment

## Purpose

Identify affected Azure virtual machines, virtual machine scale sets, managed disks, disk snapshots, storage accounts, Azure NetApp Files capacity pools, and Elastic SAN resources. Estimate the monthly and annual increase by using recent billed cost where possible and public list prices only as a fallback.

This is an estimate, not a bill or commercial quote. Always state that the final impact depends on actual usage, commercial agreements, Reserved Instances, Savings Plans, and future price publication.

## Operating rules

- Use only read operations.
- Use the built-in Azure CLI read tool for Azure Resource Graph requests.
- Obtain billed cost only with the Cost Details tools `AzureCostDetails-Report` and `AzureCostDetails-Read`. The read-only CLI channel rejects Cost Management POST requests: do not retry them through the CLI, never route them through a write tool, and never substitute an estimate silently.
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

Run the four paginated Azure Resource Graph queries in [data acquisition](./references/data-acquisition.md):

1. Virtual machines and virtual machine scale sets
2. Managed disks
3. Disk snapshots
4. Storage accounts, Azure NetApp Files capacity pools, and Elastic SAN

Apply the classifications in [assessment rules](./references/assessment-rules.md). Keep only affected resources. If none are found, report the checked scope and rules, then stop without requesting cost reports.

### 3. Retrieve billed cost

Billed cost comes only from the Cost Details tools, as described in [data acquisition](./references/data-acquisition.md#billed-cost). Follow the cost and calculation steps of the skill `azure-cost-advisor` (`references/overview-workflow.md`, sections 2 and 3):

1. One `AzureCostDetails-Report` per subscription and month, with `metric="ActualCost"`, for the last `Cost months` complete months.
2. `AzureCostDetails-Read` with `view="resources"` for every page of every report.
3. `AmortizedCost` for the same months when reservation or savings-plan usage appears.

Surface any subscription that fails because of permissions or another error; do not treat it as zero cost. Normalize resource IDs to lowercase before joining inventory and cost rows.

### 4. Calculate the baseline

Apply the formulas and commitment handling in [assessment rules](./references/assessment-rules.md):

- exclude nonaffected storage meters from the on-demand baseline
- include Savings Plan amortized cost
- exclude Reserved Instance cost until reservation expiry
- assign a zero baseline when only excluded meters were billed
- use retail list price only for VMs and managed disks with no billed usage, as a hypothetical scenario labelled "hypothetical, not billed"

The Cost Details totals per resource do not split meter subcategories. Storage accounts, Azure NetApp Files, and Elastic SAN are therefore an upper bound in bulk; split their excluded meters with `AzureCostDetails-Read` `view="rows"` for the resource chosen for a deep dive.

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
8. Confirm only `Virtual Machines` and `Storage` meter categories were included.
9. Flag list-price rows, fully Reserved Instance-covered rows, excluded-meter-only rows, unattached disks, and changes between the current SKU and the historical billing meter.
10. Report a currently deallocated VM as a separate running-cost scenario, never as an expected bill while it stays stopped.

### 6. Produce the report

Follow [the output contract](./references/output-contract.md).

Return:

- assessment scope and period
- affected-resource counts by category
- estimated monthly and annual increases by currency
- top affected resources
- data-quality summary showing billed, list-price, fully Reserved Instance-covered, excluded-meter-only, missing-price, and failed-subscription counts
- limitations and the estimate disclaimer
- a UTF-8 CSV attachment when file generation is available

## Failure handling

- If Azure Resource Graph access fails, stop and report that Reader access is required.
- If the Cost Details tools are missing, name them and say they must be created in Agent Canvas; see the plugin README. Do not fall back to a CLI POST or a silent estimate.
- If Cost Details fails for some subscriptions, continue with the successful ones and clearly list the incomplete ones. A retail list-price scenario is allowed only for VMs and managed disks, labelled "hypothetical, not billed".
- If complete billing evidence stays unavailable for all subscriptions, report the limitation and do not present a complete forecast, and do not imply that discounts, Azure Hybrid Benefit, commitments, or actual running hours were considered.
- If the Azure CLI read tool or code execution is unavailable, explain which capability is missing and provide the equivalent local command from the utility documentation. Do not return a success-shaped estimate.

## Related skills

For a conversational analysis (overview first, then a deep dive on a resource the customer chooses), use `azure-cost-advisor`. It applies the rules of this skill. The tool mechanics are in `azure-cost-details`.

## Local reference implementation

The repository also contains `CostIncrease_Assessment_v6.ps1` for local execution with `Az.Accounts` and `Az.ResourceGraph`. It is the reference implementation and the right choice for very large inventories. The SRE workflow uses the agent's managed identity, the Azure CLI read tool for Resource Graph, and the Cost Details tools for billed cost; it does not require PowerShell in the SRE sandbox.
