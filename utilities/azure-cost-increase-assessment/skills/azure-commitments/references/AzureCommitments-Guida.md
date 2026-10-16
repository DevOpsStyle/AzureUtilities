# Azure Commitments per Azure SRE Agent

`AzureCommitments-List.py` è un tool Python indipendente da incollare in Agent Canvas, come gli altri. Espone `main(...) -> dict` e restituisce JSON. Elenca le **reservation** e i **savings plan** attivi che l'identità gestita può leggere, così l'agente può rispondere con dati precisi: scope, durata, scadenza, rinnovo, impegno orario dei savings plan e utilizzo.

La panoramica e il deep dive del plugin lo usano per dire quali impegni esistono, quando scadono, se sono ancora attivi alla data dell'aumento e quanto sono utilizzati.

## Inserimento in Agent Canvas

> **Nome del tool.** Azure SRE Agent non accetta l'underscore nei nomi dei tool: usa esattamente `AzureCommitments-List`, con il trattino.

1. Crea un Python tool chiamato esattamente `AzureCommitments-List` e incolla tutto il contenuto di [`AzureCommitments-List.py`](../scripts/AzureCommitments-List.py) nel tab Code.
2. Nel tab Identity abilita **Azure Resource Manager / ARM**. Il timeout predefinito di 120 secondi è sufficiente.
3. Incolla nel campo Description il testo qui sotto.
4. Assegna i due ruoli del paragrafo successivo all'identità del tool.
5. Installa o aggiorna il plugin e verifica che le skill `azure-commitments` e `azure-cost-advisor` abbiano il tool associato. Se non risulta collegato, aggiungilo con **Choose tools**.

Descrizione del tool:

```text
Elenca le reservation e i savings plan Azure attivi che l'identità gestita può leggere. Sono risorse a livello tenant: servono Reservations Reader su /providers/Microsoft.Capacity e Savings plan reader su /providers/Microsoft.BillingBenefits. Sola lettura. Parametri: kind (all, reservations, savings_plans); subscription_id, per indicare se ciascun impegno può applicarsi a quella subscription; effective_date (YYYY-MM-DD), per sapere se è ancora attivo in quella data; filtri reserved_resource_type, location e sku (riguardano solo le reservation: i savings plan non sono legati a tipo, area o taglia e restano nel risultato) e commitment_id; include_inactive; offset e page_size. Restituisce nome, stato, tipo e SKU, area, quantità, durata, scadenza e giorni residui, rinnovo, scope, impegno orario del savings plan e utilizzo a 1, 7 e 30 giorni. Una lista vuota non prova che non esistano impegni: può mancare il permesso. Non elenca le risorse coperte. Risultato completo solo con status=ok e data_complete=true.
```

## Ruoli da assegnare

Reservation e savings plan non appartengono a una subscription: sono risorse del tenant con permessi propri, che **non** si ereditano da `Reader` sulla subscription. All'identità gestita dell'agente servono due ruoli a livello tenant. Il tool funziona anche con uno solo: l'altro elenco risulta `forbidden` o vuoto e il risultato è `partial`.

| Ruolo | Scope |
|---|---|
| `Reservations Reader` | `/providers/Microsoft.Capacity` |
| `Savings plan reader` | `/providers/Microsoft.BillingBenefits` |

Per assegnarli serve il diritto di concedere ruoli a livello tenant: un amministratore deve prima **elevare l'accesso** (ruolo User Access Administrator sul tenant), come descritto nella documentazione Microsoft. L'Object ID dell'identità si trova nel portale dell'agente in **Settings > Azure settings > Go to Identity**.

```powershell
Connect-AzAccount -Tenant <tenant-id>
New-AzRoleAssignment -Scope "/providers/Microsoft.Capacity" -PrincipalId <object-id> -RoleDefinitionName "Reservations Reader"
New-AzRoleAssignment -Scope "/providers/Microsoft.BillingBenefits" -PrincipalId <object-id> -RoleDefinitionName "Savings plan reader"
```

Le assegnazioni fatte con PowerShell non compaiono nel portale: per vederle usa `Get-AzRoleAssignment`. Possono servire alcuni minuti per propagarsi. Un amministratore di fatturazione EA/MCA può invece vedere i propri impegni dal portale con i ruoli di fatturazione, ma quei ruoli non danno accesso all'identità dell'agente.

## Prova nel Test playground

Tutti gli impegni attivi, con la data dell'aumento:

```json
{
  "kind": "all",
  "subscription_id": "<subscription-id>",
  "effective_date": "2027-02-01",
  "reserved_resource_type": "",
  "location": "",
  "sku": "",
  "commitment_id": "",
  "include_inactive": false,
  "offset": 0,
  "page_size": 50
}
```

