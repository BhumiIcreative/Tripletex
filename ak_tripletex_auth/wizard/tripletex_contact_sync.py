from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError


class TripletexContactSync(models.TransientModel):
    _name = 'tripletex.contact.sync'
    _description = "Tripletex Contact Sync"

    def import_contacts_from_odoo(self):
        print('***************')
    def import_contacts_from_tripletex(self):
        print('***************')
    def contacts_sync(self):
        print('***************')
        
    def contact_data_get_from_tripletex(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        headers = {'Content-Type': 'application/json', "Accept": "application/json"}
        username = 0
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url:
            raise ValidationError(
                _("Error: Base URL not configured. Please set 'ak_tripletex_auth.base_url' in the config parameters."))
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        try:
            initial_response = requests.get(f'{base_url}/contact', headers=headers, auth=(username, password))
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code == 200:
            initial_data = json.loads(initial_response.text)
            full_result_size = initial_data.get('fullResultSize', 0)
            params = {'from': 0, 'count': full_result_size}
            try:
                response = requests.get(f'{base_url}/contact?from=0&count={full_result_size}', headers=headers,
                                        auth=(username, password))
            except Exception as e:
                raise ValidationError(e)
            if response.status_code == 200:
                contact_data = json.loads(response.text)
                self.create_contact(contact_data)

    def create_contact(self, contact_data):
        for data in contact_data['values']:
            tripletex = data.get('id')
            first_name = data.get('firstName', '')
            last_name = data.get('lastName', '')
            email = data.get('email', '')
            mobile = data.get('phoneNumberMobile', '')
            customer = self.env['tripletex.contact'].search([('tripletex', '=', tripletex)])
            if customer:
                customer.write({
                    'first_name': first_name,
                    'email': email,
                    'last_name': last_name,
                    'mobile': mobile,
                })
            else:
                self.env['tripletex.contact'].create({
                    'tripletex': tripletex,
                    'first_name': first_name,
                    'email': email,
                    'last_name': last_name,
                    'mobile': mobile,
                })

    def fetch_delivery_address_data(self, address_id):
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        try:
            response = requests.get(
                f"{self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')}/deliveryAddress/{address_id}",
                headers={'Content-Type': 'application/json', "Accept": "application/json"},
                auth=(0, password))
        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            return json.loads(response.text)
        else:
            return None