from odoo import fields, models, api


class TripletexContact(models.Model):
    _name = 'tripletex.contact'
    _rec_name = 'tripletex'
    _description = "tripletex contact"

    tripletex = fields.Char(
        string="Tripletex"
    )
    first_name = fields.Char(
        string="First Name",
        required=True
    )
    last_name = fields.Char(
        string="Last Name"
    )
    email = fields.Char(
        string="Email"
    )
    mobile = fields.Char(
        string="Mobile"
    )
