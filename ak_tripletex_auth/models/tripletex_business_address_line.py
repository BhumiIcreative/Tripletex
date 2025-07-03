from odoo import fields, models


class TripletexBusinessAddressLine(models.Model):
    _name = 'business.address.line'
    _description = "Tripletex Business Address Line"

    business_address_id = fields.Many2one(
        'res.partner',
        string="Delivery Address"
    )
    tripletex = fields.Char(
        string="Tripletex"
    )
    street = fields.Char(
        string="Street 1"
    )
    street2 = fields.Char(
        string="Street 2"
    )
    zip = fields.Char(
        string="ZIP Code"
    )
    city = fields.Char(
        string="City"
    )
    country = fields.Char(
        string="Country"
    )
    country_code = fields.Char(
        string="Country Code (Alpha-2)"
    )
