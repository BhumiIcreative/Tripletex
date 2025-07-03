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
                    ('l10n_no_bronnoysund_number', '=', data.get('organizationNumber')),
                    ('company_type', '=', 'personal' if data.get('isPrivateIndividual', False) else 'company'),
                ])
                if existing_contact:
                    existing_contact.write({
                        "trip_customer": data.get('id'),
                    })

    def customer_data_get_from_tripletex(self):
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
            initial_response = requests.get(f'{base_url}/customer', headers=headers, auth=(username, password))
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code == 200:
            full_result_length = json.loads(initial_response.text).get('fullResultSize', 0)
            try:
                response = requests.get(f'{base_url}/customer?from=0&count={full_result_length}', headers=headers,
                                        auth=(username, password))
            except Exception as e:
                raise ValidationError(e)
            return base_url, headers, response
        return None

    def import_customer_from_tripletex(self):
        base_url, headers, response = self.customer_data_get_from_tripletex()
        if response.status_code == 200:
            all_data = json.loads(response.text)
            for data in all_data['values']:
                tripletex_id = data.get('id')
                postal_address_data = data.get('postalAddress', {})
                postal_vals = {}
                if (
                        postal_address_data
                        and postal_address_data.get('id')
                        and (address_data := self.fetch_address_data(postal_address_data['id']))
                        and address_data.get('value')
                ):
                    country_data = address_data['value'].get('country', {})
                    postal_country_data = self.get_country_data(country_data.get('id')) if country_data else {}
                    postal_vals = {
                        'street': address_data['value'].get('addressLine1'),
                        'street2': address_data['value'].get('addressLine2'),
                        'zip': address_data['value'].get('postalCode', ''),
                        'city': address_data['value'].get('city', ''),
                        'country_id': postal_country_data.get('id') if postal_country_data else False,
                    }

                business_address_data = data.get('physicalAddress', {})
                business_vals = {}
                if (
                        business_address_data
                        and business_address_data.get('id')
                        and (address_data := self.fetch_address_data(business_address_data['id']))
                        and address_data.get('value')
                ):
                    country_data = address_data['value'].get('country', {})
                    business_country_data = self.get_country_data(country_data.get('id')) if country_data else {}
                    business_vals = {
                        'business_street': address_data['value'].get('addressLine1'),
                        'business_street2': address_data['value'].get('addressLine2'),
                        'business_zip': address_data['value'].get('postalCode', ''),
                        'business_city': address_data['value'].get('city', ''),
                        'business_country_id': self.env['res.country'].search(
                            [('code', '=', business_country_data.get('iso_alpha2_code', ''))],
                            limit=1).id if business_country_data else False,
                        'business_country_code': business_country_data.get('iso_alpha2_code',
                                                                           '') if business_country_data else '',
                    }

                customer_vals = {
                    'name': data.get('name'),
                    'email': data.get('email', ''),
                    'phone': data.get('phoneNumber', ''),
                    'mobile': data.get('phoneNumberMobile', ''),
                    'is_company': not data.get('isPrivateIndividual'),
                    'company_type': 'company' if not data.get('isPrivateIndividual') else 'person',
                    'trip_customer': tripletex_id,

                    # Additional fields from Tripletex payload
                    'supplier_rank': 1 if data.get('isSupplier') else 0,
                    'vat': data.get('organizationNumber') or '',
                    'alternate_email': data.get('overdueNoticeEmail') or '',
                    'website': data.get('website') or '',
                    'due_date': data.get('invoicesDueIn'),
                    'is_single_customer_invoice': data.get('singleCustomerInvoice', False),
                    'is_soft_reminder': data.get('isAutomaticSoftReminderEnabled', False),
                    'is_reminder': data.get('isAutomaticReminderEnabled', False),
                    'is_notice_od_debt': data.get('isAutomaticNoticeOfDebtCollectionEnabled', False),
                    'discountPercentage': data.get('discountPercentage') or 0,
                    'comment': data.get('description') or '',
                    **postal_vals,
                }

                customer = self.env['res.partner'].search([('trip_customer', '=', tripletex_id)], limit=1)
                print("\n\ncustomer trip_customer::::::",customer.trip_customer)
                if customer:
                    customer.write(customer_vals)
                else:
                    customer = self.env['res.partner'].create(customer_vals)

                physical_address_data = data.get('physicalAddress', {})
                if (
                        physical_address_data
                        and physical_address_data.get('id')
                        and (address_data := self.fetch_address_data(physical_address_data['id']))
                        and address_data.get('value')
                ):
                    country_data = address_data['value'].get('country', {})
                    physical_country_data = self.get_country_data(country_data.get('id')) if country_data else {}

                    physical_address_vals = {
                        'tripletex': address_data['value'].get('id'),
                        'street': address_data['value'].get('addressLine1'),
                        'street2': address_data['value'].get('addressLine2'),
                        'zip': address_data['value'].get('postalCode', ''),
                        'city': address_data['value'].get('city', ''),
                        'country': physical_country_data.get('name') if physical_country_data else '',
                        'country_code': physical_country_data.get('iso_alpha2_code', '') if physical_country_data else '',
                        'business_address_id': customer.id,
                    }

                    business_address = self.env['business.address.line'].search(
                        [('tripletex', '=', physical_address_vals['tripletex'])], limit=1)

                    if business_address:
                        business_address.write(physical_address_vals)
                    else:
                        self.env['business.address.line'].create(physical_address_vals)

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

    def import_customer_from_odoo(self):
        base_url, headers, response = self.customer_data_get_from_tripletex()
        external_numbers = set()
        if response.status_code == 200:
            external_numbers = {int(data['id']) for data in json.loads(response.text).get('values', []) if
                                data.get('id')}
        for customer in self.env['res.partner'].search([('trip_customer', '!=', False)]):
            if int(customer.trip_customer) not in external_numbers:
                print("\ncreated customer :::: ", customer.name)
                data = {
                    'name': customer['name'],
                    'isSupplier': bool(customer.supplier_rank),
                    'organizationNumber': customer.vat or None,
                    'email': customer.email or None,
                    'overdueNoticeEmail': customer.alternate_email or None,
                    'phoneNumber': customer.phone or None,
                    'phoneNumberMobile': customer.mobile or None,
                    'isPrivateIndividual': False if customer.company_type == 'company' else True,
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
                    'physicalAddress': {
                        "addressLine1": customer.business_street,
                        "addressLine2": customer.business_street2,
                        "postalCode": customer.business_zip,
                        "city": customer.business_city,
                        "country": {
                            "id": self.env['res.partner'].get_country_id(customer.business_country_id.code)
                        } if customer.business_country_id else None
                    },
                    "postalAddress": {
                        "addressLine1": customer.street,
                        "addressLine2": customer.street2,
                        "postalCode": customer.zip,
                        "city": customer.city,
                        "country": {
                            "id": self.env['res.partner'].get_country_id(customer.country_id.code)
                        } if customer.country_id else None
                    }
                }
                if customer.company_type == 'company':
                    self.env['res.partner'].create_customer_in_tripletex(data, customer)
