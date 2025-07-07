from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError


class TripletexCustomerDataLoad(models.TransientModel):
    _name = 'tripletex.customer.load'
    _description = "Tripletex Customer Data Load"

    def customer_data_get(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url:
            raise ValidationError(
                _("Error: Base URL not configured. Please set 'ak_tripletex_auth.base_url' in the config parameters."))
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        try:
            response = requests.get(f'{base_url}/customer',
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

    def customer_data_get_from_tripletex(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        headers = {'Content-Type': 'application/json', "Accept": "application/json"}
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url:
            raise ValidationError(
                _("Error: Base URL not configured. Please set 'ak_tripletex_auth.base_url' in the config parameters."))
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        return base_url, headers, password

    def prepare_url_for_data_get(self):
        base_url, headers, password = self.customer_data_get_from_tripletex()
        try:
            initial_response = requests.get(f'{base_url}/customer', headers=headers, auth=(0, password))
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code == 200:
            full_result_length = json.loads(initial_response.text).get('fullResultSize', 0)
            try:
                response = requests.get(f'{base_url}/customer?from=0&count={full_result_length}', headers=headers,
                                        auth=(0, password))
            except Exception as e:
                raise ValidationError(e)
            return response
        return None

    def get_address_vals(self, address_id, prefix=''):
        address = self.fetch_address_data(address_id)
        if not address or not address.get('value'):
            return {}
        val = address['value']
        country = self.get_country_data(val.get('country', {}).get('id')) if val.get('country') else {}
        return {
            f'{prefix}street': val.get('addressLine1'),
            f'{prefix}street2': val.get('addressLine2'),
            f'{prefix}zip': val.get('postalCode', ''),
            f'{prefix}city': val.get('city', ''),
            f'{prefix}country_id': self.env['res.country'].search(
                [('code', '=', country.get('iso_alpha2_code', ''))], limit=1).id if prefix else country.get('id'),
            f'{prefix}country_code': country.get('iso_alpha2_code', '') if prefix else ''
        }

    def import_customer_from_tripletex(self):

        response = self.prepare_url_for_data_get()
        if response.status_code != 200:
            raise ValidationError(_("Failed to fetch data from Tripletex."))

        for data in json.loads(response.text).get('values', []):
            trip_id = data.get('id')
            postal_vals = self.get_address_vals(data.get('postalAddress', {}).get('id') or 0)
            business_vals = self.get_address_vals(data.get('physicalAddress', {}).get('id') or 0, 'business_')

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
                'alternate_email': data.get('overdueNoticeEmail', ''),
                'website': data.get('website', ''),
                'due_date': data.get('invoicesDueIn'),
                'is_single_customer_invoice': data.get('singleCustomerInvoice', False),
                'is_soft_reminder': data.get('isAutomaticSoftReminderEnabled', False),
                'is_reminder': data.get('isAutomaticReminderEnabled', False),
                'is_notice_od_debt': data.get('isAutomaticNoticeOfDebtCollectionEnabled', False),
                'discountPercentage': data.get('discountPercentage') or 0,
                'comment': data.get('description') or '',
                **postal_vals, **business_vals
            }

            customer = self.env['res.partner'].search([('trip_customer', '=', trip_id)], limit=1)
            customer.write(vals) if customer else self.env['res.partner'].create(vals)

    def fetch_address_data(self, address_id):
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        try:
            response = requests.get(
                f"{self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')}/address/{address_id}",
                headers={'Content-Type': 'application/json', "Accept": "application/json"},
                auth=(0, password))

        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            return json.loads(response.text)
        else:
            return None

    def get_country_data(self, country_id):
        if not country_id:
            return None
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))
        try:
            response = requests.get(
                f"{self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')}/country/{country_id}",
                headers={'Content-Type': 'application/json', "Accept": "application/json"},
                auth=(0, password))
        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            country_data = json.loads(response.text)
            return {
                'name': country_data['value'].get('name'),
                'iso_alpha2_code': country_data['value'].get('isoAlpha2Code'),
                'id': self.env['res.country'].search([('code', '=', country_data['value'].get('isoAlpha2Code'))],
                                                     limit=1).id,
            }
        else:
            return {}

    def format_address(self, customer, prefix=''):
        return {
            "addressLine1": getattr(customer, f"{prefix}street", ''),
            "addressLine2": getattr(customer, f"{prefix}street2", ''),
            "postalCode": getattr(customer, f"{prefix}zip", ''),
            "city": getattr(customer, f"{prefix}city", ''),
            "country": {
                "id": self.env['res.partner'].get_country_id(getattr(customer, f"{prefix}country_id").code)
            } if getattr(customer, f"{prefix}country_id") else None
        }

    def import_customer_from_odoo(self):
        response = self.prepare_url_for_data_get()
        external_numbers = {int(d['id']) for d in json.loads(response.text).get('values', []) if
                            d.get('id')} if response.status_code == 200 else set()

        for customer in self.env['res.partner'].search([('trip_customer', '!=', False)]):
            if int(customer.trip_customer) not in external_numbers and customer.customer_rank:
                print("\ncreated customer :::: ", customer.name)
                data = {
                    'name': customer.name,
                    'isSupplier': bool(customer.supplier_rank),
                    'organizationNumber': customer.vat or None,
                    'email': customer.email or None,
                    'overdueNoticeEmail': customer.alternate_email or None,
                    'phoneNumber': customer.phone or None,
                    'phoneNumberMobile': customer.mobile or None,
                    'isPrivateIndividual': customer.company_type != 'company',
                    'website': customer.website or None,
                    'invoicesDueIn': customer.due_date,
                    'singleCustomerInvoice': customer.is_single_customer_invoice,
                    'isAutomaticSoftReminderEnabled': customer.is_soft_reminder,
                    'isAutomaticReminderEnabled': customer.is_reminder,
                    'isAutomaticNoticeOfDebtCollectionEnabled': customer.is_notice_od_debt,
                    'discountPercentage': customer.discountPercentage,
                    'invoiceEmail': customer.email,
                    'description': customer.comment or '',
                    'invoiceSendMethod': 'EMAIL',
                    'physicalAddress': self.format_address(customer, 'business_'),
                    'postalAddress':  self.format_address(customer)
                }
                if customer.company_type == 'company':
                    self.env['res.partner'].create_customer_in_tripletex(data, customer)