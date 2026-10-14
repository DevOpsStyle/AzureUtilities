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

---

## Azure SRE Agent plugin

This folder is also a standalone Azure SRE Agent plugin. It imports the `azure-cost-increase-assessment` skill, which applies the same pricing rules through the agent's built-in read-only Azure tools and sandboxed code execution. It does not change Azure resources and does not require PowerShell in the SRE sandbox.

### SRE prerequisites

- An Azure SRE Agent in **Running** state
- **SRE Agent Author** or **Administrator** role on the agent
- The **Plugins** page under **Builder**
- **Reader** and **Cost Management Reader** assigned to the SRE Agent managed identity for the subscriptions to assess

### Install from this repository

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
7. Select **Install** and wait for `Plugin installed from URL successfully with 1 skill`.
8. Open **Builder > Skill builder** or **Agent Canvas** and verify that `azure-cost-increase-assessment` is listed.

The plugin appears in the Plugins grid with the **Standalone** badge. Azure SRE Agent pins the installation to the current Git commit, so repository updates are not applied automatically. Update or reinstall the plugin to load a newer version.

### Verify the installation

Start a new agent chat and send:

```text
Assess the impact of the Azure price increases effective 1 February 2027 across all subscriptions you can access. Use the last three complete months and EUR for retail fallback.
```

If the skill reports authorization failures, assign **Reader** and **Cost Management Reader** to the SRE Agent managed identity on the subscriptions to assess, then retry after Azure RBAC propagation completes.

### Common installation errors

| Symptom | Check |
|---|---|
| Plugin or `plugin.json` not found | **Path in repo** must be exactly `utilities/azure-cost-increase-assessment` |
| Repository not found | Use `DevOpsStyle/AzureUtilities`; if the repository is private, provide a PAT with read access |
| Skill does not appear after installation | Refresh the Plugins page, confirm the success message, then check **Skill builder** or **Agent Canvas** |
| The portal shows an unexpected React error | Refresh and check whether the plugin was installed despite the UI error; otherwise retry with the exact field values above |
| Assessment returns authorization errors | Grant the agent managed identity **Reader** and **Cost Management Reader** on the target scope |

The equivalent REST request body is:

```json
{
  "sourceUrl": "DevOpsStyle/AzureUtilities",
  "pathInRepo": "utilities/azure-cost-increase-assessment"
}
```

The skill follows the [Azure SRE Agent plugin format](https://learn.microsoft.com/azure/sre-agent/install-plugin-from-url). The local PowerShell script remains the reference implementation for interactive use outside Azure SRE Agent.

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
