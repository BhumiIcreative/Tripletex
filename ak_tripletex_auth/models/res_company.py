from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'
    _description = "Company"

    is_tripletex = fields.Boolean(
        default=False,
        string="Tripletex Integration"
    )
