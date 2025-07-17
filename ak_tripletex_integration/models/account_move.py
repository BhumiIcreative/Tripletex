from odoo import fields, models, api, _
import json
import requests
from odoo.exceptions import ValidationError
from datetime import datetime


class AccountMove(models.Model):
    _inherit = 'account.move'

    customer = fields.Char(
        string="Tripletex Customer Id",
        store=True,
    )
    invoice_num = fields.Char(
        string="Tripletex Invoice Number",
        readonly=True
    )
    invoice = fields.Integer(
        string="Tripletex Invoice ID"
    )
    is_hidden = fields.Boolean(
        default=False,
        string="Hidden",
        help="To see tripletex info on it"
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        compute='_compute_sale_order',
        store=True, string='Sale Order',
        help='Reference to the Sale Order associated with this invoice'
    )
    tripletex_comment = fields.Text(
        string="Comment"
    )

    @api.depends('line_ids.sale_line_ids.order_id')
    def _compute_sale_order(self):
        """
        Computes the associated sale order for the move by checking related sale lines.
        Sets the `sale_order_id` to the first matching sale order found in move lines.
        """
        for move in self:
            sale_orders = move.line_ids.mapped('sale_line_ids.order_id')
            move.sale_order_id = sale_orders and sale_orders[0] or False

    @api.onchange('partner_id')
    def onchange_customer_id(self):
        """
        Onchange method for `partner_id`.
        Automatically sets the `customer` field based on the selected partner's customer ID.
        """
        if self.partner_id:
            self.customer = self.partner_id.trip_customer

    def make_tripletex_request(self, url, method='get', data=None):
        """
         Generic method to perform HTTP requests to the Tripletex API.
         :param url: Full Tripletex endpoint URL.
         :param method: HTTP method ('get' or 'post').
         :param data: JSON-serializable data for POST requests.
         :return: `requests.Response` object if the request is successful.
         :raises ValidationError: On network errors or non-200/201 responses.
         """
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            return None
        if method == 'get':
            response = requests.get(url=url, auth=('0', password), headers=headers)
        elif method == 'post':
            response = requests.post(url=url, data=data, auth=('0', password), headers=headers)
        if response.status_code in [200,201]:
            return response
        else:
            raise ValidationError(_("Error making request to Tripletex: {}").format(
                response.text if response.text else "Unknown error"))

    def get_country_id(self, iso_alpha2_code):
        """
        Fetches the Tripletex country ID based on the ISO Alpha-2 country code.
        :param iso_alpha2_code: Two-letter ISO country code.
        :return: Tripletex country ID if found, else None.
        :raises ValidationError: If API request fails.
        """
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            return None
        try:
            response = self.make_tripletex_request(f'{base_url}/country')
        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            for country_data in response.json().get('values', []):
                if country_data.get('isoAlpha2Code') == iso_alpha2_code:
                    return country_data.get('id')
        else:
            raise ValidationError(_("Error getting country data from Tripletex: {}").format(
                response.text if response.text else "Unknown error"))

    def get_tripletex_currency_data(self, currency_name):
        """
        Fetches currency metadata from Tripletex based on currency display name.
        :param currency_name: Currency name (e.g., 'NOK', 'EUR').
        :return: Dictionary of currency fields if found, else None.
        :raises ValidationError: If request fails or currency not found.
        """
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            return None
        try:
            response = self.make_tripletex_request(f'{base_url}/currency')
        except Exception as e:
            raise ValidationError(e)
        if response.status_code == 200:
            for currency in response.json().get('values', []):
                if currency.get('displayName') == currency_name:
                    return {
                        "id": currency.get('id'),
                        "version": currency.get('version'),
                        "description": currency.get('description'),
                        "factor": currency.get('factor'),
                        "displayName": currency.get('displayName'),
                    }
        else:
            raise ValidationError(_("Error getting currency data from Tripletex: {}").format(
                response.text if response.text else "Unknown error"))

    def action_post(self):
        """
        Overrides the `action_post` method to synchronize invoices with Tripletex.
        - Validates no duplicate invoice is being created.
        - Builds the Tripletex invoice payload with customer, delivery, currency, and product data.
        - Makes API call to Tripletex and stores the response invoice number and ID.
        - Also stores delivery address ID to the partner_shipping if not already mapped.
        :return: Result from super().action_post()
        :raises ValidationError: If duplicate invoice or Tripletex request fails.
        """
        result = super().action_post()
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            return result
        for rec in self:
            if rec.journal_id.type == 'sale' and rec.invoice_num:
                raise ValidationError(
                    _("Duplicate Invoice Not Allowed. Please create a Credit Note and Issue a New Invoice."))
            contact_tripletex_id = rec.create_contact_if_needed(base_url)
            delivery_address = rec.get_delivery_address()
            data = {
                'invoiceDate': rec.invoice_date.isoformat() if rec.invoice_date else datetime.today().isoformat(),
                'invoiceDueDate': rec.invoice_date_due.isoformat() if rec.invoice_date_due else datetime.today().isoformat(),
                'kid': rec.payment_reference,
                'comment': rec.tripletex_comment,
                'orders': [{
                    "reference": rec.ref or None,
                    "customer": {
                        "id": rec._create_or_get_customer(base_url, rec._get_customer_data())
                    },
                    "contact": {'id': contact_tripletex_id} if contact_tripletex_id else None,
                    "orderLines": rec._prepare_orderlines(base_url),
                    "currency": rec.get_tripletex_currency_data(rec.currency_id.name),
                    "orderDate": rec.invoice_date.isoformat() if rec.invoice_date else datetime.today().isoformat(),
                    "deliveryDate": rec.delivery_date.isoformat() if rec.delivery_date else datetime.today().isoformat()
                }],
            }
            if delivery_address:
                data['orders'][0]["deliveryAddress"] = delivery_address
            try:
                response = rec.make_tripletex_request(f'{base_url}/invoice', method='post', data=json.dumps(data))
            except Exception as e:
                raise ValidationError(e)
            if response.status_code == 201:
                rec.write({'invoice_num': response.json()['value']['invoiceNumber'],
                           'invoice': response.json()['value']['id']
                           })
                delivery_id = response.json().get('value', {}).get('orders', [{}])[0].get('deliveryAddress',
                                                                                          {}).get('id', None)
                if delivery_id and not rec.partner_shipping_id.trip_customer:
                    rec.partner_shipping_id.write({
                        'trip_customer': delivery_id
                    })
                return result
            else:
                raise ValidationError(
                    _("Error creating invoice in Tripletex: {}").format(
                        response.text if response.text else "Unknown error"))
        return result

    def _get_iso_alpha2_code(self):
        """
        Get the ISO Alpha-2 code of the shipping country.
        :return: ISO code string
        :raises: ValidationError if code is not set.
        """
        country_record = self.env['res.country'].browse(self.partner_shipping_id.country_id.id)
        if not country_record.code:
            raise ValidationError(_("ISO Alpha-2 code not found for the shipping country."))
        return country_record.code

    def _get_customer_data(self):
        """
        Construct the Tripletex-compatible customer dictionary from the partner.
        :return: Dictionary with customer data formatted for Tripletex
        """
        partner = self.env['res.partner'].browse(self.partner_id.id)
        return {
            "name": partner.name,
            "email": partner.email or None,
            'organizationNumber': partner.vat or None,
            "phoneNumberMobile": partner.mobile or None,
            'isPrivateIndividual': True if partner.company_type == 'person' else False,
            'invoiceSendMethod': 'EMAIL',
            'postalAddress': {
                'addressLine1': partner.street,
                'addressLine2': partner.street2 or None,
                'postalCode': partner.zip or None,
                'city': partner.city or None,
                'country': {"id": self.get_country_id(partner.country_id.code)},
            }
        }

    def _create_or_get_customer(self, base_url, customer_data):
        """
        Create or retrieve the Tripletex customer ID for the invoice partner.
        :param base_url: Base Tripletex API URL
        :param customer_data: Payload for customer creation
        :return: Tripletex customer ID
        """
        customer_id = None
        partner = self.partner_id
        if partner.company_type == 'company' and not partner.trip_customer:
            try:
                response = self.make_tripletex_request(f'{base_url}/customer', method='post',
                                                        data=json.dumps(customer_data))
            except Exception as e:
                raise ValidationError(e)
            if response.status_code == 201:
                partner.write({'trip_customer': response.json()['value']['id']})
            else:
                raise ValidationError(_("Error creating customer in Tripletex: {}").format(
                    response.text if response.text else "Unknown error"))
        elif partner.company_type == 'person' and partner.parent_id and not partner.parent_id.trip_customer:
            try:
                response = self.make_tripletex_request(f'{base_url}/customer', method='post',
                                                        data=json.dumps(customer_data))
            except Exception as e:
                raise ValidationError(e)
            if response.status_code == 201:
                partner.parent_id.write({'trip_customer': response.json()['value']['id']})
            else:
                raise ValidationError(_("Error creating customer in Tripletex: {}").format(
                    response.text if response.text else "Unknown error"))
        elif partner.company_type == 'person' and not partner.parent_id and not partner.trip_customer:
            try:
                response = self.make_tripletex_request(f'{base_url}/customer', method='post',
                                                        data=json.dumps(customer_data))
            except Exception as e:
                raise ValidationError(e)
            if response.status_code == 201:
                partner.write({'trip_customer': response.json()['value']['id']})
            else:
                raise ValidationError(_("Error creating customer in Tripletex: {}").format(
                    response.text if response.text else "Unknown error"))
        elif partner.company_type == 'person' and partner.type == 'invoice' and partner.parent_id and partner.parent_id.trip_customer:
            if self.sale_order_id.partner_id.company_type == 'company':
                customer_id = self.sale_order_id.partner_id.trip_customer
            if self.sale_order_id.partner_id.company_type == 'person':
                customer_id = self.sale_order_id.partner_id.parent_id.trip_customer
        elif partner.company_type == 'person' and partner.parent_id and partner.parent_id.trip_customer:
            customer_id = partner.parent_id.trip_customer
        elif partner.company_type == 'person' and partner.trip_customer:
            customer_id = partner.trip_customer
        elif partner.company_type == 'company' and partner.trip_customer:
            customer_id = partner.trip_customer
        return customer_id

    def create_contact_if_needed(self, base_url):
        """
        Create a contact in Tripletex if needed for a person-type partner.
        :param base_url: Base Tripletex API URL
        :return: Tripletex contact ID or None
        """
        print('\n---------create_contact_if_needed')
        partner = self.partner_id
        contact_tripletex_id = None
        if partner.company_type == 'person' and partner.type == 'invoice' and partner.parent_id and partner.parent_id.trip_customer:
            if self.sale_order_id.partner_id.trip_customer and self.sale_order_id.partner_id.company_type == 'person':
                contact_tripletex_id = self.sale_order_id.partner_id.trip_customer
            elif self.sale_order_id.partner_id.trip_customer and self.sale_order_id.partner_id.company_type == 'company':
                contact_tripletex_id = None
            else:
                first_name, last_name = (self.sale_order_id.partner_id.name.split(' ', 1) + [None])[:2] if isinstance(
                    self.sale_order_id.partner_id.name, str) and ' ' in self.sale_order_id.partner_id.name else (
                    self.sale_order_id.partner_id.name, None)
                contact_data = {
                    'firstName': first_name,
                    'lastName': last_name,
                    'email': self.sale_order_id.partner_id.email or None,
                    'phoneNumberMobile': self.sale_order_id.partner_id.phone or None,
                    'trip_customer': {
                        'id': self.sale_order_id.partner_id.parent_id.trip_customer} if self.sale_order_id.partner_id.parent_id.trip_customer else None,
                }
                if contact_data:
                    try:
                        response = self.make_tripletex_request(f'{base_url}/contact', method='post',
                                                                data=json.dumps(contact_data))
                    except Exception as e:
                        raise ValidationError(e)
                    if response.status_code == 201:
                        self.sale_order_id.partner_id.write({'trip_customer': response.json()['value']['id']})
                    else:
                        raise ValidationError(_("Error creating contact in Tripletex: {}").format(
                            response.text if response.text else "Unknown error"))
        if partner.company_type == 'person' and not partner.type == 'invoice' and partner.parent_id and partner.parent_id.trip_customer:
            first_name, last_name = (partner.name.split(' ', 1) + [None])[:2] if isinstance(partner.name,
                                                                                            str) and ' ' in partner.name else (
                partner.name, None)
            contact_data = {
                'firstName': first_name,
                'lastName': last_name,
                'email': partner.email or None,
                'phoneNumberMobile': partner.phone or None,
                'trip_customer': {
                    'id': partner.parent_id.trip_customer} if partner.parent_id.trip_customer else None,
            }
            if contact_data:
                try:
                    response = self.make_tripletex_request(f'{base_url}/contact', method='post',
                                                            data=json.dumps(contact_data))
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code == 201:
                    partner.write({'trip_customer': response.json()['value']['id']})
                else:
                    raise ValidationError(_("Error creating contact in Tripletex: {}").format(
                        response.text if response.text else "Unknown error"))
        return contact_tripletex_id

    def get_delivery_address(self):
        """
        Construct the delivery address data or fetch its Tripletex ID if already synced.
        :return: Dictionary with delivery address or just an ID.
        """
        delivery_address = {}
        if hasattr(self, 'partner_shipping_id') and not self.partner_shipping_id.type == 'delivery':
            return delivery_address
        if hasattr(self,
                   'partner_shipping_id') and self.partner_shipping_id.type == 'delivery' and not self.partner_shipping_id.trip_customer:
            delivery_address = {
                'name': self.partner_shipping_id.name or None,
                'addressLine1': self.partner_shipping_id.street or None,
                'addressLine2': self.partner_shipping_id.street2 or None,
                'postalCode': self.partner_shipping_id.zip or None,
                'city': self.partner_shipping_id.city or None,
                "country": {"id": self.get_country_id(self.partner_shipping_id.country_id.code)}
            }
        elif self.partner_shipping_id.type == 'delivery' and self.partner_shipping_id.trip_customer:
            delivery_address = {'id': self.partner_shipping_id.trip_customer}
        return delivery_address

    # main code
    # def _prepare_orderlines(self, base_url):
    #     """
    #     Build order line data for Tripletex from invoice lines.
    #     Ensures products exist in Tripletex before using their IDs.
    #     :param base_url: Base Tripletex API URL
    #     :return: List of order line dictionaries
    #     """
    #     print('\n---------_prepare_orderlines')
    #     orderlines = []
    #     for line in self.invoice_line_ids:
    #         if line.read()[0].get('display_type') == 'product':
    #             self._create_tripletex_product_if_needed(base_url, line.product_id)
    #             tripletex_tax = line.tax_ids.mapped('tripletex_tax')
    #             orderlines.append({
    #                 "product": {
    #                     "id": line.product_id.tripletex_product,
    #                     "priceExcludingVatCurrency": line['price_unit'],
    #                 },
    #                 "count": line['quantity'],
    #                 "description": f"{line.name}",
    #                 "vatType": {"id": tripletex_tax[0] if tripletex_tax else None},
    #                 "discount": line['discount'],
    #                 "unitPriceExcludingVatCurrency": line['price_unit'],
    #                 "amountExcludingVatCurrency": line['price_subtotal'],
    #             })
    #     return orderlines

    def _prepare_orderlines(self, base_url):
        """
        Build order line data for Tripletex from invoice lines.
        Ensures products exist in Tripletex before using their IDs.
        :param base_url: Base Tripletex API URL
        :return: List of order line dictionaries
        """
        print('\n---------_prepare_orderlines')
        orderlines = []
        for line in self.invoice_line_ids:
            if line.display_type == 'product':
                # Validate product
                if not line.product_id:
                    raise ValidationError(_("Missing product on invoice line: %s") % line.display_name)

                # Validate name (used as description)
                product_name = line.name or line.product_id.name or ''
                if not product_name.strip():
                    raise ValidationError(
                        _("Missing name/description on invoice line for product %s") % line.product_id.display_name)

                # Ensure product exists in Tripletex
                self._create_tripletex_product_if_needed(base_url, line.product_id)

                # Prepare VAT type
                tripletex_tax = line.tax_ids.mapped('tripletex_tax')
                vat_type_id = tripletex_tax[0] if tripletex_tax else None

                # Build order line
                orderlines.append({
                    "product": {
                        "id": line.product_id.tripletex_product,
                        "priceExcludingVatCurrency": line.price_unit,
                    },
                    "count": line.quantity,
                    "description": product_name,
                    "vatType": {"id": vat_type_id} if vat_type_id else None,
                    "discount": line.discount,
                    "unitPriceExcludingVatCurrency": line.price_unit,
                    "amountExcludingVatCurrency": line.price_subtotal,
                })
        return orderlines

    # def _prepare_orderlines(self, base_url):
    #     orderlines = []
    #     for line in self.invoice_line_ids:
    #         if line.read()[0].display_type == 'product':
    #             self._create_tripletex_product_if_needed(base_url, line.product_id)
    #             tripletex_tax = line.tax_ids.mapped('tripletex_tax')
    #             orderlines.append({
    #                 "product": {
    #                     "id": line.product_id.tripletex_product,
    #                     "priceExcludingVatCurrency": line['price_unit'],
    #                 },
    #                 "count": line['quantity'],
    #                 "description": line.name,
    #                 "vatType": {"id": tripletex_tax[0] if tripletex_tax else None},
    #                 "discount": line['discount'],
    #                 "unitPriceExcludingVatCurrency": line['price_unit'],
    #                 "amountExcludingVatCurrency": line['price_subtotal'],
    #             })
    #         elif line.display_type == 'line_section':
    #             line_section_value = self._get_line_section_value(line)
    #             order_group_id = self._create_order_group(base_url, line_section_value)
    #             orderlines.append({
    #                 "orderGroup": {
    #                     'id': order_group_id,
    #                 }
    #             })
    #     return orderlines

    def _get_line_section_value(self, line):
        print('\n---------_get_line_section_value')
        return line.name

    # def _create_order_group(self, base_url, title):
    #    # Define the payload for creating the order group
    #    payload = {
    #        'title': title,
    #    }
    #    # Make the POST request to create the order group
    #     try:
    #       response = self.make_tripletex_request(f'{base_url}/order/orderGroup', method='post', data=json.dumps(payload))
    #     except Exception as e:
    #       raise ValidationError(e)
    #    if response.status_code == 201:
    #        # Parse and return the order group ID from the response
    #        return response.json()['value']['id']
    #    else:
    #        raise ValidationError(_("Error creating order group in Tripletex: {}").format(
    #        response.text if response.text else "Unknown error"))

    def _create_tripletex_product_if_needed(self, base_url, product):
        """
         Create a product in Tripletex if not already linked.
        :param base_url: Base Tripletex API URL
        :param product: product.product record
        """
        print('\n---------_create_tripletex_product_if_needed')
        if not product.tripletex_product:
            product_data = {
                'name': product.name,
                'number': product.default_code or None,
                'costExcludingVatCurrency': product.standard_price,
                'priceExcludingVatCurrency': product.list_price,
            }
            try:
                response = self.make_tripletex_request(f'{base_url}/product', method='post', data=json.dumps(product_data))
            except Exception as e:
                raise ValidationError(e)
            if response.status_code == 201:
                product.write({'tripletex_product': response.json()['value']['id']})
            else:
                raise ValidationError(_("Error creating Product in Tripletex: {}").format(
                    response.text if response.text else "Unknown error"))
