from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError


class TripletexSupplierSync(models.TransientModel):
    _name = 'tripletex.supplier.sync'
    _description = "Tripletex Supplier Sync"

    def supplier_sync(self):
        print('*****************')
    def import_supplier_from_tripletex(self):
        print('*****************')
    def import_supplier_from_odoo(self):
        print('*****************')
