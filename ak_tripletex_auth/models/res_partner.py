from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError


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
        string="Due Date"
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
        # Fetching base_url from configuration parameters
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        # Assuming you need authentication for the country API
        headers = {'Content-Type': 'application/json', "Accept": "application/json"}
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url or not password:
            return None
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
        res = super(ResPartner, self).create(vals_list)
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        password = self.env['tripletex.session.token'].search([], limit=1).token

        if base_url and password:
            for rec in vals_list:
                if not rec.get('trip_customer'):
                    data = {
                        'name': rec['name'],
                        'isSupplier': bool(rec.get('supplier_rank')),
                        'organizationNumber': rec.get('vat') or None,
                        'email': rec.get('email') or None,
                        'overdueNoticeEmail': rec.get('alternate_email') or None,
                        'phoneNumber': rec.get('phone') or None,
                        'phoneNumberMobile': rec.get('mobile') or None,
                        'isPrivateIndividual': False if rec.get('company_type') == 'company' else True,
                        'website': rec.get('website') or None,
                        'invoicesDueIn': rec.get('due_date'),
                        'singleCustomerInvoice': rec.get('is_single_customer_invoice'),
                        'isAutomaticSoftReminderEnabled': rec.get('is_soft_reminder'),
                        'isAutomaticReminderEnabled': rec.get('is_reminder'),
                        'isAutomaticNoticeOfDebtCollectionEnabled': rec.get('is_notice_od_debt'),
                        'discountPercentage': rec.get('discountPercentage'),
                        'invoiceEmail': rec.get('email'),
                        'description': rec.get('comment') or '',
                        'invoiceSendMethod': 'EMAIL',
                        'physicalAddress': {
                            "addressLine1": rec.get("business_street"),
                            "addressLine2": rec.get("business_street2"),
                            "postalCode": rec.get("business_zip"),
                            "city": rec.get("business_city"),
                            "country": {"id": self.get_country_id(
                                self.env['res.country'].browse(rec.get('business_country_id')).code)
                            }
                        },
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

                    print("\n\n>>>>>>>>>>>>>>>>>>>>>>>>>>data",rec['name'])

                    if rec.get('company_type') == 'company':
                        self.create_customer_in_tripletex(data, res)

        return res

    def create_customer_in_tripletex(self, data, partner=None):
        """
        Send prepared data to Tripletex to create a customer.
        If partner is passed, store the Tripletex ID in trip_customer field.
        """
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        token = self.env['tripletex.session.token'].search([], limit=1).token

        if not base_url or not token:
            raise ValidationError("Tripletex configuration missing.")

        try:
            response = requests.post(
                url=f'{base_url}/customer',
                data=json.dumps(data),
                auth=('0', token),
                headers={'Content-Type': 'application/json', "Accept": "application/json"},
            )
        except Exception as e:
            raise ValidationError(_("Tripletex connection error: {}").format(str(e)))

        if response.status_code == 201:
            trip_id = response.json()['value']['id']
            if partner:
                partner.write({'trip_customer': trip_id})
            return trip_id
        else:
            raise ValidationError(_("Error creating customer in Tripletex: {}").format(
                response.text or "Unknown error"))

    def write(self, vals):
        """
        Overrides the write method to update corresponding customer/contact/delivery information in Tripletex
        based on changes made to the partner in Odoo.
        :param vals: Dictionary of values being written to the partner
        :return: True if write succeeds
        :raises ValidationError: If any Tripletex API call fails
        """
        print("\n---part---------write")
        res = super(ResPartner, self).write(vals)
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        headers = {'Content-Type': 'application/json', "Accept": "application/json"}
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url or not password:
            return res
        for rec in self:
            country_code = rec.country_id.code
            name = rec.name
            phone = rec.phone if country_code == 'NO' else None
            mobile = rec.mobile if country_code == 'NO' else None
            street = rec.street or None
            street2 = rec.street2 or None
            zip = rec.zip or None
            city = rec.city or None
            email = rec.email or None
            altr_email = rec.alternate_email or None
            country_id = rec.get_country_id(rec.country_id.code) if rec.country_id else None
            if rec.company_type == 'person' and rec.type == 'contact' and not rec.parent_id.id or None and rec.trip_customer:
                data = {
                    'name': name,
                    'email': email,
                    'overdueNoticeEmail': altr_email,
                    'phoneNumber': phone,
                    'phoneNumberMobile': mobile,
                    'isPrivateIndividual': True,
                    'description': rec.comment or '',
                    'isAutomaticSoftReminderEnabled': rec.is_soft_reminder,
                    'singleCustomerInvoice': rec.is_single_customer_invoice,
                    'isAutomaticReminderEnabled': rec.is_reminder,
                    'isAutomaticNoticeOfDebtCollectionEnabled': rec.is_notice_od_debt,
                    'discountPercentage': rec.discountPercentage,
                    'postalAddress': {
                        'addressLine1': street,
                        'addressLine2': street2,
                        'postalCode': zip,
                        'city': city,
                        'country': {
                            'id': country_id
                        }
                    },
                    'physicalAddress': {
                        'addressLine1': rec.business_street,
                        "addressLine2": rec.business_street2,
                        "postalCode": rec.business_zip,
                        "city": rec.business_city,
                        "country": {
                            "id": rec.get_country_id(rec.business_country_id.code) if rec.business_country_id else None
                        }
                    },
                }
                json_data = json.dumps(data)
                # Try updating in customer API first
                try:
                    response = requests.put(
                        url=f'{base_url}/customer/{rec.customer}',
                        data=json_data,
                        auth=('0', password),
                        headers=headers,
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code != 200:
                    # If update in customer API fails, try updating in contact API
                    response_contact = requests.put(
                        url=f'{base_url}/contact/{rec.trip_customer}',
                        data=json_data,
                        auth=('0', password),
                        headers=headers,
                    )
                    if response_contact.status_code != 200:
                        # Both attempts failed, raise an error
                        error_message = response_contact.text if response_contact.text else "Unknown error"
                        raise ValidationError(_("Error updating contact in Tripletex: {}").format(error_message))
            if rec.company_type == 'company' and rec.trip_customer:
                data = {
                    'name': name,
                    'organizationNumber': rec.vat or None,
                    'email': email,
                    'overdueNoticeEmail': altr_email,
                    'phoneNumber': phone,
                    'phoneNumberMobile': mobile,
                    'isPrivateIndividual': False if rec.company_type == 'company' else True,
                    'invoiceSendMethod': 'EMAIL',
                    'description': rec.comment or '',
                    'singleCustomerInvoice': rec.is_single_customer_invoice,
                    'website': rec.website or None,
                    'isAutomaticSoftReminderEnabled': rec.is_soft_reminder,
                    'isAutomaticReminderEnabled': rec.is_reminder,
                    'isAutomaticNoticeOfDebtCollectionEnabled': rec.is_notice_od_debt,
                    'discountPercentage': rec.discountPercentage,
                    'postalAddress': {
                        'addressLine1': street,
                        'addressLine2': street2,
                        'postalCode': zip,
                        'city': city,
                        'country': {
                            'id': country_id
                        }
                    },
                    'physicalAddress': {
                        'addressLine1': rec.business_street,
                        "addressLine2": rec.business_street2,
                        "postalCode": rec.business_zip,
                        "city": rec.business_city,
                        "country": {
                            "id": rec.get_country_id(rec.business_country_id.code) if rec.business_country_id else None
                        }
                    },
                }
                try:
                    response = requests.put(
                        url=f'{base_url}/customer/{rec.trip_customer}',
                        data=json.dumps(data),
                        auth=('0', password),
                        headers=headers,
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code != 200:
                    error_message = response.text if response.text else "Unknown error"
                    raise ValidationError(_("Error updating customer in Tripletex: {}").format(error_message))
            if rec.type == 'invoice' and rec.parent_id.id or None:
                parent_record = self.env['res.partner'].browse(rec.parent_id.id or None)
                parent_customer_id = parent_record.trip_customer if parent_record else None
                if parent_customer_id:
                    invoice_data = {
                        'invoiceEmail': rec.email or None,
                        # 'invoiceSendMethod': invoice_send_method,
                    }
                    try:
                        response = requests.put(
                            url=f'{base_url}/customer/{parent_customer_id}',
                            data=json.dumps(invoice_data),
                            auth=('0', password),
                            headers=headers,
                        )
                    except Exception as e:
                        raise ValidationError(e)
                    if response.status_code == 200:
                        print("InvoiceEmail updated successfully.")
                    else:
                        error_message = response.text if response.text else "Unknown error"
                        raise ValidationError(_("Error updating InvoiceEmail in Tripletex: {}").format(error_message))
            if rec.type == 'delivery' and rec.trip_customer:
                delivery_address = {
                    'addressLine1': street,
                    'addressLine2': street2,
                    'postalCode': zip,
                    'city': city,
                    'country': {
                        'id': country_id
                    }
                }
                try:
                    response = requests.put(
                        url=f'{base_url}/deliveryAddress/{rec.trip_customer}',
                        data=json.dumps(delivery_address),
                        auth=('0', password),
                        headers=headers,
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code == 200:
                    print("Delivery updated successfully.")
                else:
                    raise ValidationError(_("Error updating delivery in Tripletex: {}").format(
                        response.text if response.text else "Unknown error"))
        return res

    def unlink(self):
        """
        Overrides the unlink method to delete corresponding customer or contact records in Tripletex
        before deleting the partner in Odoo.
        Deletion logic is based on partner type and company type.
        :return: Result of the superclass `unlink` method
        :raises ValidationError: If Tripletex deletion fails or response status is not 204
        """
        print("\n-----------------unlink")
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        password = self.env['tripletex.session.token'].search([], limit=1).token
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
                        headers={'Content-Type': 'application/json', "Accept": "application/json"},
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code != 204:
                    raise ValidationError(_("not deleted"))
        res = super().unlink()
        return res
