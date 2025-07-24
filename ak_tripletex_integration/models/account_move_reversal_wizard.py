from odoo import fields, models, api
import requests
import logging

_logger = logging.getLogger(__name__)


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    invoice = fields.Integer(string="Invoice")

    @api.model
    def default_get(self, fields_list):
        """
        Override the default_get method to pre-fill certain fields
        based on the context (typically from the active invoice).
        - Sets the 'invoice' field based on the active record.
        - Sets the 'refund_method' to 'cancel' by default.
        """
        res = super().default_get(fields_list)
        active_id = self.env.context.get("active_id")

        if active_id:
            invoice = self.env["account.move"].browse(int(active_id))
            if invoice.exists():
                res.update(
                    {
                        "invoice": invoice.invoice,
                        "refund_method": "cancel",
                    }
                )
        return res

    def reverse_moves(self):
        """
        Override the reverse_moves method to trigger a Tripletex API call
        that creates a credit note in Tripletex for the reversed invoice.
        Requirements:
        - Tripletex base URL and session token must be configured.
        - Executes an HTTP PUT request to create the credit note using invoice ID.
        Returns:
            result: The standard result of the super call to reverse_moves.
        """
        result = super().reverse_moves()

        base_url = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("ak_tripletex_integration.base_url")
        )
        token = self.env["tripletex.session.token"].search([], limit=1).token

        if not base_url or not token:
            return result

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        auth = ("0", token)
        for rec in self.filtered(lambda x: x.invoice):
            try:
                url = f'{base_url}/invoice/{rec.invoice}/:createCreditNote?date={rec.date}&comment={rec.reason or ""}'
                requests.put(url, auth=auth, headers=headers)
            except Exception as e:
                _logger.error("Tripletex credit note error: %s", e)
        return result
