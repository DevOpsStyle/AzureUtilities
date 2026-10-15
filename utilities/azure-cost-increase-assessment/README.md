# CostIncrease_Assessment_v6.ps1

`CostIncrease_Assessment_v6.ps1` finds the Azure resources affected by the price changes effective **1 February 2027** and estimates the monthly and annual increase for each one. It writes the result to a single CSV file.

> **Disclaimer.** The output is an **estimate** intended to give an indication of the potential impact. The final impact depends on the actual usage of the resources, on the applicable commercial agreement and on commitments (Reserved Instances, Savings Plans), and must be verified against actual billing. The same disclaimer is written at the top of the CSV file.

The cost baseline comes from **Cost Management**, meaning what was actually billed, with discounts, Azure Hybrid Benefit and real running hours already included. The public list price is used only when a resource has no billed usage in the period analysed.

---

## Demo

[![Animated demo of the Azure Cost Increase Assessment utility](./media/regional-and-vm-price-increases-demo.gif)](./media/regional-and-vm-price-increases-demo.gif)

[Open the demo at full size](./media/regional-and-vm-price-increases-demo.gif).

---

## 1. Prerequisites

| Requirement | Details |
|---|---|
| PowerShell | 7.x recommended; also works with Windows PowerShell 5.1 |
| Modules | `Az.Accounts` and `Az.ResourceGraph` |
| Permissions | **Reader** and **Cost Management Reader** on the subscriptions to analyse |
| Network | HTTPS access to `management.azure.com` and `prices.azure.com` |

```powershell
Install-Module Az.Accounts, Az.ResourceGraph -Scope CurrentUser
```

On Enterprise Agreement enrolments, cost data is visible at subscription level only if *AO view charges* is enabled. If it is disabled, the Cost Management queries fail and the script falls back to list prices.

---

## 2. How to run it

