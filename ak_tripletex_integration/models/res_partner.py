from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError
import concurrent.futures


class ResPartner(models.Model):
    _inherit = 'res.partner'

    trip_customer = fields.Char(
        string="Tripletex Id"
    )
    is_hidden = fields.Boolean(
        default=False,
        string="Hidden",
    )
    discountPercentage = fields.Float(
        string="Discount Chargeable Order line"
    )
    alternate_email = fields.Char(
        string="Alternate email"
    )
    due_date = fields.Integer(
        default=14,
        string="Due Days"
    )
    is_notice_od_debt = fields.Boolean(
        string="Notice of debt collection"
    )
    is_reminder = fields.Boolean(
        string="Reminder"
    )
    is_soft_reminder = fields.Boolean(
        string="Soft reminder"
    )
    business_street = fields.Char(string="Street", store=True)
    business_street2 = fields.Char(string="Street", store=True)
    business_city = fields.Char(string="city", store=True)
    business_zip = fields.Char(string="Zip", store=True)
    business_country_id = fields.Many2one(
        'res.country',
        string='Business Country',
        store=True,
        ondelete='restrict'
    )
    business_country_code = fields.Char(string="Country Code", store=True)
    is_single_customer_invoice = fields.Boolean(
        string="Single Customer Invoice",
        help="The following information must be identical on orders/projects that belong to the same invoice:\n"
             "• Customer/Other invoice receiver\n"
             "• Attn.\n"
             "• Currency\n"
             "• Due date\n"
             "• e-Invoice email address\n"
             "• Mobile phone number for SMS notifications"
    )

    def get_country_id(self, iso_alpha2_code):
        """
        Retrieve the Tripletex country ID based on the ISO Alpha-2 code.
        :param iso_alpha2_code: Two-letter ISO country code (e.g., 'NO' for Norway)
        :return: Corresponding country ID from Tripletex, or None if not found or error
        :raises ValidationError: If the Tripletex API call fails
        """
        print("\n---part---------get_country_id")

        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        try:
            response = requests.get(
                url=f'{base_url}/country',
                auth=('0', password),
                headers=headers,
            )
        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            for country_data in response.json().get('values', []):
                if country_data.get('isoAlpha2Code') == iso_alpha2_code:
                    return country_data.get('id')
        else:
            raise ValidationError(_("Error Country Data: {}").format(
                response.text if response.text else "Unknown error"))

    @api.model_create_multi
    def create(self, vals_list):
        """
         Overrides the default create method to automatically create corresponding
         customer records in Tripletex when a new company-type partner is created in Odoo.
         For each new partner in the provided vals_list:
         - If the `trip_customer` field is not set,
         - And the partner is a company (`company_type == 'company'`),
         Then a customer payload is constructed from the partner's values and passed to
         the `create_customer_in_tripletex` method, which handles the API call.
         """
        res = super(ResPartner, self).create(vals_list)
        if self.env.context.get('skip_tripletex_sync'):
            return super().create(vals_list)
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()

        if base_url and password:
            print("\n//////////////////////")
            for rec in vals_list:
                if not rec.get('trip_customer'):
                    data = {
                        'name': rec['name'],
                        'isSupplier': bool(rec.get('supplier_rank')),
                        'isCustomer': bool(rec.get('customer_rank')),
                        'organizationNumber': rec.get('vat') or None,
                        'email': rec.get('email') or None,
                        'overdueNoticeEmail': rec.get('alternate_email') or None,
                        'phoneNumber': rec.get('phone') or None,
                        'phoneNumberMobile': rec.get('mobile') or None,
                        'isPrivateIndividual': False if rec.get('company_type') == 'company' else True,
                        'website': rec.get('website') or None,
                        'invoiceEmail': rec.get('email'),
                        'isInactive': not rec.get('active', True),
                        'description': self.env['product.template'].convert_html_to_normal_text(
                            rec.get('comment') or ''),
                        "postalAddress": {
                            "addressLine1": rec.get('street'),
                            "addressLine2": rec.get('street2'),
                            "postalCode": rec.get('zip'),
                            "city": rec.get('city'),
                            "country": {
                                "id": self.get_country_id(self.env['res.country'].browse(rec.get('country_id')).code)
                            }
                        }
                    }
                    if not data.get('isSupplier'):
                        data.update({
                            # 'invoicesDueIn': rec.get('due_date'),
                            'singleCustomerInvoice': rec.get('is_single_customer_invoice'),
                            'isAutomaticSoftReminderEnabled': rec.get('is_soft_reminder'),
                            'isAutomaticReminderEnabled': rec.get('is_reminder'),
                            'isAutomaticNoticeOfDebtCollectionEnabled': rec.get('is_notice_od_debt'),
                            'discountPercentage': rec.get('discountPercentage'),
                            'physicalAddress': {
                                "addressLine1": rec.get("business_street"),
                                "addressLine2": rec.get("business_street2"),
                                "postalCode": rec.get("business_zip"),
                                "city": rec.get("business_city"),
                                "country": {"id": self.get_country_id(
                                    self.env['res.country'].browse(rec.get('business_country_id')).code)
                                }
                            },
                        })
                    if rec.get('company_type') == 'company':
                        if not rec.get('email'):
                            raise ValidationError(_("Customer '%s' has no email.") % rec['name'])
                        self.create_customer_in_tripletex(data, res)
        return res

    def create_customer_in_tripletex(self, data, partner=None):
        """
        Send prepared data to Tripletex to create a customer.
        If partner is passed, store the Tripletex ID in trip_customer field.
        """
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            raise ValidationError("Tripletex configuration missing.")
        is_customer = data.get('isCustomer') or (partner and partner.customer_rank > 0)
        endpoint = 'customer' if is_customer else 'supplier'
        try:
            response = requests.post(
                url=f'{base_url}/{endpoint}',
                data=json.dumps(data),
                auth=('0', password),
                headers=headers,
            )

        except Exception as e:
            raise ValidationError(_("Tripletex connection error: {}").format(str(e)))

        if response.status_code in [201, 200]:
            trip_id = response.json().get('value', {}).get('id')
            partner.write({'trip_customer': trip_id}) if partner else None
            return trip_id
        else:
            raise ValidationError(_("Error creating customer in Tripletex: {}").format(
                response.text or "Unknown error"))

    def update_data_into_tripletex(self, partner, base_url, headers, auth, endpoint, payload):
        """
        Sends a PUT request to the specified Tripletex API endpoint with the given payload.
        This helper method is used to update customer, contact, invoice, or delivery data
        in Tripletex from Odoo. It handles both HTTP errors and unexpected exceptions,
        and raises a ValidationError if the request fails.
        """
        try:
            response = requests.put(
                url=f"{base_url}{endpoint}",
                data=json.dumps(payload),
                auth=auth,
                headers=headers,
            )
            if response.status_code == 404:
                partner.write({'trip_customer': False})
            elif response.status_code not in [200, 201]:
                raise ValidationError(_(f"Tripletex update error ({endpoint}): {response.text or 'Unknown error'}"))
        except Exception as e:
            raise ValidationError(str(e))

    def write(self, vals):
        """
        Syncs partner data from Odoo to Tripletex upon update.

        This method overrides the standard write() to push updated partner data
        to Tripletex, depending on the partner type (customer, contact, invoice, delivery).
        It uses appropriate API endpoints such as `/customer/`, `/contact/`,
        and `/deliveryAddress/`.

        Raises:
            ValidationError: If any Tripletex API call fails.
        """
        print("\n---part---------write")
        res = super(ResPartner, self).write(vals)
        if self.env.context.get('skip_tripletex_sync'):
            return super().write(vals)
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        auth = ('0', password)
        if not base_url or not password:
            return res
        api_calls = []
        print("\n//////////////////////")
        for rec in self:
            partner = rec.trip_customer
            is_customer = bool(rec.customer_rank)
            is_supplier = bool(rec.supplier_rank)
            if not rec.trip_customer:
                continue
            country_id = rec.get_country_id(rec.country_id.code) if rec.country_id else None
            business_country_id = rec.get_country_id(rec.business_country_id.code) if rec.business_country_id else None

            postal_address = {
                'addressLine1': rec.street or None,
                'addressLine2': rec.street2 or None,
                'postalCode': rec.zip or None,
                'city': rec.city or None,
                'country': {'id': country_id} if country_id else None,
            }
            physical_address = {
                'addressLine1': rec.business_street or None,
                'addressLine2': rec.business_street2 or None,
                'postalCode': rec.business_zip or None,
                'city': rec.business_city or None,
                'country': {'id': business_country_id} if business_country_id else None,
            }
            # Invoice contact update
            if rec.type == 'invoice' and rec.parent_id and rec.parent_id.trip_customer:
                self.update_data_into_tripletex(partner,
                                                base_url, headers, auth,
                                                f'/customer/{rec.parent_id.trip_customer}',
                                                {'invoiceEmail': rec.email or None}
                                                )
                continue

            # Delivery address update
            if rec.type == 'delivery':
                self.update_data_into_tripletex(partner,
                                                base_url, headers, auth,
                                                f'/deliveryAddress/{rec.trip_customer}',
                                                postal_address
                                                )
                continue

            data = {
                'name': rec.name,
                'email': rec.email or None,
                'overdueNoticeEmail': rec.alternate_email or None,
                'phoneNumber': rec.phone or None,
                'phoneNumberMobile': rec.mobile or None,
                'isPrivateIndividual': rec.company_type == 'person',
                'website': rec.website or None,
                'isInactive': not rec.active,
                'invoiceEmail': rec.email or None,
                'description': self.env['product.template'].convert_html_to_normal_text(rec.comment or ''),
            }
            if postal_address:
                data['postalAddress'] = postal_address
            if physical_address:
                data['physicalAddress'] = physical_address
                # Customer fields
            if is_customer:
                data.update({
                    'isCustomer': True,
                    # 'invoicesDueIn': rec.due_date,
                    'singleCustomerInvoice': rec.is_single_customer_invoice,
                    'isAutomaticSoftReminderEnabled': rec.is_soft_reminder,
                    'isAutomaticReminderEnabled': rec.is_reminder,
                    'isAutomaticNoticeOfDebtCollectionEnabled': rec.is_notice_od_debt,
                    'discountPercentage': rec.discountPercentage,
                    'organizationNumber': rec.vat or None,
                })
                endpoint = f'/customer/{rec.trip_customer}'

            elif is_supplier:
                data.update({
                    'isSupplier': True,
                    'organizationNumber': rec.vat or None,
                })
                endpoint = f'/supplier/{rec.trip_customer}'

            if rec.company_type == 'person' and rec.type == 'contact' and not rec.parent_id:
                data.update({'isPrivateIndividual': True})
            elif rec.company_type == 'company':
                data.update({
                    'organizationNumber': rec.vat or None,
                    'website': rec.website,
                    'isPrivateIndividual': False
                })
            else:
                continue

            api_calls.append(self.prepare_api_call(rec, endpoint, data))
            if not self.env.context.get('skip_tripletex_sync') and api_calls:
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [executor.submit(call) for call in api_calls]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            print(f"Tripletex API call failed: {str(e)}")

        return res

    def prepare_api_call(self, partner, endpoint, data):
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        auth = ('0', password)

        def call():
            self.update_data_into_tripletex(partner, base_url, headers, auth, endpoint, data)

        return call

    def unlink(self):
        """
        Overrides the unlink method to delete corresponding customer or contact records in Tripletex
        before deleting the partner in Odoo.
        Deletion logic is based on partner type and company type.
        :return: Result of the superclass `unlink` method
        :raises ValidationError: If Tripletex deletion fails or response status is not 204
        """
        print("\n-----------------unlink")
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            return super().unlink()
        for rec in self:
            url = None
            if rec.company_type == 'company' and rec.trip_customer:
                url = f'{base_url}/customer/{rec.trip_customer}'
            elif rec.company_type == 'person' and rec.trip_customer:
                url = f'{base_url}/contact/list?ids={rec.trip_customer}'
            else:
                if rec.type == 'contact' and rec.trip_customer:
                    url = f'{base_url}/contact/list?ids={rec.trip_customer}'
            if url:
                try:
                    response = requests.delete(
                        url=url,
                        auth=('0', password),
                        headers=headers,
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code != 204:
                    raise ValidationError(_("not deleted"))
        res = super().unlink()
        return res
