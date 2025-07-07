from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError
from bs4 import BeautifulSoup


class TripletexProductMap(models.TransientModel):
    _name = 'tripletex.product.map'
    _description = "Tripletex Product Map"


    def convert_html_to_normal_text(self, raw_html):
        soup = BeautifulSoup(raw_html or "", "html.parser")
        return soup.get_text(separator="\n")

    def get_tripletex_data(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        if not base_url:
            raise ValidationError(_("Error: Base URL not configured. Please set 'ak_tripletex_auth.base_url'."))

        password = self.env['tripletex.session.token'].search([], limit=1).token
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        auth = (0, password)

        try:
            initial_response = requests.get(f'{base_url}/product', headers=headers, auth=auth)
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code == 200:
            full_result_length = json.loads(initial_response.text).get('fullResultSize', 0)
            params = {'from': 0, 'count': full_result_length}
            try:
                response = requests.get(f'{base_url}/product?from=0&count={full_result_length}',
                                        headers=headers, auth=auth)
            except Exception as e:
                raise ValidationError(e)
        return base_url, headers, auth, response

    def import_products_from_odoo(self):
        base_url, headers, auth, response = self.get_tripletex_data()
        external_numbers = set()

        if response.status_code == 200:
            for data in json.loads(response.text).get('values', []):
                if data.get('number'):
                    external_numbers.add(data['number'])

        tripletex_uoms = {}
        try:
            unit_list_response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
            if unit_list_response.status_code == 200:
                for unit in json.loads(unit_list_response.text).get('values', []):
                    short_name = unit.get('nameShort', '').lower()
                    if short_name:
                        tripletex_uoms[short_name] = unit['id']
        except Exception as e:
            print(f"Failed to fetch UoMs from Tripletex: {e}")

        for product in self.env['product.template'].search([('default_code', '!=', False)]):
            if not product.default_code in external_numbers:
                uom = product.uom_id
                odoo_uom_short = uom.name.lower()
                tripletex_uom_id = tripletex_uoms.get(odoo_uom_short)

                if not tripletex_uom_id:
                    unit_payload = {
                        "name": uom.name,
                        "nameShort": odoo_uom_short,
                        "nameEN": uom.name,
                        "nameShortEN": odoo_uom_short,
                        "commonCode": uom.common_code.name,
                    }
                    try:
                        create_uom_response = requests.post(
                            f"{base_url}/product/unit",
                            headers=headers,
                            auth=auth,
                            data=json.dumps(unit_payload)
                        )
                        if create_uom_response.status_code in [200, 201]:
                            tripletex_uoms[odoo_uom_short] = json.loads(create_uom_response.text)['value']['id']
                    except Exception as e:
                        print(f"UoM creation error: {e}")

                payload = {
                    "number": product.default_code,
                    "name": product.name,
                    "priceExcludingVatCurrency": product.list_price,
                    "orderLineDescription": self.convert_html_to_normal_text(product.description_sale or ""),
                    "description": self.convert_html_to_normal_text(product.description or ""),
                    "costExcludingVatCurrency": product.standard_price
                }
                if tripletex_uom_id:
                    payload["productUnit"] = {"id": tripletex_uom_id}

                try:
                    create_response = requests.post(
                        f"{base_url}/product",
                        headers=headers,
                        auth=auth,
                        data=json.dumps(payload)
                    )
                except Exception as e:
                    raise ValidationError(f"Failed to send product to Tripletex: {e}")

                if create_response.status_code == 201:
                    print(f"Created product in Tripletex: {product.name}")
                else:
                    raise ValidationError(f"Failed to create product: {create_response.text}")

    def import_products_from_tripletex(self):
        base_url, headers, auth, response = self.get_tripletex_data()
        if response.status_code == 200:
            tripletex_products = json.loads(response.text).get('values', [])
            for t_product in tripletex_products:
                if t_product.get('number') and t_product.get('number') not in self.env['product.template'].search(
                        [('default_code', '!=', False)]).mapped('default_code'):
                    uom = None
                    unit_data = None
                    product_unit = t_product.get('productUnit', {})
                    if product_unit:
                        product_unit_url = product_unit.get('url')
                        if product_unit_url:
                            if not product_unit_url.startswith("http"):
                                full_unit_url = f"https://{product_unit_url}"
                            else:
                                full_unit_url = product_unit_url
                            try:
                                unit_response = requests.get(full_unit_url, headers=headers, auth=auth)
                                if unit_response.status_code == 200:
                                    unit_data = json.loads(unit_response.text).get('value')
                                    short_name = unit_data.get('nameShort', '').lower()
                                    full_name = unit_data.get('name')
                                    common_code_val = unit_data.get('commonCode')

                                    common_code = None
                                    if common_code_val:
                                        common_code = self.env['uom.common.code'].search(
                                            [('name', '=', common_code_val)], limit=1)
                                    if short_name:
                                        uom = self.env['uom.uom'].search([('name', '=', short_name)], limit=1)
                                        if not uom:
                                            uom_category = self.env['uom.category'].search([('name', '=', 'General')])
                                            if not uom_category:
                                                uom_category = self.env['uom.category'].create({'name': 'General'})
                                            uom = self.env['uom.uom'].create({
                                                'name': short_name,
                                                'category_id':uom_category.id ,
                                                # or define dynamically
                                                'uom_type': 'reference',
                                                'common_code': common_code.id if common_code else False,
                                            })
                                            print(f":::::::UOM {uom}")
                            except Exception as e:
                                print(f"Error fetching unit for: {e}")
                    vals = {
                        'name': t_product.get('name') or 'Unnamed Product',
                        'default_code': t_product.get('number'),
                        'description_sale': t_product.get('orderLineDescription', ''),
                        'description': t_product.get('description', ''),
                        'standard_price': t_product.get('costExcludingVatCurrency') or 0.0,
                        'list_price': t_product.get('priceExcludingVatCurrency') or 0.0,
                    }
                    if uom:
                        vals['uom_id'] = uom.id
                    self.env['product.template'].create(vals)
        else:
            raise ValidationError(f"Failed to fetch products from Tripletex: {response.text}")

    def product_sync(self):
        """
        Fetches all products from the Tripletex API and maps them to Odoo products.
        - Requires `ak_tripletex_auth.base_url` to be configured in system parameters.
        - Requires an active session token (`tripletex.session.token`).
        - Searches Odoo products with matching `default_code` as Tripletex `number`.
        - Writes the `tripletex_product` field with Tripletex product ID on matched records.
        :raises ValidationError: If required config or session token is missing, or request fails.
        """
        base_url, headers, auth, response = self.get_tripletex_data()
        if response.status_code == 200:
            for data in json.loads(response.text)['values']:
                for product in self.env['product.template'].search([('default_code', '=', data['number'])]):
                    product.write({
                        'tripletex_product': data['id']
                    })

    def product_sync_to_tripeltex(self):
        """

        """
        base_url, headers, auth, response = self.get_tripletex_data()
        if response.status_code == 200:
            tripletex_uom = {}
            try:
                unit_list_response = requests.get(f"{base_url}/product/unit", headers=headers, auth=auth)
                if unit_list_response.status_code == 200:
                    for unit in json.loads(unit_list_response.text).get('values', []):
                        short_name = unit.get('nameShort', '').lower()
                        if short_name:
                            tripletex_uom[short_name] = unit['id']
            except Exception as e:
                print(f"Failed to fetch UoMs from Tripletex: {e}")

            last_sync_str = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.product_sync_date')
            if last_sync_str:
                last_sync_str = last_sync_str.replace('T', ' ')  # convert ISO to Odoo datetime format
                last_sync_date = fields.Datetime.from_string(last_sync_str)
            else:
                last_sync_date = None

            for data in json.loads(response.text)['values']:
                product = self.env['product.template'].search([('default_code', '=', data['number'])], limit=1)
                if product and last_sync_date and product.write_date and (last_sync_date <= product.write_date):
                    uom = product.uom_id
                    odoo_uom_short = uom.name.lower()

                    if not tripletex_uom.get(odoo_uom_short):
                        unit_payload = {
                            "name": uom.name,
                            "nameShort": odoo_uom_short,
                            "nameEN": uom.name,
                            "nameShortEN": odoo_uom_short,
                            "commonCode": uom.common_code.name if uom.common_code else "NAR",
                        }
                        try:
                            create_uom_response = requests.post(
                                f"{base_url}/product/unit",
                                headers=headers,
                                auth=auth,
                                data=json.dumps(unit_payload)
                            )
                            if create_uom_response.status_code in [200, 201]:
                                tripletex_uom_id = json.loads(create_uom_response.text)['value']['id']
                                tripletex_uom[odoo_uom_short] = tripletex_uom_id  # cache it for next product
                                print(f" Created UoM in Tripletex: {uom.name}")
                            else:
                                print(f"Could not create UoM: {create_uom_response.text}")
                        except Exception as e:
                            print(f"UoM creation error: {e}")

                    tripletex_uom_id = None
                    odoo_uom_short = product.uom_id.name.lower()
                    if odoo_uom_short in tripletex_uom:
                        tripletex_uom_id = tripletex_uom[odoo_uom_short]

                    payload = {
                        "name": product.name,
                        "description": product.description or "",
                        "orderLineDescription": self.convert_html_to_normal_text(product.description_sale or ""),
                        "priceExcludingVatCurrency": self.convert_html_to_normal_text(product.list_price),
                        "costExcludingVatCurrency": product.standard_price or 0.0,
                    }
                    if tripletex_uom_id:
                        payload["productUnit"] = {"id": tripletex_uom_id}
                    try:
                        update_response = requests.put(
                            f"{base_url}/product/{data.get('id')}",
                            headers=headers,
                            auth=auth,
                            data=json.dumps(payload)
                        )
                    except Exception as e:
                        raise ValidationError(f"Failed to update product {data['number']} in Tripletex: {e}")

                    if update_response.status_code in [200, 201]:
                        print("Updated Tripletex product")
                    else:
                        raise ValidationError(" Failed to update product")

            self.env['ir.config_parameter'].sudo().set_param(
                'ak_tripletex_auth.product_sync_date',
                fields.Datetime.now().isoformat()
            )
