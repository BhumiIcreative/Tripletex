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
        response = self.prepare_url_for_data_get()
        external_numbers = set()
        is_supplier = False
        if response.status_code == 200:
            external_numbers = {int(data['id']) for data in json.loads(response.text).get('values', []) if
                                data.get('id')}
        print("\n\naaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        for customer in self.env['res.partner'].search([('trip_customer', '!=', False)]):
            print("\n\nbbbbbbbbbbbbbbbbbbbbbbbbbbb")
            if int(customer.trip_customer) not in external_numbers and data.get('id'):
                print("\n\ncccccccccccccccccccccccccc")
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

    def import_supplier_from_odoo(self):
        print('*****************')