Atteso: `status=ok` e `data_complete=true`. Se `visible_count` è 0 e `notes` spiega che nessun impegno è visibile, controlla i due ruoli: non significa che non esistano impegni. Un `forbidden` indica ruolo mancante e riporta quale; con `partial` un solo elenco è stato letto.

Solo le reservation di VM in una regione:

```json
{ "kind": "reservations", "reserved_resource_type": "VirtualMachines", "location": "northeurope" }
```

## Come funziona e cosa non fa

- Chiama `GET /providers/Microsoft.Capacity/reservations` e `GET /providers/Microsoft.BillingBenefits/savingsPlans`, entrambe con `api-version=2022-11-01`, seguendo `nextLink` (fino a 10 pagine). Sono verificate su ARM reale. La versione `2026-06-01` che compare nella pagina di documentazione dei savings plan **non è ancora disponibile** in ARM: la chiamata risponde 404 `InvalidResourceType` e indica `2022-11-01` tra le versioni supportate.
- Le API elencano solo ciò che l'identità può leggere. Per questo una lista vuota non prova l'assenza di impegni, e il tool lo dice nelle `notes`.
- `active` vale per stato `Succeeded` e non scaduto. `covers_effective_date` indica se l'impegno è ancora attivo alle 00:00 UTC della data indicata, quindi se la copertura finisce prima dell'aumento.
- `applies_to_subscription` è `yes` per lo scope sulla subscription, `resource_group` per un resource group al suo interno, `possible` per lo scope condiviso (si applica a ogni subscription idonea del billing scope), `unknown` per un management group o quando la risposta non indica nessuno scope, `no` quando lo scope risolto è un'altra subscription. `unknown` significa non verificato, mai "no".
- Lo scope di una reservation a scope singolo arriva in `appliedScopeProperties` (è la forma degli esempi di risposta dell'API 2022-11-01, in cui l'array `appliedScopes` compare solo nelle richieste): il tool legge sia quella sia il vecchio array `appliedScopes`, e `applied_scope_name` riporta il nome visualizzato dello scope.
- I filtri `reserved_resource_type`, `location` e `sku` riguardano solo le reservation. I savings plan non hanno tipo di risorsa, area o taglia (si applicano al calcolo in più aree), quindi con `kind=all` restano nel risultato e una nota lo dichiara; con `kind=reservations` non compaiono.
- Nessuna delle due API elenca le risorse coperte. Il collegamento certo con una risorsa arriva dal `ReservationId` o dal `BenefitId` delle righe di fatturazione; una corrispondenza per tipo, SKU e regione è solo un candidato.
- Il tool legge una pagina ogni 100 impegni. La quota di letture è bassa (l'header `x-ms-ratelimit-remaining-tenant-reads` mostra quante ne restano): l'agente lo chiama una volta e riusa il risultato.
- Non modifica nulla: sono solo richieste GET.

## Verifica

Il tool è stato provato su ARM reale, in sola lettura, nella subscription di test: le tre chiamate rispondono 200 con lista vuota (nessun impegno visibile), e `status`, `sources`, note e `data_complete` risultano corretti. I campi di un elenco popolato sono verificati con risposte simulate costruite sulla forma documentata: paginazione con `nextLink`, scadenze rispetto alla data di efficacia, scope (nella forma `appliedScopeProperties` degli esempi di risposta 2022-11-01 e in quella ad array), filtri, permessi mancanti (403), limiti di frequenza (429, 503), `nextLink` non valido e paginazione senza fine. Per un elenco popolato nel tuo tenant, esegui la prova nel playground dopo aver assegnato i ruoli.

Riferimenti Microsoft:

- [Reservation - List All (REST)](https://learn.microsoft.com/en-us/rest/api/reserved-vm-instances/reservation/list-all)
- [Savings Plan - List All (REST)](https://learn.microsoft.com/en-us/rest/api/billingbenefits/savings-plan/list-all)
- [Permessi per visualizzare e gestire le reservation](https://learn.microsoft.com/en-us/azure/cost-management-billing/reservations/view-reservations)
- [Concedere accesso RBAC alle reservation con PowerShell](https://learn.microsoft.com/en-us/azure/cost-management-billing/reservations/manage-reservations-rbac-powershell)
- [Permessi per visualizzare e gestire i savings plan](https://learn.microsoft.com/en-us/azure/cost-management-billing/savings-plan/permission-view-manage)
- [Elevare l'accesso](https://learn.microsoft.com/en-us/azure/role-based-access-control/elevate-access-global-admin)
