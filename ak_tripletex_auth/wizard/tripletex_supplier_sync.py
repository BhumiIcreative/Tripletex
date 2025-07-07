from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError


class TripletexSupplierSync(models.TransientModel):
    _name = 'tripletex.supplier.sync'
    _description = "Tripletex Supplier Sync"

    def supplier_sync(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url:
            raise ValidationError(
                _("Error: Base URL not configured. Please set 'ak_tripletex_auth.base_url' in the config parameters."))
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        try:
            response = requests.get(f'{base_url}/supplier',
                                    headers={'Content-Type': 'application/json', "Accept": "application/json"},
                                    auth=(0, password))
        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            for data in json.loads(response.text)['values']:
                existing_contact = self.env['res.partner'].search([
                    ('name', '=', data.get('name')),
                    '|',
                    ('email', '=', data.get('email')),
                    ('vat', '=', data.get('organizationNumber')),
                    ('company_type', '=', 'personal' if data.get('isPrivateIndividual', False) else 'company'),
                ])
                if existing_contact:
                    existing_contact.write({
                        "trip_customer": data.get('id'),
                    })

    def prepare_url_for_data_get(self):
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        try:
            initial_response = requests.get(f'{base_url}/supplier', headers=headers, auth=(0, password))
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code == 200:
            full_result_length = json.loads(initial_response.text).get('fullResultSize', 0)
            try:
                response = requests.get(f'{base_url}/supplier?from=0&count={full_result_length}', headers=headers,
                                        auth=(0, password))
            except Exception as e:
                raise ValidationError(e)
            return response
        return None

    def import_supplier_from_tripletex(self):
        # self.env['tripletex.customer.load'].import_customer_from_tripletex()
        response = self.prepare_url_for_data_get()
        if response.status_code != 200:
            raise ValidationError(_("Failed to fetch data from Tripletex."))

        for data in json.loads(response.text).get('values', []):
            trip_id = data.get('id')
            postal_vals = self.env['tripletex.customer.load'].get_address_vals(data.get('postalAddress', {}).get('id') or 0)
            business_vals = self.env['tripletex.customer.load'].get_address_vals(data.get('physicalAddress', {}).get('id') or 0, 'business_')

            vals = {
                'name': data.get('name'),
                'email': data.get('email', ''),
                'phone': data.get('phoneNumber', ''),
                'mobile': data.get('phoneNumberMobile', ''),
                'is_company': not data.get('isPrivateIndividual'),
                'company_type': 'company' if not data.get('isPrivateIndividual') else 'person',
                'trip_customer': trip_id,
                'supplier_rank': 1 if data.get('isSupplier') else 0,
                'customer_rank': 1 if data.get('isCustomer') else 0,
                'vat': data.get('organizationNumber') or '',
                'website': data.get('website', ''),
                'comment': data.get('description') or '',
                **postal_vals, **business_vals
            }

            customer = self.env['res.partner'].search([('trip_customer', '=', trip_id)], limit=1)
            customer.write(vals) if customer else self.env['res.partner'].create(vals)

    def import_supplier_from_odoo(self):
        response = self.prepare_url_for_data_get()
        external_numbers = {int(d['id']) for d in json.loads(response.text).get('values', []) if
                            d.get('id')} if response.status_code == 200 else set()

        for supplier in self.env['res.partner'].search([('trip_customer', '!=', False)]):
            if int(supplier.trip_customer) not in external_numbers and supplier.supplier_rank:
                print("\ncreated Supplier :::: ", supplier.name)
                data = {
                    'name': supplier.name,
                    'isSupplier': bool(supplier.supplier_rank),
                    'organizationNumber': supplier.vat or None,
                    'email': supplier.email or None,
                    'overdueNoticeEmail': supplier.alternate_email or None,
                    'phoneNumber': supplier.phone or None,
                    'phoneNumberMobile': supplier.mobile or None,
                    'isPrivateIndividual': supplier.company_type != 'company',
                    'website': supplier.website or None,
                    'description': supplier.comment or '',
                    'physicalAddress': self.env['tripletex.customer.load'].format_address(supplier, 'business_'),
                    'postalAddress': self.env['tripletex.customer.load'].format_address(supplier)
                }
                if supplier.company_type == 'company':
                    self.env['res.partner'].create_customer_in_tripletex(data, supplier)
