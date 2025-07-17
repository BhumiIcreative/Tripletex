from odoo import fields, models, api


class TripletexConsumer(models.Model):
    _name = 'tripletex.consumer'
    _description = "Tripletex consumer"

    tripletex = fields.Char(
        string="Tripletex"
    )
    name = fields.Char(
        string="Name",
        required=True
    )
    organization_no = fields.Char(
        string="Organization No"
    )
    email = fields.Char(
        string="Email"
    )
    phone = fields.Char(
        string="Phone"
    )
    mobile = fields.Char(
        string="Mobile"
    )
    is_company_private = fields.Boolean(
        default=True,
        string="Is Company Private"
    )
    postal_address_ids = fields.One2many(
        'postal.address.line',
        'postal_address_id',
        string="Postal Address"
    )
    delivery_address_ids = fields.One2many(
        'delivery.address.line',
        'delivery_address_id',
        string="Delivery Address"
    )
    business_address_ids = fields.One2many(
        'business.address.line',
        'business_address_id',
        string="Business Address"
    )
