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
| 1 | Permissions | `Reader` and `Cost Management Reader` on the subscriptions, and two tenant-level reader roles for reservations and savings plans | Azure role assignments |
| 2 | Python tools | `AzureCostDetails-Report`, `AzureCostDetails-Read`, `AzureCostDetails-Query`, and `AzureCommitments-List` | Created by you in Agent Canvas: Azure SRE Agent cannot import Python tools from `plugin.json` |
| 3 | Skills | `azure-cost-advisor`, `azure-cost-increase-assessment`, `azure-cost-details`, `azure-commitments` | Imported by this plugin |
| 4 | Custom instructions | Routing and guardrails applied to every conversation | Pasted by you in the agent's general custom instructions |

The plugin imports four skills:

| Skill | Purpose |
|---|---|
| `azure-cost-advisor` | The conversation: overview, shortlist, deep dive on the chosen resource, and the CSV |
| `azure-cost-increase-assessment` | The canonical increase rules, inventory queries, CSV format, and the bulk CSV assessment |
| `azure-cost-details` | The billed cost: Cost Details reports with two Python tools, and the Cost Management Query tool as a second source |
| `azure-commitments` | The active reservations and savings plans: scope, term, expiry, and utilization |

### Prerequisites

- An Azure SRE Agent in the **Running** state, and the **SRE Agent Administrator** or **Author** role on it
- The **Plugins** page visible under **Builder**
- Subscriptions on an **Enterprise Agreement (EA)** or a **Microsoft Customer Agreement (MCA)** for the Cost Details tools, which support only these. On other offers, or when Cost Details is rate-limited, the agent uses the Query tool and says so (see "How the tools get the costs")
- Permission to create role assignments on the subscriptions to analyse
- To see reservations and savings plans: someone who can **elevate access** to grant two roles at tenant scope (Step 1). Without them the agent works, but reports the commitment details as not verified

### Step 1. Grant the agent access

The agent works with its **user-assigned managed identity (UAMI)**. Azure assigns `Reader` only to the resource groups chosen at creation, and no cost role. Add these roles:

| Role | Scope | Why |
|---|---|---|
| `Reader` | Subscription | Azure Resource Graph returns only what the identity can read. At subscription scope it sees every resource, not only the agent's resource groups |
| `Cost Management Reader` | Subscription | Required by the three cost tools |
| `Reservations Reader` | Tenant: `/providers/Microsoft.Capacity` | Lets `AzureCommitments-List` read the active reservations. A reservation is a tenant-level resource, so a role on the subscription shows none |
| `Savings plan reader` | Tenant: `/providers/Microsoft.BillingBenefits` | Lets `AzureCommitments-List` read the active savings plans, for the same reason |

Find the **Object (principal) ID** of the UAMI in the agent portal under **Settings > Azure settings > Go to Identity**, then run, for every subscription to analyse:

```bash
az role assignment create --assignee-object-id <uami-object-id> --assignee-principal-type ServicePrincipal --role "Reader" --scope /subscriptions/<subscription-id>
az role assignment create --assignee-object-id <uami-object-id> --assignee-principal-type ServicePrincipal --role "Cost Management Reader" --scope /subscriptions/<subscription-id>
```

