from odoo import fields, models, api, _
import requests
import json
import base64
from odoo.exceptions import UserError, ValidationError
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor


class TripletexProductMap(models.TransientModel):
    _name = 'tripletex.product.map'
    _description = "Tripletex Product Map"

    @property
    def get_tripletex_data(self):
        base_url, headers, password = self.env['tripletex.customer.load'].customer_data_get_from_tripletex()
        auth = ('0', password)
        try:
            initial_response = requests.get(f'{base_url}/product', headers=headers, auth=auth)
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code == 200:
            full_result_length = json.loads(initial_response.text).get('fullResultSize', 0)
            try:
                response = requests.get(f'{base_url}/product?from=0&count={full_result_length}',
                                        headers=headers, auth=auth)
            except Exception as e:
                raise ValidationError(e)
        return base_url, headers, auth, response

    def fetch_tripletex_uom_map(self, base_url, headers, auth):
        tripletex_uom = {}
        try:
            unit_response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
            if unit_response.status_code == 200:
                for unit in json.loads(unit_response.text).get('values', []):
                    short_name = unit.get('nameShort', '').lower()
                    if short_name:
                        tripletex_uom[short_name] = unit['id']
        except Exception as e:
            print(f"Failed to fetch UoMs from Tripletex: {e}")
        return tripletex_uom

    def create_tripletex_uom(self, base_url, headers, auth, uom):
        unit_payload = {
            "name": uom.name,
            "nameShort": uom.name.lower(),
            "nameEN": uom.name,
            "nameShortEN": uom.name.lower(),
            "commonCode": uom.common_code.name if uom.common_code else '',
        }
        try:
            create_uom_response = requests.post(
                f"{base_url}/product/unit",
                headers=headers,
                auth=auth,
                data=json.dumps(unit_payload)
            )
            if create_uom_response.status_code in [200, 201]:
                return json.loads(create_uom_response.text)['value']['id']
        except Exception as e:
            print(f"UoM creation error: {e}")
        return None

    def prepare_tripletex_product_payload(self, product, tripletex_uom_id=None):
        payload = {
            "number": product.default_code,
            "name": product.name,
            "isInactive": not product.active,
            "priceExcludingVatCurrency": product.list_price,
            "orderLineDescription": self.convert_html_to_normal_text(product.description_sale or ""),
            "description": self.convert_html_to_normal_text(product.description or ""),
            "costExcludingVatCurrency": product.standard_price
        }
        if tripletex_uom_id:
            payload["productUnit"] = {"id": tripletex_uom_id}
        return payload

    # def import_products_from_odoo(self):
    #     base_url, headers, auth, response = self.get_tripletex_data
    #     trip_product_numbers = set()
    #
    #     if response.status_code == 200:
    #         for data in json.loads(response.text).get('values', []):
    #             if data.get('number'):
    #                 trip_product_numbers.add(data['number'])
    #
    #     # Fetch UoM mapping
    #     tripletex_uom = self.fetch_tripletex_uom_map(base_url, headers, auth)
    #
    #     # Search products in Odoo
    #     existing_product = self.env['product.template'].search([('default_code', '!=', False)])
    #     for product in existing_product:
    #         if product.default_code not in trip_product_numbers:
    #             uom = product.uom_id
    #             odoo_uom_short = uom.name.lower()
    #             tripletex_uom_id = tripletex_uom.get(odoo_uom_short)
    #
    #             # Create UoM in Tripletex if not found
    #             if not tripletex_uom_id:
    #                 tripletex_uom_id = self.create_tripletex_uom(base_url, headers, auth, uom)
    #
    #             try:
    #                 create_response = requests.post(
    #                     f"{base_url}/product",
    #                     headers=headers,
    #                     auth=auth,
    #                     data=json.dumps(self.prepare_tripletex_product_payload(product, tripletex_uom_id))
    #                 )
    #             except Exception as e:
    #                 raise ValidationError(f"Failed to send product to Tripletex: {e}")
    #
    #             if create_response.status_code in [201, 200]:
    #                 product_data = create_response.json().get('value', {})
    #                 if product_data.get('id'):
    #                     product.tripletex_product = product_data.get('id')
    #
    #                     self.create_tripletex_supplier_product(
    #                         product, product_data, base_url, headers, auth, tripletex_uom_id
    #                     )

    def import_products_from_odoo(self):
        base_url, headers, auth, response = self.get_tripletex_data
        trip_product_numbers = set()

        if response.status_code == 200:
            for data in json.loads(response.text).get('values', []):
                if data.get('number'):
                    trip_product_numbers.add(data['number'])
        tripletex_uom = self.fetch_tripletex_uom_map(base_url, headers, auth)

        products = self.env['product.template'].with_context(active_test=False).search([('default_code', '!=', False)])
        products_to_create = [p for p in products if p.default_code not in trip_product_numbers]

        def process_product(product_id):
            product = self.env['product.template'].browse(product_id)
            print("\nProducts::::::::::::::::", product)
            try:
                uom = product.uom_id
                uom_short = uom.name.lower()
                tripletex_uom_id = tripletex_uom.get(uom_short)

                if not tripletex_uom_id:
                    tripletex_uom_id = self.create_tripletex_uom(base_url, headers, auth, uom)

                payload = self.prepare_tripletex_product_payload(product, tripletex_uom_id)
                response = requests.post(
                    f"{base_url}/product",
                    headers=headers,
                    auth=auth,
                    data=json.dumps(payload)
                )

                if response.status_code in [200, 201]:
                    data = response.json().get('value', {})
                    return {
                        'product_id': product.id,
                        'tripletex_id': data.get('id'),
                        'tripletex_number': data.get('number'),
                        'tripletex_name': data.get('name'),
                        'tripletex_uom_id': tripletex_uom_id
                    }
                else:
                    print(f"Failed to create product {product.name}: {response.text}")
            except Exception as e:
                print(f"Product {product.name} failed: {e}")
            return None

        product_ids = [p.id for p in products_to_create]
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_product, product_ids))

        for result in filter(None, results):
            product = self.env['product.template'].browse(result.get('product_id'))
            product.write({'tripletex_product': result.get('tripletex_id')})

            self.create_tripletex_supplier_product(
                product,
                {
                    'id': result.get('tripletex_id'),
                    'number': result.get('tripletex_number'),
                    'name': result.get('tripletex_name'),
                    'standard_price': product.standard_price
                },
                base_url, headers, auth, result.get('tripletex_uom_id')
            )

    def create_tripletex_supplier_product(self, product, product_data, base_url, headers, auth, tripletex_uom_id=None):
        """
        Create a Tripletex supplier product if the Odoo product has a vendor.

        :param product: recordset of product.template
        :param product_data: dict returned from Tripletex product creation (must include 'id' and 'number')
        :param base_url: Tripletex API base URL
        :param headers: request headers
        :param auth: request authentication tuple
        :param tripletex_uom_id: Optional UoM ID from Tripletex
        :raises ValidationError: On supplier or supplierProduct creation failure
        """

        if product.seller_ids:
            supplier = product.seller_ids[0].partner_id
            supplier_id = supplier.trip_customer
            if not supplier_id:
                supplier_id = self.create_tripletex_supplier(supplier, base_url, headers, auth)
            supplier_product_payload = {
                "supplier": {"id": supplier_id},
                "number": product_data.get('number'),
                "name": product_data.get('name'),
                "resaleProduct": {"id": product_data.get('id')},
                "costExcludingVatCurrency": product_data.get('standard_price'),
            }
            if tripletex_uom_id:
                supplier_product_payload["productUnit"] = {"id": tripletex_uom_id}
            try:
                supplier_product_response = requests.post(
                    f"{base_url}/product/supplierProduct",
                    headers=headers,
                    auth=auth,
                    data=json.dumps(supplier_product_payload)
                )
                if supplier_product_response.status_code not in [200, 201]:
                    raise ValidationError(f"Failed to create supplier product: {supplier_product_response.text}")
            except Exception as e:
                raise ValidationError(f"Failed to create supplier product to Tripletex: {e}")

    def create_tripletex_supplier(self, supplier, base_url, headers, auth):
        """
        Creates or retrieves a supplier in Tripletex based on the supplier's name.

        - First, checks if the supplier already exists in Tripletex using the name.
        - If found, returns the existing Tripletex supplier ID.
        - If not found, constructs a payload using supplier data and sends a POST request to create it.
        - Also writes the `trip_customer` field on the supplier record with the returned Tripletex ID.
        """
        try:
            search_response = requests.get(
                f"{base_url}/supplier",
                headers=headers,
                auth=auth,
                params={"name": supplier.name}
            )
            if search_response.status_code == 200:
                supplier_data = search_response.json().get('values', [])
                matched = [s for s in supplier_data if s.get('name') == supplier.name]
                if matched:
                    return matched[0]['id']

            payload = {
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
            create_response = requests.post(
                f"{base_url}/supplier",
                headers=headers,
                auth=auth,
                data=json.dumps(payload)
            )
            if create_response.status_code in [200, 201]:
                trip_id = create_response.json()['value']['id']
                if supplier:
                    supplier.write({'trip_customer': trip_id})
                return trip_id
            else:
                raise ValidationError(f"Could not create supplier in Tripletex: {create_response.text}")
        except Exception as e:
            raise ValidationError(f"Supplier creation failed: {str(e)}")

    # def import_products_from_tripletex(self):
    #     """
    #     Imports products from Tripletex into Odoo.
    #
    #     - Fetches product list from Tripletex API.
    #     - Skips products that already exist in Odoo based on `default_code`.
    #     - For each new product:
    #         - Fetches and creates corresponding UoM from Tripletex if needed.
    #         - Creates a product.template record in Odoo with relevant fields.
    #     """
    #     base_url, headers, auth, response = self.get_tripletex_data
    #     if response.status_code == 200:
    #         tripletex_products = json.loads(response.text).get('values', [])
    #         existing_product = set(
    #             self.env['product.template'].search([('default_code', '!=', False)]).mapped('default_code')
    #         )
    #         for t_product in tripletex_products:
    #             if t_product.get('number') and t_product.get('number') in existing_product and t_product.get(
    #                     'resaleProduct'):
    #                 supplier_data = t_product.get('supplier')
    #                 if supplier_data:
    #                     supplier_id = supplier_data.get('id')
    #                     supplier_partner = self.env['res.partner'].search([('trip_customer', '=', supplier_id)],
    #                                                                       limit=1)
    #                     print("\nsupplier_partner", supplier_partner)
    #                     if not supplier_partner:
    #                         try:
    #                             supplier_response = requests.get(
    #                                 f"{base_url}/supplier/{supplier_id}",
    #                                 headers=headers,
    #                                 auth=auth
    #                             )
    #                             supplier_info = supplier_response.json().get('value')
    #                         except Exception as e:
    #                             raise ValidationError(_("Failed to fetch supplier data: %s") % str(e))
    #                         supplier_vals = {
    #                             'name': supplier_info.get('name'),
    #                             'email': supplier_info.get('email'),
    #                             'vat': supplier_info.get('organizationNumber'),
    #                             'phone': supplier_info.get('phoneNumber'),
    #                             'mobile': supplier_info.get('phoneNumberMobile'),
    #                             'website': supplier_info.get('website'),
    #                             'comment': supplier_info.get('description'),
    #                             'trip_customer': supplier_id,
    #                             'company_type': 'person' if supplier_info.get('isPrivateIndividual') else 'company',
    #                             'supplier_rank': 1,
    #                         }
    #                         supplier_vals.update(self.env['tripletex.customer.load'].get_address_vals(
    #                             supplier_info.get('postalAddress', {}).get('id') or 0, prefix=''))
    #                         supplier_vals.update(self.env['tripletex.customer.load'].get_address_vals(
    #                             supplier_info.get('physicalAddress', {}).get('id') or 0, prefix='business_'))
    #                         supplier_partner = self.env['res.partner'].create(supplier_vals)
    #                     template = self.env['product.template'].search(
    #                         [('default_code', '=', t_product.get('number'))], limit=1)
    #                     if template and not template.seller_ids.filtered(
    #                             lambda s: s.partner_id.id == supplier_partner.id):
    #                         template.write({
    #                             'seller_ids': [(0, 0, {
    #                                 'partner_id': supplier_partner.id,
    #                                 'min_qty': 1,
    #                                 'price': t_product.get('costExcludingVatCurrency') or 0.0,
    #                                 'delay': 1,
    #                             })]
    #                         })
    #
    #             elif t_product.get('number') not in existing_product:
    #                 uom = None
    #                 if t_product.get('productUnit', {}) and t_product.get('productUnit', {}).get('url'):
    #                     unit_url = t_product.get('productUnit', {}).get('url', '')
    #                     full_unit_url = unit_url if unit_url.startswith('http') else f"https://{unit_url}"
    #                     try:
    #                         unit_response = requests.get(full_unit_url, headers=headers, auth=auth)
    #                         if unit_response.status_code == 200:
    #                             unit_data = json.loads(unit_response.text).get('value')
    #                             short_name = unit_data.get('nameShort', '').lower()
    #                             common_code_name = unit_data.get('commonCode')
    #                             common_code = self.env['uom.common.code'].search([('name', '=', common_code_name)],
    #                                                                              limit=1) if common_code_name else None
    #                             uom = self.env['uom.uom'].search([('name', '=', short_name)],
    #                                                              limit=1) if short_name else None
    #                             if not uom:
    #                                 uom_category = self.env['uom.category'].search([('name', '=', 'General')]) or \
    #                                                self.env['uom.category'].create({'name': 'General'})
    #                                 uom = self.env['uom.uom'].create({
    #                                     'name': short_name,
    #                                     'category_id': uom_category.id,
    #                                     'uom_type': 'reference',
    #                                     'common_code': common_code.id if common_code else False,
    #                                 })
    #                     except Exception as e:
    #                         print(f"Error fetching unit for: {e}")
    #                 vals = {
    #                     'name': t_product.get('name') or 'Unnamed Product',
    #                     'default_code': t_product.get('number'),
    #                     'description_sale': t_product.get('orderLineDescription', ''),
    #                     'description': t_product.get('description', ''),
    #                     'standard_price': t_product.get('costExcludingVatCurrency') or 0.0,
    #                     'list_price': t_product.get('priceExcludingVatCurrency') or 0.0,
    #                     'tripletex_product': t_product.get('id')
    #                 }
    #                 if uom:
    #                     vals['uom_id'] = uom.id
    #                 self.env['product.template'].with_context(skip_tripletex_sync=True).create(vals)
    #                 existing_product.add(t_product.get('number'))
    #
    #     else:
    #         raise ValidationError(f"Failed to fetch products from Tripletex: {response.text}")
    def import_products_from_tripletex(self):
        base_url, headers, auth, response = self.get_tripletex_data
        if response.status_code != 200:
            raise ValidationError(f"Failed to fetch products from Tripletex: {response.text}")

        tripletex_products = json.loads(response.text).get('values', [])
        Product = self.env['product.template'].with_context(skip_tripletex_sync=True)
        Partner = self.env['res.partner']
        AddressTool = self.env['tripletex.customer.load']

        existing_codes = set(Product.search([('default_code', '!=', False)]).mapped('default_code'))

        def fetch_supplier_and_uom(t_product):
            result = {
                'product_data': t_product,
                'supplier': None,
                'uom': None
            }
            try:
                supplier_info = None
                supplier_data = t_product.get('supplier')
                if supplier_data:
                    supplier_id = supplier_data.get('id')
                    supplier = Partner.search([('trip_customer', '=', supplier_id)], limit=1)
                    if not supplier:
                        supplier_response = requests.get(f"{base_url}/supplier/{supplier_id}", headers=headers,
                                                         auth=auth)
                        if supplier_response.status_code == 200:
                            supplier_info = supplier_response.json().get('value')
                            supplier_vals = {
                                'name': supplier_info.get('name'),
                                'email': supplier_info.get('email'),
                                'vat': supplier_info.get('organizationNumber'),
                                'phone': supplier_info.get('phoneNumber'),
                                'mobile': supplier_info.get('phoneNumberMobile'),
                                'website': supplier_info.get('website'),
                                'comment': supplier_info.get('description'),
                                'trip_customer': supplier_id,
                                'company_type': 'person' if supplier_info.get('isPrivateIndividual') else 'company',
                                'supplier_rank': 1,
                            }
                            supplier_vals.update(
                                AddressTool.get_address_vals(supplier_info.get('postalAddress', {}).get('id') or 0))
                            supplier_vals.update(
                                AddressTool.get_address_vals(supplier_info.get('physicalAddress', {}).get('id') or 0,
                                                             prefix='business_'))
                            supplier = Partner.create(supplier_vals)
                    result['supplier'] = supplier

                uom_data = t_product.get('productUnit', {})
                if uom_data.get('url'):
                    full_url = uom_data['url'] if uom_data['url'].startswith('http') else f"https://{uom_data['url']}"
                    uom_response = requests.get(full_url, headers=headers, auth=auth)
                    if uom_response.status_code == 200:
                        u_data = uom_response.json().get('value')
                        short_name = u_data.get('nameShort', '').lower()
                        common_code = self.env['uom.common.code'].search([('name', '=', u_data.get('commonCode'))],
                                                                         limit=1)
                        uom = self.env['uom.uom'].search([('name', '=', short_name)], limit=1)
                        if uom:
                            if uom.common_code != common_code:
                                uom.write({'common_code': common_code.id if common_code else False})
                        else:
                            uom_category = self.env['uom.category'].search([('name', '=', 'General')], limit=1)
                            if not uom_category:
                                uom_category = self.env['uom.category'].create({'name': 'General'})
                            uom = self.env['uom.uom'].create({
                                'name': short_name,
                                'category_id': uom_category.id,
                                'uom_type': 'reference',
                                'common_code': common_code.id if common_code else False,
                            })
                        result['uom'] = uom

            except Exception as e:
                print(f"Failed to fetch supplier or UOM: {e}")
            return result

        with ThreadPoolExecutor(max_workers=10) as executor:
            enriched_data = list(executor.map(fetch_supplier_and_uom, tripletex_products))
        for entry in enriched_data:
            t_product = entry['product_data']
            supplier = entry['supplier']
            uom = entry['uom']
            code = t_product.get('number')
            if not code:
                continue

            existing_product = Product.search([('default_code', '=', code)], limit=1)
            if code not in existing_codes:
                vals = {
                    'name': t_product.get('name') or 'Unnamed Product',
                    'default_code': code,
                    "active": not t_product.get('isInactive'),
                    'description_sale': t_product.get('orderLineDescription', ''),
                    'description': t_product.get('description', ''),
                    'standard_price': t_product.get('costExcludingVatCurrency') or 0.0,
                    'list_price': t_product.get('priceExcludingVatCurrency') or 0.0,
                    'tripletex_product': t_product.get('id'),
                }
                if uom:
                    vals['uom_id'] = uom.id
                existing_product = Product.create(vals)
                existing_codes.add(code)
            if existing_product and supplier and not existing_product.seller_ids.filtered(
                    lambda s: s.partner_id == supplier):
                existing_product.write({
                    'seller_ids': [(0, 0, {
                        'partner_id': supplier.id,
                        'min_qty': 1,
                        'price': t_product.get('costExcludingVatCurrency') or 0.0,
                        'delay': 1,
                    })]
                })

    def product_sync(self):
        """
        Sync Tripletex products to Odoo:
        - Matches by tripletex_product or fallback default_code.
        - Updates name, price, UoM, description, supplier.
        - Skips UoM write if restricted by stock moves/journal entries.
        - Uses context to prevent infinite sync loops.
        """
        base_url, headers, auth, response = self.get_tripletex_data
        if response.status_code != 200:
            raise ValidationError("Failed to fetch products from Tripletex: %s" % response.text)

        tripletex_products = json.loads(response.text).get('values', [])
        for data in tripletex_products:
            data_id = data.get('id')
            product = self.env['product.template'].search(
                [('tripletex_product', '=', data_id), ('default_code', '=', data.get('number'))])
            if not product:
                continue
            uom_id = False
            unit_data = data.get('productUnit') or data.get('unit')
            if unit_data and unit_data.get('url'):
                unit_url = unit_data['url']
                full_unit_url = unit_url if unit_url.startswith('http') else f"https://{unit_url}"
                try:
                    unit_response = requests.get(full_unit_url, headers=headers, auth=auth)
                    if unit_response.status_code == 200:
                        unit_val = unit_response.json().get('value')
                        short_name = unit_val.get('nameShort', '').lower()
                        common_code_name = unit_val.get('commonCode')
                        common_code = self.env['uom.common.code'].search([('name', '=', common_code_name)],
                                                                         limit=1) if common_code_name else None
                        uom = self.env['uom.uom'].search([('name', '=', short_name)], limit=1)
                        if not uom:
                            uom_category = self.env['uom.category'].search([('name', '=', 'General')], limit=1) or \
                                           self.env['uom.category'].create({'name': 'General'})
                            uom = self.env['uom.uom'].create({
                                'name': short_name,
                                'category_id': uom_category.id,
                                # 'uom_type': 'reference',
                                'common_code': common_code.id if common_code else False,
                            })
                        uom_id = uom.id
                except Exception as e:
                    raise ValidationError("Failed to fetch UoM info: %s" % e)
            for prod in product:
                vals = {
                    'name': data.get('name') or 'Unnamed Product',
                    'default_code': data.get('number'),
                    'active' : not data.get('isInactive'),
                    'description_sale': data.get('orderLineDescription', ''),
                    'description': data.get('description', ''),
                    'standard_price': data.get('costExcludingVatCurrency') or 0.0,
                    'list_price': data.get('priceExcludingVatCurrency') or 0.0,
                }

                if uom_id:
                    vals['uom_id'] = uom_id
                    vals['uom_po_id'] = uom_id
                # --- Image Handling ---
                image_data = data.get('image')
                if image_data and image_data.get('url'):
                    print("\nimage_data:::::::::", image_data)
                    image_url = image_data.get('url')
                    full_image_url = image_url if image_url.startswith('http') else f"https://{image_url}"
                    try:
                        # Step 1: Fetch the first response — this is NOT the image, but JSON metadata
                        image_response = requests.get(full_image_url, headers=headers, auth=auth)
                        print("\nimage_response:::::", image_response)

                        if image_response.status_code == 200:
                            # Step 2: Decode the JSON directly (no need to re-encode)
                            image_metadata = json.loads(image_response.content).get("value")
                            if image_metadata and image_metadata.get("url"):
                                # Step 3: Now fetch the actual image
                                real_image_url = image_metadata["url"]
                                real_image_url = real_image_url if real_image_url.startswith(
                                    "http") else f"https://{real_image_url}"
                                final_image_response = requests.get(real_image_url, headers=headers, auth=auth)
                                if final_image_response.status_code == 200:
                                    # Step 4: Assign binary content
                                    image_binary = final_image_response.content
                                    vals['image_1920'] = image_binary
                                    print("Binary data (first 100 bytes):", image_binary[:100])
                                    print("Image binary size:", len(image_binary))
                                    print("MIME check (first 8 bytes):", image_binary[:8])
                                else:
                                    print(f"Failed to fetch actual image from URL: {real_image_url}")
                            else:
                                print("No valid image metadata URL found.")
                    except Exception as e:
                        print(f"Failed to load image for product ID {data_id}: {e}")
                print("\n\nvals::::::::::::::::::",vals)
                try:
                    print(f">> Writing to product {product.id}: {vals}")
                    prod.with_context(skip_tripletex_sync=True).write(vals)
                except (UserError, ValidationError) as e:
                    continue
            for item in tripletex_products:
                resale = item.get('resaleProduct')
                if resale and resale.get('id') == data.get('id'):
                    supplier_id = item.get('supplier', {}).get('id')
                    supplier_partner = False
                    if int(product.tripletex_product) == resale.get('id'):
                        if supplier_id:
                            supplier_partner = self.env['res.partner'].search([('trip_customer', '=', supplier_id)],
                                                                              limit=1)
                        if not supplier_partner:
                            try:
                                supplier_response = requests.get(f"{base_url}/supplier/{supplier_id}", headers=headers,
                                                                 auth=auth)
                                if supplier_response.status_code == 200:
                                    supplier_info = supplier_response.json().get('value')
                                    supplier_vals = {
                                        'name': supplier_info.get('name'),
                                        'email': supplier_info.get('email'),
                                        'vat': supplier_info.get('organizationNumber'),
                                        'phone': supplier_info.get('phoneNumber'),
                                        'mobile': supplier_info.get('phoneNumberMobile'),
                                        'website': supplier_info.get('website'),
                                        'comment': supplier_info.get('description'),
                                        'trip_customer': supplier_id,
                                        'company_type': 'person' if supplier_info.get(
                                            'isPrivateIndividual') else 'company',
                                        'supplier_rank': 1,
                                    }
                                    supplier_vals.update(self.env['tripletex.customer.load'].get_address_vals(
                                        supplier_info.get('postalAddress', {}).get('id') or 0, prefix=''))
                                    supplier_vals.update(self.env['tripletex.customer.load'].get_address_vals(
                                        supplier_info.get('physicalAddress', {}).get('id') or 0, prefix='business_'))
                                    supplier_partner = self.env['res.partner'].create(supplier_vals)
                            except Exception as e:
                                raise ValidationError("Failed to fetch supplier: %s" % e)

                        if supplier_partner:
                            cost = data.get('costExcludingVatCurrency') or 0.0
                            existing_vendor = product.seller_ids.filtered(
                                lambda s: s.partner_id.id == supplier_partner.id)
                            if existing_vendor:
                                existing_vendor.unlink()
                            tripletex_seller = self.env['product.supplierinfo'].create({
                                'product_tmpl_id': product.id,
                                'partner_id': supplier_partner.id,
                                'min_qty': 1,
                                'price': cost,
                                'delay': 1,
                                'sequence': 0,  # Make it appear first
                            })
                            for i, seller in enumerate(
                                    product.seller_ids.filtered(lambda s: s.id != tripletex_seller.id), start=1):
                                seller.sequence = i

    # HELPER METHOD

    def convert_html_to_normal_text(self, raw_html):
        soup = BeautifulSoup(raw_html or "", "html.parser")
        return soup.get_text(separator="\n")
