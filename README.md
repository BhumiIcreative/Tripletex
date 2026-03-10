# Tripletex

## Added: Odoo 19 migration module for Invoice/Bill Perfect-Match reconciliation

This repository now includes an Odoo addon:

- `addons/account_reconcile_perfect_match_v19`

### What it restores

For invoice/bill reconciliation models, it restores Odoo 18-style *perfect match* behavior:

- Auto-matching on exact amount (with configurable tolerance)
- Optional strict date matching between statement line date and payment/invoice line date

### Main technical points

- Adds fields on `account.reconcile.model`:
  - `perfect_match_enabled`
  - `perfect_match_tolerance`
  - `perfect_match_use_date`
- Hooks into reconciliation rule application (`_apply_rules`) and narrows candidates to strict perfect matches.
- Adds form view options on reconciliation models so accounting users can configure the behavior.

### Installation

1. Put the addon on the Odoo addons path.
2. Update app list.
3. Install **Account Reconciliation Perfect Match (v19)**.
4. Open a reconciliation model of type invoice/bill matching and enable **Enable Perfect Match**.

### Notes

Because Odoo 19 internals can vary by patch level, this module uses a compatibility check for invoice/bill matching rule types.