Run the script for the full cost estimate, or the Azure Resource Graph queries in [2.2](#22-quick-inventory-with-azure-resource-graph) for a quick inventory of the affected resources.

### 2.1 Full assessment with the PowerShell script

```powershell
Connect-AzAccount -Tenant <tenant-id-or-domain>
Unblock-File .\CostIncrease_Assessment_v6.ps1      # only if Windows blocks the downloaded file
.\CostIncrease_Assessment_v6.ps1 -Currency EUR -CostMonths 3 -OutFile .\price_increase_impact.csv
```

| Parameter | Default | Description |
|---|---|---|
| `-Currency` | `EUR` | Currency for the list-price fallback. **Set it to the billing currency**, otherwise list-price rows and billed rows are in different currencies |
| `-CostMonths` | `3` | Number of full past months used for the monthly average |
| `-OutFile` | `.\azure_price_increase_impact.csv` | Path of the generated CSV |
| `-NoListPriceFallback` | off | Use billed data only, no list-price estimates |
| `-UseAssumedStorageExclusions` | off | Also excludes legacy *Blob Storage* meters (Hot/Cool/Archive), which are not in the published exclusion list |
| `-NoDisclaimerInCsv` | off | Writes a plain CSV without the disclaimer lines at the top |

The script analyses every subscription in the tenant that the signed-in user can access. With many subscriptions, the Cost Management API may throttle requests: the script waits and retries automatically.

### 2.2 Quick inventory with Azure Resource Graph

The two read-only queries below list the resources that fall under a price increase, with no script and no cost data. They need only the **Reader** role. The script uses extended versions of the same queries, so that it can also estimate the cost.

1. Open [Azure Resource Graph Explorer](https://portal.azure.com/#view/HubsExtension/ArgQueryBlade).
2. To change the scope, select **Directory** and choose the directory, management group, or subscriptions to query.
3. Paste a query and select **Run query**. Select **Download as CSV** to export the result.

Resource Graph returns at most 1,000 records per query by default, and the CSV export is limited to 55,000 records. For a larger estate, query one subscription at a time or use the script, which pages through all results.

#### Virtual machines and Virtual Machine Scale Sets

Lists every VM and scale set under an increase, sorted by percentage: 25% for the v1 and v2 series in any region, the regional percentage for the other series in the 7 regions, and no increase for the Intel v7 series (see [3. Pricing rules applied](#3-pricing-rules-applied)). `instances` is the scale set capacity, or 1 for a single VM.

```kusto
Resources
| where type in~ ("microsoft.compute/virtualmachines", "microsoft.compute/virtualmachinescalesets")
| extend vmSize = iff(type =~ "microsoft.compute/virtualmachines", tostring(properties.hardwareProfile.vmSize), tostring(sku.name))
| extend instances = iff(type =~ "microsoft.compute/virtualmachinescalesets", toint(sku.capacity), 1)
| extend s = tolower(vmSize), region = tolower(location)
| extend isV1 = s matches regex "^standard_(b[0-9]+(ls|s|ms)|d[0-9]+(-[0-9]+)?|ds[0-9]+(-[0-9]+)?|f[0-9]+s?|g[0-9]+|gs[0-9]+(-[0-9]+)?|l[0-9]+s|np[0-9]+s|hc[0-9]+(-[0-9]+)?rs)(_promo)?$"
| extend isV2 = s matches regex "^standard_(a[0-9]+m?|ds?[0-9]+(-[0-9]+)?|f[0-9]+s|l[0-9]+s)_v2(_promo)?$"
| extend isIntelV7 = s matches regex "^standard_(d[0-9]+(-[0-9]+)?(s|ds|ls|lds)|e[0-9]+(-[0-9]+)?(s|ds))_v7$"
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| extend expectedIncreasePct = case(isV1 or isV2, 25, regionalPct > 0 and isIntelV7 == false, regionalPct, 0)
| extend increaseRegime = case(isV1 or isV2, "v1/v2 series increase", expectedIncreasePct > 0, "Regional increase", "Not affected")
| where expectedIncreasePct > 0
| project subscriptionId, resourceGroup, name, type, location, vmSize, instances, increaseRegime, expectedIncreasePct, id
| order by expectedIncreasePct desc
```

#### Managed disks

Lists the managed disks in the 7 regions, where the regional increase applies. A `diskState` of `Unattached` marks a disk that is still billed but not in use.

```kusto
Resources
| where type =~ "microsoft.compute/disks"
| extend region = tolower(location)
| extend regionalPct = case(region == "northeurope", 17, region in ("francecentral", "francesouth"), 11, region in ("westeurope", "norwayeast", "norwaywest"), 9, region == "southeastasia", 8, 0)
| where regionalPct > 0
| project subscriptionId, resourceGroup, name, location, diskSku = tostring(sku.name), diskSizeGB = toint(properties.diskSizeGB), diskState = tostring(properties.diskState), regionalPct, id
```

These queries do not show the billed cost or the monthly and annual impact. The VM list also does not show whether a VM is covered by a Reserved Instance, which is not affected until the reservation expires. Disk snapshots, storage accounts, Azure NetApp Files capacity pools, and Elastic SAN are not included either. The script and the [Azure SRE Agent plugin](#azure-sre-agent-plugin) cover all of these.

---

## Azure SRE Agent plugin

This folder is also a standalone Azure SRE Agent plugin. A customer asks about Azure costs, or about the price increases effective 1 February 2027, in plain language. The agent first gives a short overview ("today the affected resources cost X; after the increase they cost Y", with the per-resource detail in a CSV), asks which resource to look into, and then goes deep on that resource: region or series rule, billed cost, reservations, savings plans, and related disks. Everything is read-only.

Setup has four parts. Do them in this order:

| Step | Part | What it contains | How it gets into the agent |
|---|---|---|---|
| 1 | Permissions | `Reader` and `Cost Management Reader` for the agent's managed identity | Azure role assignments |
| 2 | Python tools | `AzureCostDetails-Report` and `AzureCostDetails-Read` | Created by you in Agent Canvas: Azure SRE Agent cannot import Python tools from `plugin.json` |
| 3 | Skills | `azure-cost-advisor`, `azure-cost-increase-assessment`, `azure-cost-details` | Imported by this plugin |
| 4 | Custom instructions | Routing and guardrails applied to every conversation | Pasted by you in the agent's general custom instructions |

The plugin imports three skills:

| Skill | Purpose |
|---|---|
| `azure-cost-advisor` | The conversation: overview, shortlist, deep dive on the chosen resource, and the CSV |
| `azure-cost-increase-assessment` | The canonical increase rules, inventory queries, CSV format, and the bulk CSV assessment |
| `azure-cost-details` | How to generate and read Cost Details reports with the two Python tools |

### Prerequisites

- An Azure SRE Agent in the **Running** state, and the **SRE Agent Administrator** or **Author** role on it
- The **Plugins** page visible under **Builder**
- Subscriptions on an **Enterprise Agreement (EA)** or a **Microsoft Customer Agreement (MCA)**: the Cost Details API behind the two tools supports only these. On other offers the agent reports the error instead of estimating (see Troubleshooting)
- Permission to create role assignments on the subscriptions to analyse

### Step 1. Grant the agent access

The agent works with its **user-assigned managed identity (UAMI)**. Azure assigns `Reader` only to the resource groups chosen at creation, and no cost role. Add these roles at **subscription** scope, for every subscription to analyse:

| Role | Why |
|---|---|
| `Reader` | Azure Resource Graph returns only what the identity can read. At subscription scope it sees every resource, not only the agent's resource groups |
| `Cost Management Reader` | Required by the two Cost Details tools |
| `Reservations Reader` (optional) | Lets the agent read reservation expiry dates in a deep dive. It can only be assigned at tenant scope, `/providers/Microsoft.Capacity`, by someone with elevated access. Without it the expiry is reported as not verified |

Find the **Object (principal) ID** of the UAMI in the agent portal under **Settings > Azure settings > Go to Identity**, then run:

```bash
az role assignment create --assignee-object-id <uami-object-id> --assignee-principal-type ServicePrincipal --role "Reader" --scope /subscriptions/<subscription-id>
az role assignment create --assignee-object-id <uami-object-id> --assignee-principal-type ServicePrincipal --role "Cost Management Reader" --scope /subscriptions/<subscription-id>
```

Role assignments can take several minutes to propagate. The cost visibility settings of the agreement still apply, for example *AO view charges* on an EA.

### Step 2. Create the two Python tools

Azure SRE Agent does not accept underscores in tool names. Use the hyphenated names exactly: the skills call the tools by these names.

1. Open the agent and go to **Builder > Agent Canvas**, then select **Create > Tool > Python tool**.
2. Set **Tool Name** to `AzureCostDetails-Report` and **Timeout (seconds)** to `900`. Paste [the source](./skills/azure-cost-details/scripts/AzureCostDetails-Report.py) in the **Code** tab, and the description from the [setup guide](./skills/azure-cost-details/references/AzureCostDetails-Guida.md) in **Description**.
3. Open the **Identity** tab and enable **Azure Resource Manager (ARM)**.
4. Open **Test playground** and run the first example of the setup guide with a completed past day. `ready` is a success; `pending` means run it again with the same `resume_state`.
5. Select **Create tool**.
6. Repeat for `AzureCostDetails-Read` ([source](./skills/azure-cost-details/scripts/AzureCostDetails-Read.py)), testing it with the `operation_url` returned by the first tool.

The agent does not read the repository files at run time. When the scripts change, edit each tool, paste the new source, and save.

The tools use the agent's managed identity. They contain no client ID, token, storage key, or SAS. The reader downloads the SAS-protected blobs returned by Azure and never forwards the ARM token to Blob Storage.

### Step 3. Install the plugin

Create the two Python tools first, so the skills can resolve the tool names when they are imported.

The plugin is stored in a subfolder of this repository. Enter the repository and subfolder in two separate fields:

| Azure SRE Agent field | Exact value |
|---|---|
| **GitHub repository URL** | `DevOpsStyle/AzureUtilities` |
| **Path in repo** | `utilities/azure-cost-increase-assessment` |

> **Important:** enter the path exactly as shown. It is relative to the repository root and must not start with `/`, include `plugin.json`, or contain the GitHub repository URL.

1. Open the [Azure SRE Agent portal](https://sre.azure.com) and select the target agent.
2. Confirm that the agent is in the **Running** state.
3. Go to **Builder > Plugins** and select **Install from URL**.
4. Enter `DevOpsStyle/AzureUtilities` in **GitHub repository URL**.
5. Enter `utilities/azure-cost-increase-assessment` in **Path in repo**.
6. Leave credentials empty for this public repository. If it is made private, expand **Advanced** and provide a GitHub personal access token with read access.
7. Select **Install** and wait for `Plugin installed from URL successfully with 3 skills`.
8. Open **Builder > Skill builder** or **Agent Canvas** and verify that `azure-cost-advisor`, `azure-cost-increase-assessment`, and `azure-cost-details` are listed.
9. Open `azure-cost-advisor` and `azure-cost-details` and confirm that `AzureCostDetails-Report` and `AzureCostDetails-Read` are attached to each. If they are not, use **Choose tools** to attach them and save the skill.

The plugin appears in the Plugins grid with the **Standalone** badge. Azure SRE Agent pins the installation to the current Git commit, so repository updates are not applied automatically. Update or reinstall the plugin to load a newer version.

The equivalent REST request body is:

```json
{
  "sourceUrl": "DevOpsStyle/AzureUtilities",
  "pathInRepo": "utilities/azure-cost-increase-assessment"
}
```

### Step 4. Add the custom instructions

The skills do the work. The custom instructions make the agent choose them, apply the same defaults every time, and respect the guardrails. They apply to every conversation, so they are kept short.

1. Open the agent portal and go to the agent's general **Custom instructions**: the instructions of the default agent, not the **Instructions** field of a custom agent. No custom agent is needed.
2. Paste the text below. If the field already contains instructions, add this text after them and keep any rule that does not conflict.
3. Save, then start a **new chat thread** so the change applies.

```text
For Azure cost questions and for the Azure VM and storage price increases effective February 1, 2027, use the azure-cost-advisor skill. It relies on azure-cost-increase-assessment (rules, inventory, CSV format) and azure-cost-details (billed cost). Use azure-cost-details alone for plain billing-detail requests.

Scope: default to the subscription established in the conversation (otherwise discover the accessible subscriptions and ask which to analyze) and the last three complete calendar months. State both explicitly.

Evidence: inventory VMs and separately billed disks, snapshots, and storage with read-only Azure Resource Graph queries. Obtain billed cost only through the AzureCostDetails-Report and AzureCostDetails-Read tools: complete ActualCost for every month, following every report page, plus AmortizedCost for the same months whenever reservation or savings-plan usage appears. If a read-only Cost Management POST is rejected by the CLI read channel, use these tools; do not route it through a write tool or silently substitute an estimate.

Rules: apply the skill's SKU, region, exclusion, and commitment rules without stacking increase percentages. Use public retail prices only where the skill permits a fallback, and label those figures "hypothetical, not billed". Distinguish a currently deallocated VM from historically billed compute: show its running-cost scenario separately, never as an expected bill while it remains stopped. Flag unattached disks, changes between the current SKU and the historical billing meter, missing data, and assumptions about future usage.

Output: give a short overview first (what the affected resources cost today and after the change), then ask which resource to analyze in depth and go deep only on that resource: its region and series rule, reservations, and related resources. Keep long resource lists out of the chat. Verify per-resource and per-currency totals, generate the assessment CSV, and persist it (save the file and confirm it exists) before sharing it.

If complete billing evidence remains unavailable, report the limitation rather than presenting a complete forecast. Use read-only tools for discovery and billing. Never change Azure resources.
```

What each paragraph does:

| Paragraph | Effect |
|---|---|
| Routing | Sends cost and price-increase questions to `azure-cost-advisor`, which uses the other two skills |
| Scope | Fixes the defaults: the subscription of the conversation and the last three complete calendar months, always stated |
| Evidence | Read-only inventory; billed cost only through the two Cost Details tools; `ActualCost` for every month, plus `AmortizedCost` when reservation or savings-plan usage appears; every report page |
| Rules | The rules of the PowerShell script: no stacking, exclusions, retail prices only as labelled hypothetical figures, a separate scenario for deallocated VMs, and flags for unattached disks, resized VMs, missing data, and future-usage assumptions |
| Output | Overview first, then a deep dive on the chosen resource. Long lists go to the CSV, which is verified and saved before it is shared |
| Limits | Never presents incomplete evidence as a complete forecast, and never changes Azure resources |

To analyse a different announcement, change the date and the product names in the first paragraph. The rules come from the skills: for another set of rules the agent asks for the announcement text and confirms the rules with the customer before applying them.

### Step 5. Check these settings

| Setting | Where | Why |
|---|---|---|
| Code Interpreter | **Settings > Basics > Early access to features**, as documented at the time of writing | The agent computes totals and writes the CSV with code. Without code execution there is no CSV download link. Skip this if code execution already works |
| Tool access policy | **Settings > Permissions** | If the agent asks for approval on every call, add an *Allow* rule for `AzureCostDetails-Report`, `AzureCostDetails-Read`, and `RunAzCliReadCommands`. Do not allow `RunAzCliWriteCommands` for these tasks |
| Run mode | **Settings > Basics** | In Review mode only Azure infrastructure operations ask for approval. These skills are read-only, so it does not get in the way |
| Active skills | n/a | An agent keeps at most five skills active at a time. These three leave room for two more |

### Step 6. Verify

Open a **new chat thread** and try these in order.

1. **Overview**

   ```text
   What will the Azure price increases effective 1 February 2027 cost us? Use the subscription <subscription-id>.
   ```

   Expected: the agent states the subscription and the three complete months, requests the Cost Details reports (this can take a few minutes), and answers with a short overview: today, after the change, the monthly and yearly difference, and a table per category. It lists what is not in the totals, gives a CSV download link, and asks which resource to analyse. It does not print a long resource list.

2. **Deep dive**: answer with a number or name from the shortlist. Expected: the verdict on the first line (affected or not, and why: series or region), the billed cost, the reservation and savings-plan status, and the related disks.

3. **Billing rows** (skill `azure-cost-details`), on a completed past day:

   ```text
   Show the ActualCost details by resource for subscription <subscription-id> on 12 October 2026. Include every page and keep currencies separate.
   ```

If the agent skips a skill or a tool, open the tool calls of the thread to see which step failed, and check Troubleshooting.

### Troubleshooting

| Symptom | Check |
|---|---|
| Plugin or `plugin.json` not found | **Path in repo** must be exactly `utilities/azure-cost-increase-assessment` |
| Repository not found | Use `DevOpsStyle/AzureUtilities`; if the repository is private, provide a PAT with read access |
| The portal shows an unexpected React error | Refresh and check whether the plugin was installed despite the UI error; otherwise retry with the exact field values above |
| A skill does not appear after installation | Refresh the Plugins page, confirm the success message, then check **Skill builder** or **Agent Canvas** |
| The agent cannot find `AzureCostDetails-Report` or `AzureCostDetails-Read` | The names must contain hyphens, with no underscores. Check **Capabilities > Tools > Custom tools**, then attach both tools to `azure-cost-advisor` and `azure-cost-details` with **Choose tools** |
| The agent behaves as before an update | The plugin is pinned to a commit: update or reinstall it. The tool code is separate: paste the new source in each tool |
| The agent tries a Cost Management POST through the CLI and it is rejected | Expected: the read-only channel blocks it, and the custom instructions tell the agent to use the Cost Details tools. If it keeps trying, check that the step 4 text is saved and start a new thread |
| Resource Graph shows fewer resources than expected | The managed identity has `Reader` only on some resource groups. Assign `Reader` at subscription scope (step 1) |
| Cost Details returns an authorization error | Assign `Cost Management Reader` on the subscription (step 1), wait for propagation, and check that ARM is enabled in the tool's **Identity** tab |
| Cost Details returns another error for a subscription | The API supports EA and MCA only. The agent reports the error and does not estimate. For other offers use the local script `CostIncrease_Assessment_v6.ps1`, which uses the Cost Management Query API |
| A tool returns `split_required` | The report is too large for one call: the agent asks for smaller, non-overlapping periods |
| The agent says an in-chat calculation is not practical | More than 200 affected resources, or more than 600 cost records in one month. Narrow the scope (subscription, resource group, region, or category) or run the local script |
| No CSV download link | Code execution is off: see step 5 |
| The agent asks for approval on every tool call | Add the *Allow* rules in step 5 |
| Reservation expiry is "not verified" | The identity lacks `Reservations Reader` (optional, step 1). The agent then points to Portal > Reservations |

### What to expect

- Every figure is an **estimate** from past billed cost, assuming constant consumption. It depends on your commercial agreement and on commitments, and must be verified against actual billing.
- Storage accounts, Azure NetApp Files, and Elastic SAN are shown as an **upper bound** in the overview: the excluded meters are split only in the deep dive of the chosen resource.
- A currently deallocated VM is shown as a separate **scenario**, never as an expected bill while it stays stopped.
- A resize (current SKU different from the billed meter) is detected only in the deep dive.
- Reservation expiry dates are read on a best-effort basis (step 1).
- In-chat limits: 200 affected resources per run and 600 cost records per month. The local script has no such limit.

The local PowerShell script remains the reference implementation for interactive use outside Azure SRE Agent. The plugin follows the [Azure SRE Agent plugin format](https://learn.microsoft.com/azure/sre-agent/install-plugin-from-url).

---

## 3. Pricing rules applied

### 3.1 Virtual machines

Each VM gets **one regime only**. The two increases never stack.

| VM series | Increase | Where |
|---|---|---|
| v1: Bv1, D, Ds, F, Fs, G, Gs, L, NP, HCS | **+25%** | All regions |
| v2: Av2, Amv2, Dv2, Dsv2, Fsv2, Lsv2 | **+25%** | All regions |
| Intel v7: Dsv7, Ddsv7, Dlsv7, Dldsv7, Esv7, Edsv7 | **None** | All regions |
| All other VM series (v3–v6, AMD v7, B-series v2, GPU, HPC…) | **Regional %** | The 7 regions below only |

### 3.2 Regional percentages

| Region | Increase |
|---|---|
| North Europe (Dublin) | +17% |
| France Central, France South | +11% |
| West Europe (Amsterdam) | +9% |
| Norway East, Norway West | +9% |
| Southeast Asia (Singapore) | +8% |

### 3.3 Storage

The regional increase also applies to specific Azure Storage services in the 7 regions. Resources analysed: Managed Disks, disk snapshots, Storage Accounts, Azure NetApp Files capacity pools, Elastic SAN.

Billed meters belonging to these excluded services are removed from the cost baseline: Azure Container Storage, Azure Managed Lustre, Azure NetApp Files – Cross Region Replication SKU, Blob Features, Change Feed, General Block Blob, Import/Export, Queues, Queues v2, Standard Page Blob, Storage Actions, Storage Bandwidth, Storage Discovery, Tables, Transaction Optimized SKU. Matching also covers "v2" variants of these names (for example *General Block Blob v2*).

### 3.4 Only VM and Storage charges

Only billed meters in the **Virtual Machines** and **Storage** categories are considered. Other charges billed on the same resource, such as Microsoft Defender for Storage or Defender for Servers, are ignored because they are not affected by the price changes.

### 3.5 Commitments

| Pricing model | Affected from 1 Feb 2027? | How the script treats it |
|---|---|---|
| Pay-as-you-go / EA / MCA on-demand | Yes | Included in the baseline |
| Savings Plan | Yes: covered usage reflects the new underlying rates | Included in the baseline |
| Reserved Instance | No, until the reservation expires | Excluded from the baseline |
| Spot | Not addressed | Not included |

---

## 4. How the cost is calculated

### 4.1 Inventory

Azure Resource Graph is queried for VMs and VM Scale Sets (all regions), and for Managed Disks, snapshots, Storage Accounts, NetApp capacity pools and Elastic SAN in the 7 regions.

### 4.2 Billed cost

For each subscription, the Cost Management Query API is called twice over the last `-CostMonths` full months, filtered on the *Virtual Machines* and *Storage* meter categories:

| Query | Cost type | Grouped by | Used for |
|---|---|---|---|
| A | Actual cost, on-demand only | Resource, meter subcategory | On-demand cost, with excluded storage meters removed |
| B | Amortized cost | Resource, pricing model | Savings Plan cost and RI / SP / on-demand shares |

*Actual cost* shows charges when they are billed. *Amortized cost* spreads upfront commitments (RI, Savings Plans) over the period and assigns them to the resources that used them. Values are divided by `-CostMonths` to get a monthly average.

### 4.3 Choosing the baseline

| Situation | `CostSource` | `BaseMonthlyCost` |
|---|---|---|
| Billed on-demand and/or Savings Plan usage | `Cost Management (OnDemand + Savings Plan)` | Subject on-demand cost + Savings Plan cost |
| Usage fully covered by Reserved Instances | `Cost Management (fully covered by RI)` | 0 until the RI expires |
| Only excluded meters billed | `Cost Management (excluded meters only)` | 0 |
| No billed usage (VM or disk only) | `Retail list price (<currency>)` | List-price estimate |
| No data | `None` | Empty |

$$\text{EstimatedMonthlyIncrease} = \text{BaseMonthlyCost} \times \frac{\text{ExpectedIncreasePct}}{100}$$

$$\text{EstimatedAnnualIncrease} = \text{EstimatedMonthlyIncrease} \times 12$$

### 4.4 List-price fallback

List prices come from the public Azure Retail Prices API, the same source as the Pricing Calculator.

| Resource | Method |
|---|---|
| VMs | Hourly pay-as-you-go price (Spot excluded, Windows/Linux distinguished) × 730 hours × instances. Windows prices include the OS licence |
| Premium SSD, Standard SSD, Standard HDD | Monthly price of the disk tier derived from the disk size |
| Premium SSD v2 | Capacity + IOPS above 3,000 + throughput above 125 MBps, × 730 hours |
| Ultra Disk | Capacity + IOPS + throughput, × 730 hours |
| Snapshots, Storage Accounts, NetApp, Elastic SAN | No fallback: usage-based, billed data only |

---

## 5. Reading the output

The first three lines of the CSV contain the disclaimer, then an empty line, then the table. When importing in Excel, start from **row 5** (the header row), or run the script with `-NoDisclaimerInCsv`.

### 5.1 CSV columns

| Column | Meaning |
|---|---|
| `Category` | Virtual Machine, Managed Disk, Disk Snapshot, Storage Account, Azure NetApp Files, Elastic SAN |
| `SubscriptionId`, `ResourceGroup`, `Name`, `Type`, `Location`, `ResourceId` | Resource identification |
| `Sku` | VM size, or disk/storage SKU and size |
| `Quantity` | 1 for VMs, number of instances for VM Scale Sets |
| `OsType` | Windows / Linux (VMs only) |
| `State` | VM power state (e.g. `PowerState/deallocated`) or disk state (`Attached`, `Reserved` = attached to a stopped VM, `Unattached`) |
| `IncreaseRegime` | `v1/v2 series increase` or `Regional increase` |
| `ExpectedIncreasePct` | 25, 17, 11, 9 or 8 |
| `CostSource` | Where the baseline comes from (see 4.3) |
| `Currency` | Billing currency, or `-Currency` for list-price rows |
| `BaseMonthlyCost` | Monthly cost the increase is applied to |
| `EstimatedMonthlyIncrease` | Estimated monthly increase |
| `EstimatedAnnualIncrease` | Estimated annual increase |
| `ActualOnDemandMonthly` | Billed on-demand VM/Storage cost, monthly average |
| `ActualSubjectMonthly` | Part of the above subject to the increase |
| `SavingsPlanMonthly` | Savings Plan cost, monthly average |
| `AmortizedMonthlyTotal` | Total amortized cost (on-demand + RI + SP) |
| `OnDemandSharePct`, `ReservationSharePct`, `SavingsPlanSharePct` | Share of amortized cost by pricing model |
| `ListPriceMonthly` | List-price estimate, for reference |
| `Notes` | Remarks for that row |

### 5.2 Cases to check

| What you see | What it means |
|---|---|
| `Retail list price` on a deallocated VM | The VM was off for the whole period; the estimate assumes it runs 24/7. Exclude it if it stays off |
| `ReservationSharePct` between 0 and 100 | Part of the usage is covered by an RI and excluded; it will pick up the increase when the RI expires |
| `fully covered by RI` | No increase until the reservation expires: check the expiry date in **Portal → Reservations** |
| All `...SharePct` = 0 except `OnDemandSharePct` = 100 | No RI or Savings Plan coverage in the period |
| VM Scale Set in Flexible orchestration mode | Instances are also listed as individual VMs: ignore the VMSS row to avoid double counting |
| `State = Unattached` on a disk | Disk not attached but still billed |
| `OsType = Windows` on a list-price row | List price includes the Windows licence; with Azure Hybrid Benefit the real cost is lower |

---

## 6. Limitations

- **Past consumption as forecast**, with constant volumes.
- **Cost Management latency** of about 24–48 hours; resources created after the period only get a list-price estimate (VMs and disks) or no estimate.
- **Reservation expiry dates** are not read: a resource fully covered by an RI expiring before 1 February 2027 is shown with no increase.
- **Not included**: Dedicated Hosts, Azure Batch pools, Azure File Sync, Azure Backup, Azure VMware Solution, PaaS services.
- **Future list prices.** If the Retail API already publishes the February 2027 prices, list-price rows may include the increase twice. Check `effectiveStartDate` when that date approaches.
