# Price-change profiles

A profile is the set of rules that decides which resources an announcement affects and by how much. Apply only the profile the customer means, and never mix profiles in one answer.

## Profile fields

| Field | Meaning |
|---|---|
| Effective date | When the new prices apply |
| Scope | Services, resource types, and meter categories affected |
| Regimes | Ordered rules, first match wins: condition and percentage |
| Exclusions | Series, regions, or meters that are not affected |
| Commitments | How reservations, savings plans, and Spot are treated |
| Stacking | What happens when two regimes match |
| Baseline | The cost the percentage is applied to |

## Known profile: VM and storage price changes effective 1 February 2027

Source: the Azure notifications on VM lifecycle and on regional pricing updates, as implemented by `CostIncrease_Assessment_v6.ps1`. The authoritative copy of the rules, regular expressions, and retail fallback is the skill `azure-cost-increase-assessment` (`references/assessment-rules.md`). Keep both in sync.

- **Effective date:** 1 February 2027.
- **Scope:** virtual machines and scale sets; managed disks, disk snapshots, storage accounts, Azure NetApp Files capacity pools, and Elastic SAN.
- **Meter categories counted:** only `Virtual Machines` and `Storage`. Other charges on the same resource, such as Microsoft Defender, are ignored.

Regimes for virtual machines. The first match wins and the percentages never stack:

| # | Condition | Increase |
|---|---|---|
| 1 | v1 series (Bv1, D, Ds, F, Fs, G, Gs, L/Ls, NP, HC/HCS) or v2 series (Av2, Amv2, Dv2, Dsv2, Fsv2, Lsv2) | +25% in every region |
| 2 | Intel v7 (Dsv7, Ddsv7, Dlsv7, Dldsv7, Esv7, Edsv7) | None |
| 3 | Any other series (v3 to v6, AMD v7, B-series v2, GPU, HPC) in an affected region | Regional percentage |
| 4 | Anything else | None |

Regional percentages, which also apply to storage:

| Region | Increase |
|---|---|
| `northeurope` | 17% |
| `francecentral`, `francesouth` | 11% |
| `westeurope`, `norwayeast`, `norwaywest` | 9% |
| `southeastasia` | 8% |

Storage services increase by the regional percentage in those regions only. These billed meter subcategories are excluded, case-insensitively and including `v2` variants: Container Storage, Managed Lustre, Cross Region Replication, Blob Features, Change Feed, General Block Blob, Import/Export, Queues, Standard Page Blob, Storage Actions, Bandwidth, Storage Discovery, Tables, Transaction Optimized. Legacy `Blob Storage` meters are excluded only when the customer asks for that assumption.

Commitments:

| Pricing model | Treatment |
|---|---|
| Pay-as-you-go, EA, or MCA on demand | Affected from the effective date |
| Savings plan | Affected from the effective date: covered usage reflects the new underlying rates |
| Reservation | Not affected until the reservation expires. The covered part then becomes pay-as-you-go and is affected |
| Spot | Not addressed by the announcement. Never estimate it |

Baseline: billed on-demand cost of the counted meters, plus savings-plan cost, excluding reservation-covered usage. Estimates assume the historical consumption stays constant.

Not covered: Dedicated Hosts, Azure Batch pools, Azure File Sync, Azure Backup, Azure VMware Solution, and PaaS services.

## New announcement procedure

Use it when the customer cites another announcement, or when the known profile may not fit.

1. **Get the source.** Ask for the link or the pasted text. Never rely on memory for percentages, dates, regions, or series lists. If a web-reading capability is available, read the link; otherwise ask the customer to paste the relevant paragraph.
2. **Extract the profile** into the fields above. Mark every unknown field explicitly and never infer a value.
3. **Confirm.** Show the extracted profile as a compact table with the assumptions, and ask the customer to confirm or correct it. Continue only after the confirmation.
4. **Adapt the pipeline.** Change the type list, `regionalPct`, `expectedIncreasePct`, and `regime` expressions of the exposure query in [overview-workflow](./overview-workflow.md#1-exposure-from-azure-resource-graph), keeping its structure, and validate with the summary tail first. If the profile covers services this skill cannot inventory, say what can and cannot be calculated. Billed cost by service is always available in the Cost Details `service_names`.
5. **Label every answer** in the conversation with the profile name, its source, and its effective date.
