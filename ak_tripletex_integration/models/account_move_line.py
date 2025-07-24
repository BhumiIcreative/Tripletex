from odoo import fields, models, api


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    tripletex_product = fields.Char(
        string="Tripletex Product",
        related="product_id.tripletex_product",
        readonly=True,
    )
    custom_text = fields.Text(
        string="Custom Note",
    )

    @api.onchange("product_id")
    def onchange_product_id(self):
        """
        Onchange handler for `product_id`.
        Automatically sets the `tripletex_product` field with the corresponding
        value from the selected product, if available.
        """
        self.tripletex_product = (
            self.product_id.tripletex_product if self.product_id else False
        )
