from odoo import fields, models


class TripletexSessionToken(models.Model):
    _name = "tripletex.session.token"
    _rec_name = "token"
    _description = "Tripletex Session Token"

    token = fields.Char(
        string="Token",
    )
    expiration_date = fields.Date(string="Expiration Date")
