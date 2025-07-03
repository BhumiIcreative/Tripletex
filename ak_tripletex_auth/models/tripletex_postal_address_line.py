from odoo import fields, models, api


class TripletexPostalAddressLine(models.Model):
    _name = 'postal.address.line'
    _description = "Tripletex Postal Address Line"

    postal_address_id = fields.Many2one(
        'tripletex.consumer',
        string="Postal Address"
    )
    tripletex = fields.Char(
        string="Tripletex"
    )
    street = fields.Char(
        string="Street1"
    )
    street2 = fields.Char(
        string="Street2"
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
