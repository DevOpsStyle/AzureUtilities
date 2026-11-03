# Azure Utilities

A growing collection of practical, reusable utilities for assessing, operating, and troubleshooting Microsoft Azure environments. Each utility lives in its own folder with dedicated prerequisites, usage instructions, examples, and limitations.

> These utilities are provided as assessment aids. Review their output and test them in a suitable environment before using the results for production or commercial decisions.

## Utility catalog

| Utility | Purpose | Azure SRE Agent | Download |
|---|---|---|---|
| [Azure Cost Increase Assessment](./utilities/azure-cost-increase-assessment/) | Inventories affected Azure compute and storage resources and estimates the monthly and annual impact of the price changes effective 1 February 2027. Includes an Azure SRE Agent plugin that gives a short overview first, with billed cost and active reservations and savings plans, and a deep dive on the resource you choose. | [Install from GitHub](./utilities/azure-cost-increase-assessment/#azure-sre-agent-plugin): skills and Python scripts bundled together; no manual tool creation | [Download ZIP](./downloads/Azure-Cost-Increase-Assessment-v6.zip) |

## Quick start

```powershell
git clone https://github.com/DevOpsStyle/AzureUtilities.git
Set-Location .\AzureUtilities\utilities\azure-cost-increase-assessment

Install-Module Az.Accounts, Az.ResourceGraph -Scope CurrentUser
Connect-AzAccount -Tenant <tenant-id-or-domain>

.\CostIncrease_Assessment_v6.ps1 -Currency EUR -CostMonths 3 -OutFile .\price_increase_impact.csv
```

See the [utility documentation](./utilities/azure-cost-increase-assessment/README.md) for permissions, parameters, calculation details, Azure Resource Graph queries, caveats, and the animated demo.

For **Azure SRE Agent**, use **Builder > Plugins > Install from URL** with repository `DevOpsStyle/AzureUtilities` and path `utilities/azure-cost-increase-assessment`. The four skills and the Python scripts they run are installed together, because the scripts live in the skill folders. There is no script to upload, no Agent Canvas Python tool to create, and no Code Access connection: at run time the agent finds the installed scripts and runs them as its own managed identity. The only manual steps are granting the Azure roles and, if they are off, turning on workspace tools; see the [plugin guide](./utilities/azure-cost-increase-assessment/README.md#azure-sre-agent-plugin).

## Repository structure

```text
AzureUtilities/
|-- README.md
|-- downloads/
|   `-- Azure-Cost-Increase-Assessment-v6.zip
`-- utilities/
    `-- azure-cost-increase-assessment/
        |-- CostIncrease_Assessment_v6.ps1
        |-- plugin.json
        |-- README.md
        |-- media/
        |   `-- regional-and-vm-price-increases-demo.gif
        |-- skills/                         <- one self-contained folder per skill
        |   |-- azure-commitments/
        |   |   |-- SKILL.md
        |   |   |-- references/
        |   |   |   `-- AzureCommitments-Guida.md
        |   |   `-- scripts/                <- run by the skill in the agent sandbox
        |   |       |-- AzureCommitments-List.py
        |   |       `-- requirements.txt
        |   |-- azure-cost-advisor/
        |   |   |-- SKILL.md
        |   |   `-- references/
        |   |       |-- deep-dive-playbooks.md
        |   |       |-- overview-workflow.md
        |   |       `-- price-change-profiles.md
        |   |-- azure-cost-details/
        |   |   |-- SKILL.md
        |   |   |-- references/
        |   |   |   `-- AzureCostDetails-Guida.md
        |   |   `-- scripts/                <- run by the skill in the agent sandbox
        |   |       |-- AzureCostDetails-Query.py
        |   |       |-- AzureCostDetails-Read.py
        |   |       |-- AzureCostDetails-Report.py
        |   |       `-- requirements.txt
        |   `-- azure-cost-increase-assessment/
        |       |-- SKILL.md
        |       `-- references/
        `-- tests/
            `-- test_skill_packaging.py     <- offline checks of the plugin and the scripts
```

A skill folder must be self-contained: Azure SRE Agent copies each skill folder of a plugin into the agent sandbox, so a link or a script path outside it does not resolve. Keep the scripts in the `scripts/` folder of the skill that runs them, and run the offline tests (see the plugin guide) before pushing: with automatic updates on, a push reaches the agents that installed the plugin.

Future utilities should be added under `utilities/<utility-name>/` and include a self-contained `README.md`. Add each new utility to the catalog above so users can discover it from the repository home page.

## Permissions and safety

- Use least-privilege Azure roles and review the permissions documented by each utility.
- Inspect scripts before running them against a customer tenant.
- Do not commit customer exports, tenant identifiers, credentials, access tokens, or generated reports.
- Treat cost estimates as indicative and verify final figures against actual Azure billing and applicable commercial agreements.

## Support

Open a GitHub issue with reproducible details if you find a defect or want to propose another utility. Do not include customer-sensitive information in issues or attachments.
