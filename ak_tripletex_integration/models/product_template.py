from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError
import logging
from bs4 import BeautifulSoup


class ProductTemplate(models.Model):
    _inherit = 'product.template'
    _description = "Product Template"

    tripletex_product = fields.Char(string="Tripletex Id", copy=False)

    def get_tripletex_connection(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_integration.base_url')
        token = self.env['tripletex.session.token'].search([], limit=1).token
        if not base_url or not token:
            raise ValidationError(_("Tripletex Base URL or Token is missing."))
        auth = ('0', token)
        headers = {'Content-Type': 'application/json', "Accept": "application/json"}
        return base_url, headers, auth

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('skip_tripletex_sync'):
            return super().create(vals_list)
        products = super().create(vals_list)
        base_url, headers, auth = self.get_tripletex_connection()

        # Fetch Tripletex UoMs once
        tripletex_uom_map = {}
        try:
            response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
            if response.ok:
                tripletex_uom_map = {
                    unit.get('nameShort', '').lower(): unit['id']
                    for unit in response.json().get('values', [])
                    if unit.get('nameShort')
                }
        except Exception as e:
            raise ValidationError(f"Failed to fetch UoMs from Tripletex: {e}")

        for vals, product in zip(vals_list, products):
            if not vals.get('default_code'):
                continue  # Skip if product has no internal reference

            # ---- Handle Tax
            tax_ids = vals.get('taxes_id', [])
            tax_record = None
            tripletex_tax_id = None
            if tax_ids and isinstance(tax_ids[0], list) and len(tax_ids[0]) > 1:
                tax_record = self.env['account.tax'].browse(tax_ids[0][1])
                tripletex_tax_id = tax_record.tripletex_tax if tax_record else None

            # ---- UoM: Use same logic as in write()
            uom = product.uom_id
            tripletex_uom_id, tripletex_uom_map = self._get_or_create_tripletex_uom_id(
                uom, base_url, headers, auth, tripletex_uom_map
            )

            # ---- Prepare product payload
            payload = {
                'name': vals.get('name'),
                'number': vals.get('default_code'),
                'isInactive': not vals.get('active'),
                'costExcludingVatCurrency': vals.get('standard_price', 0),
                'priceExcludingVatCurrency': vals.get('list_price', 0),
                'isStockItem': vals.get('detailed_type') == 'product',
            }

            if tripletex_tax_id:
                payload['vatType'] = {
                    'id': tripletex_tax_id,
                    'percentage': tax_record.amount if tax_record else None,
                }

            if tripletex_uom_id:
                payload['productUnit'] = {'id': tripletex_uom_id}

            # ---- Create in Tripletex
            try:
                response = requests.post(
                    f"{base_url}/product",
                    headers=headers,
                    auth=auth,
                    data=json.dumps(payload)
                )
            except Exception as e:
                raise ValidationError(f"Failed to create product in Tripletex: {e}")

            if response.status_code in [200, 201]:
                product_data = response.json().get('value', {})
                product.tripletex_product = product_data.get('id')
                try:
                    self.env['tripletex.product.map'].create_tripletex_supplier_product(
                        product, product_data, base_url, headers, auth, tripletex_uom_id
                    )
                    print(f"✅ SupplierProduct created for product '{product.name}'")
                except Exception as e:
                    print(f"Failed to create supplierProduct for '{product.name}' in Tripletex: {e}")
            else:
                raise ValidationError(f"Tripletex product creation failed: {response.text}")
        return products

    def write(self, vals):
        print('\n--------product-------write')
        if self.env.context.get('skip_tripletex_sync'):
            return super().write(vals)

        res = super().write(vals)

        if any(self.mapped('tripletex_product')):
            base_url, headers, auth, _ = self.env['tripletex.product.map'].get_tripletex_data
            tripletex_uom = {}
            try:
                response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
                if response.ok:
                    tripletex_uom = {
                        unit.get('nameShort', '').lower(): unit['id']
                        for unit in response.json().get('values', [])
                        if unit.get('nameShort')
                    }
            except Exception as e:
                print(f"Failed to fetch UoMs from Tripletex: {e}")

            for product in self.filtered('tripletex_product'):
                print("\nproduct", product)
                data = {}
                field_map = {
                    'name': 'name',
                    'standard_price': 'costExcludingVatCurrency',
                    'list_price': 'priceExcludingVatCurrency',
                    'default_code': 'number',
                }
                data.update({trip_key: vals[field] for field, trip_key in field_map.items() if field in vals})
                description_map = {
                    'description': 'description',
                    'description_sale': 'orderLineDescription',
                }
                data.update({
                    trip_key: self.convert_html_to_normal_text(vals[field])
                    for field, trip_key in description_map.items()
                    if field in vals
                })

                # --- Handle UoM ---
                uom = self.env['uom.uom'].browse(vals.get('uom_id')) if vals.get('uom_id') else product.uom_id
                trip_uom_id, tripletex_uom = self._get_or_create_tripletex_uom_id(
                    uom, base_url, headers, auth, tripletex_uom
                )
                if 'active' in vals:
                    data['isInactive'] = not vals['active']

                if trip_uom_id:
                    data['productUnit'] = {'id': trip_uom_id}

                # --- Tax ---
                tax = next((t for t in product.taxes_id if t.tripletex_tax), None)
                data['vatType'] = {
                    'id': tax.tripletex_tax if tax else None,
                    'percentage': tax.amount if tax else None,
                }
                print("\ndata", data)
                # --- Supplier Sync (Optional) ---
                trip_supplier_id = None
                supplier = next((s.partner_id for s in product.seller_ids.sorted('sequence') if s.partner_id), None)
                if supplier:
                    if not supplier.trip_customer:
                        try:
                            self.env['tripletex.product.map'].create_tripletex_supplier(supplier, base_url, headers,
                                                                                        auth)
                        except Exception as e:
                            print(f"Tripletex supplier creation failed: {e}")
                    trip_supplier_id = supplier.trip_customer

                    try:
                        supplier_response = requests.get(
                            f"{base_url}/product/supplierProduct",
                            headers=headers,
                            auth=auth,
                            params={"product.id": product.tripletex_product}
                        )
                        if supplier_response.ok:
                            for s in supplier_response.json().get('values', []):
                                if s.get("id"):
                                    requests.delete(
                                        f"{base_url}/product/supplierProduct/{s['id']}",
                                        headers=headers,
                                        auth=auth
                                    )
                    except Exception as e:
                        print(f" Failed to delete old supplierProducts for product '{product.name}': {e}")

                # --- Send update to Tripletex ---
                if data:
                    try:
                        response = requests.put(
                            url=f"{base_url}/product/{product.tripletex_product}",
                            headers=headers,
                            auth=auth,
                            data=json.dumps(data),
                        )
                        if response.status_code == 404:
                            product.write({'tripletex_product': False})
                        elif not response.ok:
                            raise ValidationError(_("Error updating Product in Tripletex: {}").format(
                                response.text or "Unknown error"
                            ))
                    except Exception as e:
                        raise ValidationError(str(e))

                # --- Create supplierProduct if vendor exists ---
                if trip_supplier_id:
                    supplier_payload = {
                        "supplier": {"id": trip_supplier_id},
                        "number": product.default_code,
                        "name": product.name,
                        "resaleProduct": {"id": product.tripletex_product},
                        "costExcludingVatCurrency": product.standard_price,
                    }
                    if trip_uom_id:
                        supplier_payload["productUnit"] = {"id": trip_uom_id}
                    try:
                        supplier_product_response = requests.post(
                            f"{base_url}/product/supplierProduct",
                            headers=headers,
                            auth=auth,
                            data=json.dumps(supplier_payload)
                        )
                        if supplier_product_response.status_code not in [200, 201]:
                            print(f" SupplierProduct creation failed: {supplier_product_response.text}")
                        if supplier_product_response.status_code == 404:
                            product.write({'tripletex_product': False})
                    except Exception as e:
                        print(f"Failed to create supplierProduct in Tripletex: {e}")

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
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        if not base_url or not password:
            return super().unlink()
        for product in self:
            # First, delete the product in Tripletex
            if product.tripletex_product:
                try:
                    response = requests.delete(
                        url=f'{base_url}/product/{product.tripletex_product}',
                        auth=('0', password),
                        headers=headers,
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

    # HELPER METHODS
    def convert_html_to_normal_text(self, raw_html):
        """ Convert an HTML string into plain text by stripping all tags. """
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        return soup.get_text(separator="\n")

    def _get_or_create_tripletex_uom_id(self, uom, base_url, headers, auth, tripletex_uom_map):
        """
        Ensures UoM exists in Tripletex. If not, creates it and returns its ID and refreshed map.
        Returns:
            - tripletex_uom_id (int or None)
            - updated tripletex_uom_map (dict)
        """
        if uom:
            if tripletex_uom_id := tripletex_uom_map.get(uom.name.lower()):
                return tripletex_uom_id, tripletex_uom_map
            if not uom.common_code:
                raise ValidationError(_(
                    f"Missing or invalid Tripletex common code for UoM '{uom.name}'. "
                    "Please assign a valid numeric common code."
                ))
            self.env['tripletex.product.map'].create_tripletex_uom(base_url, headers, auth, uom)
            try:
                response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
                if response.ok:
                    new_map = {
                        unit.get('nameShort', '').lower(): unit['id']
                        for unit in response.json().get('values', [])
                        if unit.get('nameShort')
                    }
                    return new_map.get(uom.name.lower()), new_map
                raise ValidationError("Failed to refresh UoM list from Tripletex after creation.")
            except Exception as e:
                raise ValidationError(f"Failed to re-fetch UoMs from Tripletex: {e}")
        return None, tripletex_uom_map
