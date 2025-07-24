from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    customer_token = fields.Char(
        string="Customer Token",
        config_parameter="ak_tripletex_integration.customer_token",
        help="Enter your Customer Token here.",
    )
    emp_token = fields.Char(
        string="Employee Token",
        config_parameter="ak_tripletex_integration.emp_token",
        help="Enter your Employee Token here.",
    )
    base_url = fields.Char(
        string="Base URL",
        config_parameter="ak_tripletex_integration.base_url",
        help="Enter your base url here.",
    )
    product_sync_date = fields.Datetime(
        string="Last Product Sync Time",
        config_parameter="ak_tripletex_integration.product_sync_date",
    )
