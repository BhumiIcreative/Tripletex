from odoo import api, fields, models
from odoo.tools import float_compare, float_is_zero


class AccountReconcileModel(models.Model):
    _inherit = "account.reconcile.model"

    perfect_match_enabled = fields.Boolean(
        string="Enable Perfect Match",
        default=False,
        help="When enabled for invoice/bill matching rules, only move lines with"
        " matching payment date and amount (within tolerance) are auto-selected.",
    )
    perfect_match_tolerance = fields.Monetary(
        string="Payment Tolerance",
        currency_field="company_currency_id",
        default=0.0,
        help="Tolerance used to compare statement amount and payment residual amount.",
    )
    perfect_match_use_date = fields.Boolean(
        string="Match on Date",
        default=True,
        help="When enabled, payment/accounting date must equal statement date.",
    )

    @api.model
    def _is_invoice_matching_rule(self, rule):
        """Compatibility helper to identify invoice/bill matching rules.

        Odoo versions can vary on the exact field/value naming used for this rule,
        so we keep a defensive check for common implementations.
        """
        rule = rule if isinstance(rule, models.BaseModel) else self.browse(rule)
        rule_type = (rule.rule_type or "").lower()
        return (
            "invoice" in rule_type
            or "bill" in rule_type
            or rule_type in {"invoice_matching", "match_invoices"}
        )

    def _filter_perfect_match_candidates(self, st_line, aml_candidates):
        """Return only candidates that perfectly match statement date and amount."""
        self.ensure_one()
        if not self.perfect_match_enabled or not aml_candidates:
            return aml_candidates

        tolerance = abs(self.perfect_match_tolerance)
        st_amount = abs(st_line.amount)
        st_date = st_line.date
        currency = st_line.currency_id or st_line.company_currency_id
        rounding = currency.rounding or 0.01

        matched = self.env["account.move.line"]
        for aml in aml_candidates:
            aml_amount = abs(aml.amount_residual_currency or aml.amount_residual)
            amount_ok = (
                float_compare(
                    st_amount,
                    aml_amount,
                    precision_rounding=rounding,
                )
                == 0
                or float_is_zero(
                    abs(st_amount - aml_amount) - tolerance,
                    precision_rounding=rounding,
                )
            )

            date_ok = True
            if self.perfect_match_use_date:
                line_date = aml.date_maturity or aml.date
                date_ok = bool(line_date and st_date and line_date == st_date)

            if amount_ok and date_ok:
                matched |= aml

        return matched

    def _apply_rules(self, st_lines, excluded_ids=None, partner_map=None):
        """Inject perfect-match filtering while preserving base Odoo behavior."""
        results = super()._apply_rules(
            st_lines,
            excluded_ids=excluded_ids,
            partner_map=partner_map,
        )

        if not results:
            return results

        for st_line in st_lines:
            st_result = results.get(st_line.id)
            if not st_result:
                continue

            matched_rule = self.browse(st_result.get("model_id"))
            if not matched_rule or not matched_rule._is_invoice_matching_rule(matched_rule):
                continue
            if not matched_rule.perfect_match_enabled:
                continue

            aml_ids = st_result.get("aml_ids") or []
            aml_candidates = self.env["account.move.line"].browse(aml_ids)
            perfect = matched_rule._filter_perfect_match_candidates(st_line, aml_candidates)
            st_result["aml_ids"] = perfect.ids

        return results
