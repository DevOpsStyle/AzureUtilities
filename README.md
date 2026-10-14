# Azure Utilities

A growing collection of practical, reusable utilities for assessing, operating, and troubleshooting Microsoft Azure environments. Each utility lives in its own folder with dedicated prerequisites, usage instructions, examples, and limitations.

> These utilities are provided as assessment aids. Review their output and test them in a suitable environment before using the results for production or commercial decisions.

## Utility catalog

| Utility | Purpose | Azure SRE Agent | Download |
|---|---|---|---|
| [Azure Cost Increase Assessment](./utilities/azure-cost-increase-assessment/) | Inventories affected Azure compute and storage resources and estimates the monthly and annual impact of the price changes effective 1 February 2027. | [Install-from-URL instructions](./utilities/azure-cost-increase-assessment/#azure-sre-agent-plugin) | [Download ZIP](./downloads/Azure-Cost-Increase-Assessment-v5.zip) |

## Quick start

```powershell
git clone https://github.com/DevOpsStyle/AzureUtilities.git
Set-Location .\AzureUtilities\utilities\azure-cost-increase-assessment

Install-Module Az.Accounts, Az.ResourceGraph -Scope CurrentUser
Connect-AzAccount -Tenant <tenant-id-or-domain>

.\CostIncrease_Assessment_v5.ps1 -CostMonths 3 -OutFile .\price_increase_impact.csv
```

See the [utility documentation](./utilities/azure-cost-increase-assessment/README.md) for permissions, parameters, calculation details, Azure Resource Graph queries, caveats, and the animated demo.

## Repository structure

```text
AzureUtilities/
|-- README.md
|-- downloads/
|   `-- Azure-Cost-Increase-Assessment-v5.zip
`-- utilities/
    `-- azure-cost-increase-assessment/
        |-- CostIncrease_Assessment_v5.ps1
        |-- plugin.json
        |-- README.md
        |-- media/
        |   `-- regional-and-vm-price-increases-demo.gif
        `-- skills/
            `-- azure-cost-increase-assessment/
                |-- SKILL.md
                `-- references/
```

Future utilities should be added under `utilities/<utility-name>/` and include a self-contained `README.md`. Add each new utility to the catalog above so users can discover it from the repository home page.

## Permissions and safety

- Use least-privilege Azure roles and review the permissions documented by each utility.
- Inspect scripts before running them against a customer tenant.
- Do not commit customer exports, tenant identifiers, credentials, access tokens, or generated reports.
- Treat cost estimates as indicative and verify final figures against actual Azure billing and applicable commercial agreements.

## Support

Open a GitHub issue with reproducible details if you find a defect or want to propose another utility. Do not include customer-sensitive information in issues or attachments.
