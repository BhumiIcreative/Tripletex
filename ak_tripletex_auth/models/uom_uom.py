# coding: utf-8
from odoo import fields, models


class ProductUom(models.Model):
    _inherit = 'uom.uom'

    common_code = fields.Many2one(
        comodel_name="uom.common.code",
        string='Common Code',
        help="Tripletex only accepts standardized unit codes"
    )

