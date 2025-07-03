from odoo import fields, models, api


class TripletexDeliveryAddressLine(models.Model):
    _name = 'delivery.address.line'
    _description = "Tripletex Delivery Address Line"

    delivery_address_id = fields.Many2one(
        'tripletex.consumer',
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