The two tenant-level roles are assigned once for the tenant. You need User Access Administrator rights at tenant scope to grant them: a Global Administrator can [elevate access](https://learn.microsoft.com/azure/role-based-access-control/elevate-access-global-admin) for that. Then run:

```powershell
Connect-AzAccount -Tenant <tenant-id>
New-AzRoleAssignment -Scope "/providers/Microsoft.Capacity" -PrincipalId <uami-object-id> -RoleDefinitionName "Reservations Reader"
New-AzRoleAssignment -Scope "/providers/Microsoft.BillingBenefits" -PrincipalId <uami-object-id> -RoleDefinitionName "Savings plan reader"
```

Role assignments can take several minutes to propagate. The cost visibility settings of the agreement still apply, for example *AO view charges* on an EA. Assignments made with PowerShell do not appear in the portal: check them with `Get-AzRoleAssignment`.

### Step 2. Create the four Python tools

Azure SRE Agent does not accept underscores in tool names. Use the hyphenated names exactly: the skills call the tools by these names.

| Tool name | Source | Timeout | Setup guide (description to paste, playground tests) |
|---|---|---|---|
| `AzureCostDetails-Report` | [AzureCostDetails-Report.py](./skills/azure-cost-details/scripts/AzureCostDetails-Report.py) | 900 s | [AzureCostDetails-Guida.md](./skills/azure-cost-details/references/AzureCostDetails-Guida.md) |
| `AzureCostDetails-Read` | [AzureCostDetails-Read.py](./skills/azure-cost-details/scripts/AzureCostDetails-Read.py) | 900 s | [AzureCostDetails-Guida.md](./skills/azure-cost-details/references/AzureCostDetails-Guida.md) |
| `AzureCostDetails-Query` | [AzureCostDetails-Query.py](./skills/azure-cost-details/scripts/AzureCostDetails-Query.py) | 300 s | [AzureCostDetails-Guida.md](./skills/azure-cost-details/references/AzureCostDetails-Guida.md) |
| `AzureCommitments-List` | [AzureCommitments-List.py](./skills/azure-commitments/scripts/AzureCommitments-List.py) | 120 s | [AzureCommitments-Guida.md](./skills/azure-commitments/references/AzureCommitments-Guida.md) |

For each tool:

1. Open the agent and go to **Builder > Agent Canvas**, then select **Create > Tool > Python tool**.
2. Set **Tool Name** and **Timeout (seconds)** as in the table. Paste the source in the **Code** tab, and the description from the setup guide in **Description**.
3. Open the **Identity** tab and enable **Azure Resource Manager (ARM)**.
4. Open **Test playground** and run the example of the setup guide. For `AzureCostDetails-Report`, use a completed past day: `ready` is a success, and `pending` means run it again with the same `resume_state`. Test `AzureCostDetails-Read` with the `operation_url` returned by the first tool.
5. Select **Create tool**.

The agent does not read the repository files at run time. When the scripts change, edit each tool, paste the new source, and save.

The tools use the agent's managed identity. They contain no client ID, token, storage key, or SAS. The reader downloads the SAS-protected blobs returned by Azure and never forwards the ARM token to Blob Storage. All four tools are read-only.

### Step 3. Install the plugin

Create the four Python tools first, so the skills can resolve the tool names when they are imported.

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
7. Select **Install** and wait for `Plugin installed from URL successfully with 4 skills`.
8. Open **Builder > Skill builder** or **Agent Canvas** and verify that `azure-cost-advisor`, `azure-cost-increase-assessment`, `azure-cost-details`, and `azure-commitments` are listed.
9. Confirm that the tools are attached: `azure-cost-details` has `AzureCostDetails-Report`, `AzureCostDetails-Read`, and `AzureCostDetails-Query`; `azure-cost-advisor` has those three and `AzureCommitments-List`; `azure-commitments` has `AzureCommitments-List`. If a tool is not attached, use **Choose tools** to attach it and save the skill.

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
For Azure cost questions and for the Azure VM and storage price increases effective February 1, 2027, use the azure-cost-advisor skill. It relies on azure-cost-increase-assessment (rules, inventory, CSV format), azure-cost-details (billed cost), and azure-commitments (active reservations and savings plans). Use azure-cost-details alone for plain billing-detail requests and azure-commitments alone for questions about reservations and savings plans.

Scope: default to the subscription established in the conversation (otherwise discover the accessible subscriptions and ask which to analyze) and the last complete calendar month. State both explicitly.

Evidence: inventory VMs and separately billed disks, snapshots, and storage with read-only Azure Resource Graph queries. By default obtain billed cost through the AzureCostDetails-Report and AzureCostDetails-Read tools: complete ActualCost for every month of the period, following every report page, plus AmortizedCost for the same months whenever reservation or savings-plan usage appears. Make one attempt with the AzureCostDetails-Query tool when Cost Details is rate-limited (HTTP 429) or not supported for the subscription, or when the customer asks for it, and say which source you used and what it cannot show; if both are unavailable, stop and report the wait. Never repeat a throttled request, route a Cost Management POST through the CLI or a write tool, or silently substitute an estimate. Read the active reservations and savings plans (scope, term, expiry, utilization) with the AzureCommitments-List tool, passing the effective date: an empty or partial result means "not verified", never "none exist".

Rules: apply the skill's SKU, region, exclusion, and commitment rules without stacking increase percentages. Use public retail prices only where the skill permits a fallback, and label those figures "hypothetical, not billed". Distinguish a currently deallocated VM from historically billed compute: show its running-cost scenario separately, never as an expected bill while it remains stopped. Flag unattached disks, changes between the current SKU and the historical billing meter, missing data, and assumptions about future usage.

Output: give a short overview first (what the affected resources cost today and after the change, with a commitments line when reservations or savings plans appear), then ask which resource to analyze in depth and go deep only on that resource: its region and series rule, billed cost, the reservation or savings plan that covers it, and related resources. Keep long resource lists out of the chat. Verify per-resource and per-currency totals, generate the assessment CSV, and persist it (save the file and confirm it exists) before sharing it.

If complete billing evidence remains unavailable, report the limitation rather than presenting a complete forecast. Use read-only tools for discovery and billing. Never change Azure resources.

If the user ask for the increase rule, read the assessment-rules.md
```

What each paragraph does:

| Paragraph | Effect |
|---|---|
| Routing | Sends cost and price-increase questions to `azure-cost-advisor`, which uses the other three skills, and questions about reservations and savings plans to `azure-commitments` |
| Scope | Fixes the defaults: the subscription of the conversation and the last complete calendar month, always stated |
| Evidence | Read-only inventory. Billed cost through the Cost Details tools, with `AzureCostDetails-Query` as a single, labelled second source when they are throttled, not supported, or on request. `AmortizedCost` when reservation or savings-plan usage appears. Active commitments through `AzureCommitments-List`, where an empty result is never read as "none" |
| Rules | The rules of the PowerShell script: no stacking, exclusions, retail prices only as labelled hypothetical figures, a separate scenario for deallocated VMs, and flags for unattached disks, resized VMs, missing data, and future-usage assumptions |
| Output | Overview first, with a commitments line, then a deep dive on the chosen resource. Long lists go to the CSV, which is verified and saved before it is shared |
| Limits | Never presents incomplete evidence as a complete forecast, and never changes Azure resources |

The text sets **one complete month** as the default period, which makes fewer Cost Details requests. Without a period in the instructions, the skills use the last three complete months. For a steadier average write "the last three complete calendar months" in the second paragraph. To analyse a different announcement, change the date and the product names in the first paragraph. The rules come from the skills: for another set of rules the agent asks for the announcement text and confirms the rules with the customer before applying them.

### Step 5. Check these settings

| Setting | Where | Why |
|---|---|---|
| Code Interpreter | **Settings > Basics > Early access to features**, as documented at the time of writing | The agent computes totals and writes the CSV with code. Without code execution there is no CSV download link. Skip this if code execution already works |
| Tool access policy | **Settings > Permissions** | If the agent asks for approval on every call, add an *Allow* rule for `AzureCostDetails-Report`, `AzureCostDetails-Read`, `AzureCostDetails-Query`, `AzureCommitments-List`, and `RunAzCliReadCommands`. Do not allow `RunAzCliWriteCommands` for these tasks |
| Run mode | **Settings > Basics** | In Review mode only Azure infrastructure operations ask for approval. These skills are read-only, so it does not get in the way |
| Active skills | n/a | An agent keeps at most five skills active at a time. These four leave room for one more |

### Step 6. Verify

Open a **new chat thread** and try these in order.

1. **Overview**

   ```text
   What will the Azure price increases effective 1 February 2027 cost us? Use the subscription <subscription-id>.
   ```

   Expected: the agent states the subscription and the period (the last complete month, as set in the instructions), requests the Cost Details report (this can take a few minutes), and answers with a short overview: today, after the change, the monthly and yearly difference, and a table per category. When reservations or savings plans appear it adds a commitments line. It lists what is not in the totals, gives a CSV download link, and asks which resource to analyse. It does not print a long resource list.

2. **Deep dive**: answer with a number or name from the shortlist. Expected: the verdict on the first line (affected or not, and why: series or region), the billed cost, the reservation or savings plan that covers it, with its expiry and utilization, and the related disks.

3. **Billing rows** (skill `azure-cost-details`), on a completed past day:

   ```text
   Show the ActualCost details by resource for subscription <subscription-id> on 12 October 2026. Include every page and keep currencies separate.
   ```

4. **Commitments** (skill `azure-commitments`):

   ```text
   Which reservations and savings plans are active, and which end before 1 February 2027? Use the subscription <subscription-id>.
   ```

   Expected: the counts, a table of at most 10 rows (name, SKU or hourly commitment, region, term, expiry, scope, 30-day utilization), and the ones that end before the date. With the roles of step 1 missing, the agent says that no commitment is visible and names the roles. It does not say that none exist.

5. **Second source** (the Query tool), to see how it behaves without waiting for a 429:

   ```text
   Use the Cost Management Query tool to show the billed cost by resource for September 2026 on subscription <subscription-id>, for Virtual Machines and Storage only.
   ```

   Expected: the agent states that the source is the Cost Management Query API, aggregated by resource, and that it does not show quantities, unit prices, tags, or reservation IDs.

If the agent skips a skill or a tool, open the tool calls of the thread to see which step failed, and check Troubleshooting.

### Troubleshooting

| Symptom | Check |
|---|---|
| Plugin or `plugin.json` not found | **Path in repo** must be exactly `utilities/azure-cost-increase-assessment` |
| Repository not found | Use `DevOpsStyle/AzureUtilities`; if the repository is private, provide a PAT with read access |
| The portal shows an unexpected React error | Refresh and check whether the plugin was installed despite the UI error; otherwise retry with the exact field values above |
| A skill does not appear after installation | Refresh the Plugins page, confirm the success message, then check **Skill builder** or **Agent Canvas** |
| The agent cannot find one of the four tools | The names must contain hyphens, with no underscores. Check **Capabilities > Tools > Custom tools**, then attach the tools to the skills as in step 3, with **Choose tools** |
| The agent behaves as before an update | The plugin is pinned to a commit: update or reinstall it. The tool code is separate: paste the new source in each tool |
| The agent tries a Cost Management POST through the CLI and it is rejected | Expected: the read-only channel blocks it, and the custom instructions tell the agent to use the cost tools. If it keeps trying, check that the step 4 text is saved and start a new thread |
| Resource Graph shows fewer resources than expected | The managed identity has `Reader` only on some resource groups. Assign `Reader` at subscription scope (step 1) |
| A cost tool returns an authorization error | Assign `Cost Management Reader` on the subscription (step 1), wait for propagation, and check that ARM is enabled in the tool's **Identity** tab. Both cost sources need the same role |
| Cost Details returns another error for a subscription | The API supports EA and MCA only. The agent then uses `AzureCostDetails-Query` and says so. If both fail, it reports the errors and does not estimate |
| A tool returns `split_required` | The report is too large for one call: the agent asks for smaller, non-overlapping periods |
| `AzureCostDetails-Report` returns HTTP 429 and a long wait (for example 1 to 2 hours) | Azure Cost Management is rate-limiting on-demand reports. The report was not created, so the blobs and the permissions are not the cause. The tool sends no request before the wait ends. The agent makes one attempt with `AzureCostDetails-Query`. If that is limited too, it gives the exposure overview without amounts and says how long to wait. Then ask the agent to resume **in the same thread**: a new thread has no `resume_state` and sends a new request. See "Avoid the cost rate limits" |
| `AzureCostDetails-Query` returns `retry` with HTTP 429 | The Query API has its own limits. The agent does not repeat it and does not use the CLI. Wait for the time stated and ask again in the same thread |
| `AzureCostDetails-Query` returns `empty` | Cost Management returned no data for that period and filters, for example a period without Virtual Machines or Storage charges, or recent days that have not arrived yet. The agent reports it and never reads it as zero cost. Check the period and the category filter, or try again later |
| The agent says an in-chat calculation is not practical | More than 200 affected resources, or more than 600 cost records (resources) in one month or one Query call. Narrow the scope (subscription, resource group, region, or category) or run the local script |
| No CSV download link | Code execution is off: see step 5 |
| The agent asks for approval on every tool call | Add the *Allow* rules in step 5 |
| The agent says that no reservation or savings plan is visible | The identity lacks `Reservations Reader` or `Savings plan reader` at tenant scope (step 1), or none exist. An empty list is not proof of absence. If the billing data shows `Reservation` or `SavingsPlan` pricing models, the commitments exist and the roles are missing |
| `AzureCommitments-List` returns `forbidden` or `partial` | One of the two APIs is not readable: the result names the role. Assign it (step 1) and wait for propagation |
| `AzureCommitments-List` returns 404 `InvalidResourceType` | The API version is not available in that environment. The tool uses `2022-11-01`, verified for both APIs. The `2026-06-01` shown in the savings plan documentation is not deployed yet |
| The agent used the wrong dates for "last week" or "last month" | Give explicit dates, for example `from 8 to 14 October 2026`. The agent states the first and last day it used, so check them. It may not know today's date |

### How the tools get the costs and the commitments

**Cost Details (default).** Two tools, with the same request as a Logic App HTTP action:

1. `AzureCostDetails-Report` sends `POST /subscriptions/<id>/providers/Microsoft.CostManagement/generateCostDetailsReport?api-version=2023-03-01` with the managed identity and the body `{"metric": ..., "timePeriod": {"start": ..., "end": ...}}`. It then polls the `Location` URL, honoring `Retry-After`, until Azure returns the manifest with the blob links. The Logic App action follows the `Location` URL by itself, which is why its output already contains the manifest.
2. `AzureCostDetails-Read` fetches the manifest again, downloads every CSV blob through its SAS link, aggregates the rows with a temporary database, and returns pages of JSON.

The blob links contain a SAS token, so they are never returned to the agent: the reader takes them from the manifest on every call, and the agent only handles the `operation_url`. Because both tools make the same request as a Logic App, a Logic App gets the same rate limit.

**Query (second source).** `AzureCostDetails-Query` sends `POST /subscriptions/<id>/providers/Microsoft.CostManagement/query?api-version=2023-03-01`, the request that `CostIncrease_Assessment_v6.ps1` uses, with the managed identity. It does not use the `azure-mgmt-costmanagement` library: the request is identical, and the tool avoids a package that the runtime may lack and a retry policy that would sleep silently for the whole `Retry-After`. The agent uses it only when Cost Details is rate-limited or not supported, or when a period needs more than three monthly reports, and always says so.

| | Cost Details (Report and Read) | Query (`AzureCostDetails-Query`) |
|---|---|---|
| Offers | EA and MCA | Any offer that Cost Management supports |
| Cost and pricing models per resource and currency | Yes | Yes: `cost`, `amortized_cost`, `pricing_models` |
| Reservation and savings-plan usage | In the pricing models of the actual-cost rows | Through `amortized_cost` (`metric="both"`) |
| Cost per meter for one resource | Yes, from the rows | Yes, per meter subcategory and pricing model (`group_by="meter"`) |
| Raw rows, quantities, unit prices, tags, `ReservationId`, `BenefitId`, VM size | Yes | No |
| Other meters on the same resource (Defender, licenses, bandwidth) | Included, and flagged | Left out of the cost by the category filter |
| Period per call | One calendar month | Up to 366 days |
| Limit | Rate-limited report generation | Query limits of Cost Management |

The granularity per resource is the same. What the Query path cannot show is the row-level detail and what derives from it, such as the VM size or the reservation ID of a row, and the agent states it in the answer.

**Commitments.** `AzureCommitments-List` sends `GET /providers/Microsoft.Capacity/reservations` and `GET /providers/Microsoft.BillingBenefits/savingsPlans`, both with `api-version=2022-11-01`, and normalizes the result. Reservations and savings plans are tenant-level resources, so the APIs return only what the identity can read: an empty list is not proof that none exist, and the tool says so. Neither API lists the resources a commitment covers: a confirmed link comes from the `ReservationId` or `BenefitId` of the billing rows.

### Avoid the cost rate limits

Azure rate-limits on-demand reports of the Cost Details API (`generateCostDetailsReport`), and the Query API has limits of its own. When a limit is hit, Azure answers HTTP 429 with the time to wait, and the tools pass it on. A 429 from `AzureCostDetails-Report` means that Azure refused to generate the report, so nothing was created and nothing was downloaded. The reader and the Query tool can receive a 429 too, and the agent follows the same rules.

- Microsoft recommends generating a report for the same scope and period at most once per day. Cost data refreshes every four hours, so more frequent requests return the same data and can be throttled. Microsoft also recommends caching cost data on your side, and Cost Management Exports for datasets above about 2 GB per month. These tools implement neither.
- An overview asks for one report per month, and the same number again with `AmortizedCost` when reservations or savings plans appear. A new thread repeats them. Avoid repeating full overviews on the same subscription during testing, and stay in one thread: the agent reuses the reports it has already generated. The default period of one month keeps this to one report.
- For tests, use a narrow period, such as one day or one week, and do not repeat an identical request.
- All the cost tools send a `ClientType` header (`DevOpsStyle-AzureUtilities-CostDetails`) to Azure Resource Manager, and never to Blob Storage. A Microsoft answer on Microsoft Q&A about Cost Management 429 errors says that callers without it share one rate-limit bucket with all anonymous callers, and asks for one stable value. It is a mitigation, not a guarantee: Azure also limits per subscription and per tenant, and a wait of hours is more likely a limit of that kind.
- On a 429 the result includes `rate_limit`: the `Retry-After` and `x-ms-ratelimit-*` headers that Azure sent. The name of the header that carries the wait (`entity`, `tenant`, `client`, or `qpu` in `x-ms-ratelimit-microsoft.costmanagement-<name>-retry-after`) shows which limit was reached. Keep it for support.
- The agent generates one report at a time and stops all Cost Details requests for the subscription at the first 429. It then makes one attempt with the Query tool. In a live test on the same subscription, the Query API answered while Cost Details was blocked for the agent, but that test used another identity: a limit tied to the agent's identity would apply to both. If the Query tool is limited too, the agent gives the exposure overview (what is affected and by which rule) without any amount, and says how long to wait.
- While waiting, do not start new threads on the same subscription. The local script `CostIncrease_Assessment_v6.ps1` runs with your own identity and uses the Query API with its own limits: it may still work, but this is not guaranteed.

### Choose another period

The period is only a default: the one in the custom instructions (the last complete calendar month in step 4), and the last three complete calendar months when neither you nor the instructions name one. Ask for another period in plain language: the agent passes the dates to the tools on every call, and nothing needs to be reinstalled.

| You ask | What the agent does |
|---|---|
| `Show the costs of the last day` | One report for yesterday, in UTC |
| `Use the last 7 days` | The 7 complete days ending yesterday: one report, or two when they cross a month boundary |
| `Use 1 to 15 October 2026` | One report for those dates |
| `Use the last 6 months` | One report for each calendar month |

- **One calendar month per report.** The tool rejects a period that crosses a month boundary, so the agent splits it and combines the results. It states the exact first and last day it used.
- **Recent days can be incomplete.** Costs of the last 24 to 48 hours may still be arriving, so a figure for yesterday can be understated. `empty` means no data yet, not zero cost.
- **The price-increase figures become projections** for a period shorter than a month. The monthly and yearly amounts assume that the observed days repeat all month (a month is counted as 30.4 days) and are labelled as projections, with a note in the CSV. They are sensitive to weekends and one-off charges, so prefer whole months for a figure you want to rely on.
- **Plain cost questions** (no price increase) report the total of the period as it is, without projecting.

### What to expect

- Every figure is an **estimate** from past billed cost, assuming constant consumption. It depends on your commercial agreement and on commitments, and must be verified against actual billing.
- Storage accounts, Azure NetApp Files, and Elastic SAN are shown as an **upper bound** in the overview: the excluded meters are split only in the deep dive of the chosen resource.
- A currently deallocated VM is shown as a separate **scenario**, never as an expected bill while it stays stopped.
- A resize (current SKU different from the billed meter) is detected only in the deep dive.
- Active reservations and savings plans come from `AzureCommitments-List`, which lists only what the identity can read (step 1). When it returns nothing or only part, the agent says the commitments are **not verified** and never that none exist. Which resources a commitment covers is confirmed only by the `ReservationId` or `BenefitId` of the billing rows: the Query source has neither, so there the link stays a candidate.
- When the Query source replaces Cost Details, the totals per resource are the same kind of figure, but the answer says what that source cannot show: raw rows, quantities, unit prices, tags, and the VM size of a row.
- In-chat limits: 200 affected resources per run and 600 cost records (resources) per month or per Query call. The local script has no such limit.

The local PowerShell script remains the reference implementation for interactive use outside Azure SRE Agent. The plugin follows the [Azure SRE Agent plugin format](https://learn.microsoft.com/azure/sre-agent/install-plugin-from-url).

---

## 3. Pricing rules applied

The rules come from two Microsoft notices that take effect on **1 February 2027**:

| Notice | Effect |
|---|---|
| Azure VM lifecycle, retirements and pricing updates (28 September 2026) | About **+25%**, worldwide, for the v1 and v2 VM series |
| Regional pricing update: *Action required: Prepare for specific Azure VM series and Azure Storage service price updates in seven Azure regions globally* | **+8% to +17%** for VMs and Storage in seven Azure regions, except the VM series and Storage services the notice excludes |

The regional notice excludes the v1, v2, and v7 series listed in 3.1, so the two increases never overlap. The notice lists only what is **excluded**. The script and the plugin therefore treat every other VM series, and every other Storage service of the analysed resource types, in the seven regions as affected. The notice does not list the affected ones, so confirm borderline cases with your Microsoft account team.

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

The notice names North Europe *EU North (Dublin)* and West Europe *EU West (Amsterdam)*. In Azure Resource Graph and the Azure CLI they are `northeurope` and `westeurope`.

### 3.3 Storage

The regional increase also applies to specific Azure Storage services in the 7 regions. Resources analysed: Managed Disks, disk snapshots, Storage Accounts, Azure NetApp Files capacity pools, Elastic SAN.

The notice excludes the following Azure Storage services from the regional increase, so their billed meters are removed from the cost baseline: Azure Container Storage, Azure Managed Lustre, Azure NetApp Files – Cross Region Replication SKU, Blob Features, Change Feed, General Block Blob, Import/Export, Queues, Queues v2, Standard Page Blob, Storage Actions, Storage Bandwidth, Storage Discovery, Tables, Transaction Optimized SKU. Matching also covers "v2" variants of these names (for example *General Block Blob v2*).

### 3.4 Only VM and Storage charges

Only billed meters in the **Virtual Machines** and **Storage** categories are considered. Other charges billed on the same resource, such as Microsoft Defender for Storage or Defender for Servers, are ignored because they are not affected by the price changes.

### 3.5 Commitments

| Pricing model | Affected from 1 Feb 2027? | How the script treats it |
|---|---|---|
| Pay-as-you-go / EA / MCA on-demand | Yes | Included in the baseline |
| Savings Plan | Yes: the covered VMs cost more, so the commitment is consumed sooner | Included in the baseline |
| Reserved Instance | No: the price changes when the reservation expires | Excluded from the baseline |
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
