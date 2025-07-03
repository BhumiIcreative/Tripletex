from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError
import logging
from bs4 import BeautifulSoup

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'
    _description = "Product Template"

    tripletex_product = fields.Char(
        string="Product"
    )


    def convert_html_to_normal_text(self, raw_html):
        soup = BeautifulSoup(raw_html or "", "html.parser")
        return soup.get_text(separator="\n")

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override the create method to automatically push newly created Odoo products to Tripletex via API.
        Steps:
        - For each product in vals_list, prepare the data payload.
        - Include tax info if provided.
        - Post the data to Tripletex /product endpoint.
        - On success, store the Tripletex product ID in 'tripletex_product' field.
        Raises:
            ValidationError: If Tripletex API call fails or returns an error.
        """
        print('\n--------product-------create')
        res = super().create(vals_list)
        for rec in vals_list:
            taxes = rec.get('taxes_id', [])
            if taxes and isinstance(taxes[0], list) and taxes[0] and len(taxes[0]) > 1:
                tax_record = self.env['account.tax'].browse(taxes[0][1])
                tripletex_tax = tax_record.tripletex_tax if tax_record else None
                number_value = rec.get('default_code', '')
                data = {
                    'name': rec['name'],
                    'costExcludingVatCurrency': rec.get('standard_price', 0),
                    "priceExcludingVatCurrency": rec.get('list_price', 0),
                    "isStockItem": True if rec.get('detailed_type', '') == 'product' else False,
                    #             "productUnit": {
                    #                 'id': 37227,
                    #             },
                }
                if tripletex_tax:
                    # Assuming you want to include tripletex_tax_ids in vatType
                    data['vatType'] = {
                        'id': tripletex_tax,  # Assuming only one tripletex_tax is linked
                        'percentage': tax_record.amount if tax_record else None,
                    }
                if number_value:
                    data['number'] = number_value
                try:
                    response = requests.post(
                        url=f"{self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')}/product",
                        data=json.dumps(data),
                        auth=('0', self.env['tripletex.session.token'].search([], limit=1).token),
                        headers={'Content-Type': 'application/json', "Accept": "application/json"},
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code == 201:
                    res.write({'tripletex_product': response.json()['value']['id']})
                else:
                    raise ValidationError(_("Error creating Product in Tripletex: {}").format(
                        response.text if response.text else "Unknown error"))
        return res

    def write(self, vals):
        """
        Override the write method to sync updates to the product with Tripletex via API.
        - If product has a linked Tripletex ID, updates name, prices, taxes, and default code.
        - Sends a PUT request to update the Tripletex product.
        Raises:
            ValidationError: If API call fails or returns non-success status.
        """
        print('\n--------product-------write')
        res = super().write(vals)

        base_url, headers, auth, response = self.env['tripletex.product.map'].get_tripletex_data()

        tripletex_uom = {}
        try:
            unit_list_response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
            if unit_list_response.status_code == 200:
                print("\n\n@@@@@@@@@@@@@@@@@@@@@@0", json.loads(unit_list_response.text).get('values', []))
                for unit in json.loads(unit_list_response.text).get('values', []):
                    short_name = unit.get('nameShort', '').lower()
                    if short_name:
                        tripletex_uom[short_name] = unit['id']
        except Exception as e:
            print(f"Failed to fetch UoMs from Tripletex: {e}")

        for product in self:
            print('\n--------product-------11111')
            if product.tripletex_product:
                data = {}
                if 'name' in vals:
                    data['name'] = vals['name']
                if 'standard_price' in vals:
                    data['costExcludingVatCurrency'] = vals['standard_price']
                if 'list_price' in vals:
                    data['priceExcludingVatCurrency'] = vals['list_price']
                if 'description' in vals:
                    data['description'] = self.convert_html_to_normal_text(vals['description'])
                if 'description_sale' in vals:
                    data['orderLineDescription'] = self.convert_html_to_normal_text(vals['description_sale'])

                # ✅ Updated UoM logic
                if 'uom_id' in vals:
                    uom_id = vals['uom_id']
                    tripletex_uom_id = None
                    if uom_id:
                        uom = self.env['uom.uom'].browse(uom_id)
                        print("\n\n:::::::::::uom_id", uom_id)
                        if uom.exists():
                            odoo_uom_short = uom.name.lower()
                            tripletex_uom_id = tripletex_uom.get(odoo_uom_short)
                            print("\n\n:::::::::::odoo_uom_short", odoo_uom_short)
                            print("\n\n:::::::::::tripletex_uom_id111111", tripletex_uom_id)

                            if not tripletex_uom_id:
                                if not uom.common_code:
                                    raise ValidationError(f"Missing common code for UoM '{uom.name}'")
                                unit_payload = {
                                    "name": uom.name,
                                    "nameShort": odoo_uom_short,
                                    "nameEN": uom.name,
                                    "nameShortEN": odoo_uom_short,
                                    "commonCode": uom.common_code.name,
                                }
                                print("\n\n:::::::::::unit_payload", unit_payload)
                                try:
                                    create_uom_response = requests.post(
                                        f"{base_url}/product/unit",
                                        headers=headers,
                                        auth=auth,
                                        data=json.dumps(unit_payload)
                                    )
                                    if create_uom_response.status_code in [200, 201]:
                                        tripletex_uom_id = json.loads(create_uom_response.text)['value']['id']
                                        print("\n\n:::::::::::tripletex_uom_id2222222", tripletex_uom_id)

                                        tripletex_uom[odoo_uom_short] = tripletex_uom_id
                                    else:
                                        print(f"Could not create UoM: {create_uom_response.text}")
                                except Exception as e:
                                    raise ValidationError(f"UoM creation error: {e}")

                    if tripletex_uom_id:
                        data['productUnit'] = {"id": tripletex_uom_id}
                        print("\n\ndata", data)

                # Tax
                tax_ids = [tax.tripletex_tax for tax in product.taxes_id if tax.tripletex_tax]
                data['vatType'] = {
                    'id': tax_ids[0] if tax_ids else None,
                    'percentage': product.taxes_id[0].amount if product.taxes_id else None,
                }

                number_value = vals.get('default_code', product.default_code)
                if number_value:
                    data['number'] = number_value

                if data:  # Only make a request if there's data to update
                    try:
                        response = requests.put(
                            url=f"{base_url}/product/{product.tripletex_product}",
                            data=json.dumps(data),
                            auth=auth,
                            headers=headers,
                        )
                    except Exception as e:
                        raise ValidationError(e)
                    if response.status_code == 404:
                        product.write({'tripletex_product': False})
                    elif response.status_code != 200:
                        raise ValidationError(_("Error updating Product in Tripletex: {}").format(
                            response.text if response.text else "Unknown error"))
        return res

    def unlink(self):
        """
        Override the unlink method to also delete the product in Tripletex.
        - Checks if product has a Tripletex ID.
        - Sends a DELETE request to remove the product from Tripletex.
        - If Tripletex deletion fails, raises an error and prevents unlinking in Odoo.
        Returns:
            Recordset: Result of the original unlink call.
        Raises:
            ValidationError: If Tripletex product deletion fails.
        """
        print('\n------product---------unlink')
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url or not password:
            return super().unlink()
        for product in self:
            # First, delete the product in Tripletex
            if product.tripletex_product:
                try:
                    response = requests.delete(
                        url=f'{base_url}/product/{product.tripletex_product}',
                        auth=('0', password),
                        headers={'Content-Type': 'application/json', "Accept": "application/json"},
                    )
                except Exception as e:
                    raise ValidationError(e)
                if response.status_code == 404:
                    product.write({'tripletex_product': False})
                if response.status_code == 204:
                    print(response.text)
                else:
                    raise ValidationError(_("Error deleting product in Tripletex: %s") % response.text)
        return super().unlink()
