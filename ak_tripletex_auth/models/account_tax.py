from odoo import fields, models


class TaxInherit(models.Model):
    _inherit = 'account.tax'
    _description = "tax"

    tripletex_tax_num = fields.Char(
        string="Tripletex Tax Num"
    )
    tripletex_tax = fields.Integer(
        string="Tripletex Tax"
    )
