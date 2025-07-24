from odoo import fields, models


class UomCommonCode(models.Model):
    _name = "uom.common.code"
    _description = "UoM Common Code"

    name = fields.Char(required=True)
