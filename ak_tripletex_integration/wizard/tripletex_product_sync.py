from odoo import models
import requests
import json
import base64
from odoo.exceptions import UserError, ValidationError
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
import logging

_logger = logging.getLogger(__name__)


class TripletexProductMap(models.TransientModel):
    _name = "tripletex.product.map"
    _description = "Tripletex Product Map"

    @property
    def get_tripletex_data(self):
        """
        Fetches all product data from the Tripletex API.

        This method performs two API requests:
        1. First, to determine the total number of product records.
        2. Second, to fetch all product data using that total count.

        Returns:
            tuple: A tuple containing:
                - base_url (str): The base Tripletex API URL.
                - headers (dict): The headers for authorization.
                - auth (tuple): Authentication credentials.
                - response (requests.Response): The full product list API response.

        Raises:
            ValidationError: If any request to Tripletex fails.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        auth = ("0", password)
        try:
            initial_response = requests.get(
                f"{base_url}/product", headers=headers, auth=auth
            )
        except Exception as e:
            raise ValidationError(e)
        if initial_response.status_code in [200, 201]:
            full_result_length = json.loads(initial_response.text).get(
                "fullResultSize", 0
            )
            try:
                response = requests.get(
                    f"{base_url}/product?from=0&count={full_result_length}",
                    headers=headers,
                    auth=auth,
                )
            except Exception as e:
                raise ValidationError(e)
        return base_url, headers, auth, response

    def create_tripletex_uom(self, base_url, headers, auth, uom):
        """
        Creates a Unit of Measure (UoM) in Tripletex if it doesn't already exist.

        Args:
            base_url (str): The base Tripletex API URL.
            headers (dict): HTTP headers including authentication token.
            auth (tuple): Authentication credentials (user token, password).
            uom (object): Odoo UoM record to be created in Tripletex.

        Returns:
            int | None: The Tripletex ID of the created UoM if successful, else None.
        """
        unit_payload = {
            "name": uom.name,
            "nameShort": uom.name.lower(),
            "nameEN": uom.name,
            "nameShortEN": uom.name.lower(),
            "commonCode": uom.common_code.name if uom.common_code else "",
        }

        try:
            response = requests.post(
                f"{base_url}/product/unit",
                headers=headers,
                auth=auth,
                json=unit_payload,  # use `json=` to avoid manual dumping
            )
            response.raise_for_status()  # Raise exception for bad responses

            return response.json().get("value", {}).get("id")

        except requests.RequestException as e:
            _logger.warning("Tripletex UoM creation failed: %s", e)

        return None

    def prepare_tripletex_product_payload(
        self, product, tripletex_uom_id=None, document_id=None
    ):
        """
        Prepares a payload dictionary for creating or updating a product in Tripletex.

        Args:
            product (record): The Odoo product record (`product.template` or `product.product`).
            tripletex_uom_id (int, optional): Tripletex unit of measure ID. Defaults to None.
            document_id (int, optional): Tripletex document ID for product image. Defaults to None.

        Returns:
            dict: Formatted payload to be sent to the Tripletex API.
        """
        payload = {
            "number": product.default_code,
            "name": product.name,
            "isInactive": not product.active,
            "priceExcludingVatCurrency": product.list_price,
            "orderLineDescription": self.convert_html_to_normal_text(
                product.description_sale or ""
            ),
            "description": self.convert_html_to_normal_text(
                product.description or ""
            ),
            "costExcludingVatCurrency": product.standard_price,
        }

        payload.update(
            {
                "productUnit": {"id": tripletex_uom_id}
                if tripletex_uom_id
                else None,
                "image": {"id": document_id} if document_id else None,
            }
        )
        # Remove keys with None values
        payload = {k: v for k, v in payload.items() if v is not None}

        return payload

    def upload_odoo_image_to_tripletex(
        self, product, tripletex_product_id, base_url, headers, auth
    ):
        """
        Uploads the product image from Odoo to Tripletex.

        This method sends the binary image data of an Odoo product to Tripletex using the
        `/product/{id}/image` endpoint. If the product has no image, the method exits early.

        Args:
            product (recordset): The Odoo product record containing the image (`image_1920`).
            tripletex_product_id (int): The corresponding Tripletex product ID.
            base_url (str): The base URL for the Tripletex API.
            headers (dict): The headers used for the API request (excluding 'Content-Type').
            auth (tuple): Authentication tuple (user ID, API token/password).

        Returns:
            None
        """
        if not product.image_1920:
            return

        try:
            image_data = base64.b64decode(product.image_1920)
            files = {"file": ("product_image.png", image_data, "image/png")}

            # Ensure 'Content-Type' header is removed (set automatically by requests with `files`)
            headers = {
                k: v for k, v in headers.items() if k.lower() != "content-type"
            }

            response = requests.post(
                f"{base_url}/product/{tripletex_product_id}/image",
                headers=headers,
                auth=auth,
                files=files,
            )

            if response.status_code in [200, 201]:
                _logger.info(f"✅ Uploaded image for product: {product.name}")
            else:
                _logger.warning(
                    f"❌ Failed to upload image for {product.name}: "
                    f"[{response.status_code}] {response.text}"
                )

        except Exception as e:
            _logger.exception(
                f"❌ Exception uploading image for {product.name}: {e}"
            )

    def import_products_from_odoo(self):
        """
        Imports Odoo products into Tripletex.

        This method fetches all existing products from Tripletex to avoid duplicates.
        Then, it compares Odoo product `default_code` with Tripletex product `number`.
        Products not found in Tripletex are created with corresponding fields, units of measure,
        and images. UoMs are also created in Tripletex if missing.

        After product creation, the Tripletex product ID is saved in the Odoo product (`tripletex_product`),
        and the corresponding supplier product is created in Tripletex.

        Uses multithreading to optimize product creation.

        Returns:
            None
        """
        base_url, headers, auth, response = self.get_tripletex_data
        trip_product_numbers = set()

        # Step 1: Fetch existing Tripletex product numbers
        if response.status_code == 200:
            for data in json.loads(response.text).get("values", []):
                if data.get("number"):
                    trip_product_numbers.add(data["number"])

        # Step 2: Prepare Tripletex UoM map
        tripletex_uom = self.fetch_tripletex_uom_map(base_url, headers, auth)

        # Step 3: Filter Odoo products that are not yet synced
        products = (
            self.env["product.template"]
            .with_context(active_test=False)
            .search([("default_code", "!=", False)])
        )
        products_to_create = [
            p for p in products if p.default_code not in trip_product_numbers
        ]

        def process_product(product_id):
            """Worker function to create a product in Tripletex."""
            product = self.env["product.template"].browse(product_id)
            try:
                uom = product.uom_id
                uom_short = uom.name.lower()
                tripletex_uom_id = tripletex_uom.get(uom_short)

                if not tripletex_uom_id:
                    tripletex_uom_id = self.create_tripletex_uom(
                        base_url, headers, auth, uom
                    )

                payload = self.prepare_tripletex_product_payload(
                    product, tripletex_uom_id
                )

                response = requests.post(
                    f"{base_url}/product",
                    headers=headers,
                    auth=auth,
                    data=json.dumps(payload),
                )

                if response.status_code in [200, 201]:
                    data = response.json().get("value", {})
                    tripletex_id = data.get("id")

                    # Upload product image to Tripletex
                    self.upload_odoo_image_to_tripletex(
                        product, tripletex_id, base_url, headers, auth
                    )

                    return {
                        "product_id": product.id,
                        "tripletex_id": data.get("id"),
                        "tripletex_number": data.get("number"),
                        "tripletex_name": data.get("name"),
                        "tripletex_uom_id": tripletex_uom_id,
                    }
                else:
                    _logger.warning(
                        f"Failed to create product {product.name}: {response.text}"
                    )
            except Exception as e:
                _logger.exception(f"Product {product.name} failed: {e}")
            return None

        # Step 4: Use multithreading to process product creation
        product_ids = [p.id for p in products_to_create]
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_product, product_ids))

        # Step 5: Save Tripletex product ID in Odoo and create supplierProduct
        for result in filter(None, results):
            product = self.env["product.template"].browse(
                result.get("product_id")
            )
            product.write({"tripletex_product": result.get("tripletex_id")})

            self.create_tripletex_supplier_product(
                product,
                {
                    "id": result.get("tripletex_id"),
                    "number": result.get("tripletex_number"),
                    "name": result.get("tripletex_name"),
                    "standard_price": product.standard_price,
                },
                base_url,
                headers,
                auth,
                result.get("tripletex_uom_id"),
            )

    def create_tripletex_supplier_product(
        self,
        product,
        product_data,
        base_url,
        headers,
        auth,
        tripletex_uom_id=None,
    ):
        """
        Create a supplier product in Tripletex for the given Odoo product if it has a vendor.

        This method checks whether the product has vendors (`seller_ids`) and ensures the supplier
        is created or available in Tripletex. It then constructs the supplier product payload
        and submits it to the Tripletex `/product/supplierProduct` endpoint.

        Args:
            product (recordset): The Odoo product.template record.
            product_data (dict): Response data from Tripletex product creation, must contain 'id', 'number', and 'name'.
            base_url (str): Tripletex API base URL.
            headers (dict): Request headers.
            auth (tuple): Authentication credentials (typically (token, '') format).
            tripletex_uom_id (int, optional): UoM ID from Tripletex for the product. Defaults to None.

        Raises:
            ValidationError: If required fields are missing or supplier product creation fails.
        """

        supplier = (
            product.seller_ids[0].partner_id if product.seller_ids else False
        )
        if not supplier:
            return
        supplier_id = supplier.trip_customer

        if not supplier_id:
            supplier_id = self.create_tripletex_supplier(
                supplier, base_url, headers, auth
            )
            supplier.with_context(skip_tripletex_sync=True).write(
                {"trip_customer": supplier_id}
            )

        payload = {
            "supplier": {"id": supplier_id},
            "number": product_data.get("number"),
            "name": product_data.get("name"),
            "resaleProduct": {"id": product_data.get("id")},
            "costExcludingVatCurrency": product_data.get("standard_price"),
        }

        if tripletex_uom_id:
            payload["productUnit"] = {"id": tripletex_uom_id}

        try:
            response = requests.post(
                f"{base_url}/product/supplierProduct",
                headers=headers,
                auth=auth,
                data=json.dumps(payload),
            )
            if response.status_code not in [200, 201]:
                _logger.warning(
                    f"[Tripletex SupplierProduct Error] Product {product.name} failed: {response.text}"
                )
        except Exception as e:
            _logger.warning(
                f"[Tripletex Exception] Error creating supplier product for {product.name}: {e}"
            )

    def create_tripletex_supplier(self, supplier, base_url, headers, auth):
        """
        Create or fetch a supplier in Tripletex based on the Odoo supplier record.

        This method:
        - Searches Tripletex for an existing supplier using the Odoo supplier's name.
        - If found, returns the existing Tripletex supplier ID.
        - If not found, builds the payload using the supplier's details and sends a POST request to Tripletex.
        - On successful creation, updates the supplier's `trip_customer` field in Odoo and returns the new Tripletex ID.

        Args:
            supplier (res.partner): Odoo partner record representing a supplier.
            base_url (str): Tripletex API base URL.
            headers (dict): HTTP request headers.
            auth (tuple): Tuple of (username/token, password/empty) for Tripletex auth.
        Raises:
            ValidationError: If the supplier creation fails or Tripletex returns an error.
        """
        try:
            # Try to find existing supplier in Tripletex by name
            search_response = requests.get(
                f"{base_url}/supplier",
                headers=headers,
                auth=auth,
                params={"name": supplier.name},
            )
            if search_response.status_code == 200:
                supplier_data = search_response.json().get("values", [])
                for entry in supplier_data:
                    if entry.get("name") == supplier.name:
                        return entry["id"]

            # Prepare payload for creating supplier in Tripletex
            payload = {
                "name": supplier.name,
                "isSupplier": bool(supplier.supplier_rank),
                "organizationNumber": supplier.vat or None,
                "email": supplier.email or None,
                "overdueNoticeEmail": supplier.alternate_email or None,
                "phoneNumber": supplier.phone or None,
                "phoneNumberMobile": supplier.mobile or None,
                "isPrivateIndividual": supplier.company_type != "company",
                "website": supplier.website or None,
                "description": supplier.comment or "",
                "physicalAddress": self.env[
                    "tripletex.customer.load"
                ].format_address(supplier, "business_"),
                "postalAddress": self.env[
                    "tripletex.customer.load"
                ].format_address(supplier),
            }

            # Send create request to Tripletex
            create_response = requests.post(
                f"{base_url}/supplier",
                headers=headers,
                auth=auth,
                data=json.dumps(payload),
            )

            if create_response.status_code in [200, 201]:
                trip_id = create_response.json()["value"]["id"]
                supplier.with_context(skip_tripletex_sync=True).write(
                    {"trip_customer": trip_id}
                )
                return trip_id
            else:
                raise ValidationError(
                    f"❌ Tripletex supplier creation failed: {create_response.status_code} - {create_response.text}"
                )

        except Exception as e:
            raise ValidationError(
                f"❌ Exception while creating supplier in Tripletex: {str(e)}"
            )

    def import_products_from_tripletex(self):
        """
        Imports products from Tripletex into Odoo.

        This method:
        - Retrieves product data from the Tripletex API.
        - For each product:
            - Checks and creates the supplier if not already present.
            - Checks and creates the Unit of Measure (UoM) if not already present.
            - Creates the product if it does not exist in Odoo.
            - Sets the product image from Tripletex if available.
            - Links the supplier as a vendor on the product if not already linked.

        Utilizes ThreadPoolExecutor to parallelize supplier and UoM enrichment for performance.
        """
        base_url, headers, auth, response = self.get_tripletex_data
        if response.status_code != 200:
            raise ValidationError(
                f"Failed to fetch products from Tripletex: {response.text}"
            )

        tripletex_products = json.loads(response.text).get("values", [])
        Product = self.env["product.template"].with_context(
            skip_tripletex_sync=True
        )
        Partner = self.env["res.partner"]
        AddressTool = self.env["tripletex.customer.load"]
        existing_codes = set(
            Product.search([("default_code", "!=", False)]).mapped(
                "default_code"
            )
        )

        def fetch_supplier_and_uom(t_product):
            """
            Fetch or create supplier and UoM based on Tripletex product data.
            Returns a dictionary with 'supplier', 'uom', and original product data.
            """
            result = {"product_data": t_product, "supplier": None, "uom": None}
            try:
                # Supplier logic
                supplier_data = t_product.get("supplier")
                supplier = None
                if supplier_data:
                    supplier_id = supplier_data.get("id")
                    supplier = Partner.search(
                        [("trip_customer", "=", supplier_id)], limit=1
                    )
                    if not supplier:
                        response = requests.get(
                            f"{base_url}/supplier/{supplier_id}",
                            headers=headers,
                            auth=auth,
                        )
                        if response.status_code in [200, 201]:
                            info = response.json().get("value")
                            vals = {
                                "name": info.get("name"),
                                "email": info.get("email"),
                                "vat": info.get("organizationNumber"),
                                "phone": info.get("phoneNumber"),
                                "mobile": info.get("phoneNumberMobile"),
                                "website": info.get("website"),
                                "comment": info.get("description"),
                                "trip_customer": supplier_id,
                                "company_type": "person"
                                if info.get("isPrivateIndividual")
                                else "company",
                                "supplier_rank": 1,
                            }
                            vals.update(
                                AddressTool.get_address_vals(
                                    info.get("postalAddress", {}).get("id")
                                    or 0
                                )
                            )
                            vals.update(
                                AddressTool.get_address_vals(
                                    info.get("physicalAddress", {}).get("id")
                                    or 0,
                                    prefix="business_",
                                )
                            )
                            supplier = Partner.create(vals)
                result["supplier"] = supplier

                # UoM logic
                uom_data = t_product.get("productUnit", {})
                if uom_data.get("url"):
                    uom_url = (
                        uom_data["url"]
                        if uom_data["url"].startswith("http")
                        else f"https://{uom_data['url']}"
                    )
                    response = requests.get(
                        uom_url, headers=headers, auth=auth
                    )
                    if response.status_code == 200:
                        u_data = response.json().get("value")
                        short_name = u_data.get("nameShort", "").lower()
                        common_code = self.env["uom.common.code"].search(
                            [("name", "=", u_data.get("commonCode"))], limit=1
                        )
                        uom = self.env["uom.uom"].search(
                            [("name", "=", short_name)], limit=1
                        )
                        if uom:
                            if uom.common_code != common_code:
                                uom.write(
                                    {
                                        "common_code": common_code.id
                                        if common_code
                                        else False
                                    }
                                )
                        else:
                            category = self.env["uom.category"].search(
                                [("name", "=", "General")], limit=1
                            )
                            if not category:
                                category = self.env["uom.category"].create(
                                    {"name": "General"}
                                )
                            uom = self.env["uom.uom"].create(
                                {
                                    "name": short_name,
                                    "category_id": category.id,
                                    "common_code": common_code.id
                                    if common_code
                                    else False,
                                }
                            )
                        result["uom"] = uom

            except Exception as e:
                _logger.warning(f"Failed to fetch supplier or UOM: {e}")
            return result

        # Parallel UoM + supplier fetch
        with ThreadPoolExecutor(max_workers=10) as executor:
            enriched_data = list(
                executor.map(fetch_supplier_and_uom, tripletex_products)
            )

        for entry in enriched_data:
            t_product = entry["product_data"]
            supplier = entry["supplier"]
            uom = entry["uom"]
            code = t_product.get("number")
            if not code:
                continue

            existing_product = Product.search(
                [("default_code", "=", code)], limit=1
            )
            if code not in existing_codes:
                image_base64 = None
                image_data = t_product.get("image")
                if image_data and image_data.get("id"):
                    image_base64 = self.fetch_tripletex_image_base64(
                        document_id=image_data["id"],
                        headers=headers,
                        auth=auth,
                    )

                vals = {
                    "name": t_product.get("name") or "Unnamed Product",
                    "default_code": code,
                    "active": not t_product.get("isInactive"),
                    "description_sale": t_product.get(
                        "orderLineDescription", ""
                    ),
                    "description": t_product.get("description", ""),
                    "standard_price": t_product.get("costExcludingVatCurrency")
                    or 0.0,
                    "list_price": t_product.get("priceExcludingVatCurrency")
                    or 0.0,
                    "tripletex_product": t_product.get("id"),
                }
                if uom:
                    vals["uom_id"] = uom.id
                if image_base64:
                    vals["image_1920"] = image_base64

                existing_product = Product.create(vals)
                existing_codes.add(code)

            # Add supplier info to product if not already present
            if (
                existing_product
                and supplier
                and not existing_product.seller_ids.filtered(
                    lambda s: s.partner_id == supplier
                )
            ):
                existing_product.write(
                    {
                        "seller_ids": [
                            (
                                0,
                                0,
                                {
                                    "partner_id": supplier.id,
                                    "min_qty": 1,
                                    "price": t_product.get(
                                        "costExcludingVatCurrency"
                                    )
                                    or 0.0,
                                    "delay": 1,
                                },
                            )
                        ]
                    }
                )

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
            raise ValidationError(
                "Failed to fetch products from Tripletex: %s" % response.text
            )

        tripletex_products = json.loads(response.text).get("values", [])
        for data in tripletex_products:
            data_id = data.get("id")
            product = self.env["product.template"].search(
                [("tripletex_product", "=", data_id)]
            )
            if not product:
                continue
            uom_id = False
            unit_data = data.get("productUnit") or data.get("unit")
            if unit_data and unit_data.get("url"):
                unit_url = unit_data["url"]
                full_unit_url = (
                    unit_url
                    if unit_url.startswith("http")
                    else f"https://{unit_url}"
                )
                try:
                    unit_response = requests.get(
                        full_unit_url, headers=headers, auth=auth
                    )
                    if unit_response.status_code == 200:
                        unit_val = unit_response.json().get("value")
                        short_name = unit_val.get("nameShort", "").lower()
                        common_code_name = unit_val.get("commonCode")
                        common_code = (
                            self.env["uom.common.code"].search(
                                [("name", "=", common_code_name)], limit=1
                            )
                            if common_code_name
                            else None
                        )
                        uom = self.env["uom.uom"].search(
                            [("name", "=", short_name)], limit=1
                        )
                        if not uom:
                            uom_category = self.env["uom.category"].search(
                                [("name", "=", "General")], limit=1
                            ) or self.env["uom.category"].create(
                                {"name": "General"}
                            )
                            uom = self.env["uom.uom"].create(
                                {
                                    "name": short_name,
                                    "category_id": uom_category.id,
                                    # 'uom_type': 'reference',
                                    "common_code": common_code.id
                                    if common_code
                                    else False,
                                }
                            )
                        uom_id = uom.id
                except Exception as e:
                    raise ValidationError("Failed to fetch UoM info: %s" % e)
            for prod in product:
                vals = {
                    "name": data.get("name") or "Unnamed Product",
                    "default_code": data.get("number"),
                    "active": not data.get("isInactive"),
                    "description_sale": data.get("orderLineDescription", ""),
                    "description": data.get("description", ""),
                    "standard_price": data.get("costExcludingVatCurrency")
                    or 0.0,
                    "list_price": data.get("priceExcludingVatCurrency") or 0.0,
                }

                if uom_id:
                    vals["uom_id"] = uom_id
                    vals["uom_po_id"] = uom_id

                image_data = data.get("image")
                if image_data and image_data.get("id"):
                    image_base64 = self.fetch_tripletex_image_base64(
                        document_id=image_data["id"],
                        headers=headers,
                        auth=auth,
                    )
                    if image_base64:
                        vals["image_1920"] = image_base64
                try:
                    prod.with_context(skip_tripletex_sync=True).write(vals)
                except (UserError, ValidationError) as e:
                    continue

            for item in tripletex_products:
                resale = item.get("resaleProduct")
                if resale and resale.get("id") == data.get("id"):
                    supplier_id = item.get("supplier", {}).get("id")
                    supplier_partner = False
                    if int(product.tripletex_product) == resale.get("id"):
                        if supplier_id:
                            supplier_partner = self.env["res.partner"].search(
                                [("trip_customer", "=", supplier_id)], limit=1
                            )
                        if not supplier_partner:
                            try:
                                supplier_response = requests.get(
                                    f"{base_url}/supplier/{supplier_id}",
                                    headers=headers,
                                    auth=auth,
                                )
                                if supplier_response.status_code == 200:
                                    supplier_info = (
                                        supplier_response.json().get("value")
                                    )
                                    supplier_vals = {
                                        "name": supplier_info.get("name"),
                                        "email": supplier_info.get("email"),
                                        "vat": supplier_info.get(
                                            "organizationNumber"
                                        ),
                                        "phone": supplier_info.get(
                                            "phoneNumber"
                                        ),
                                        "mobile": supplier_info.get(
                                            "phoneNumberMobile"
                                        ),
                                        "website": supplier_info.get(
                                            "website"
                                        ),
                                        "comment": supplier_info.get(
                                            "description"
                                        ),
                                        "trip_customer": supplier_id,
                                        "company_type": "person"
                                        if supplier_info.get(
                                            "isPrivateIndividual"
                                        )
                                        else "company",
                                        "supplier_rank": 1,
                                    }
                                    supplier_vals.update(
                                        self.env[
                                            "tripletex.customer.load"
                                        ].get_address_vals(
                                            supplier_info.get(
                                                "postalAddress", {}
                                            ).get("id")
                                            or 0,
                                            prefix="",
                                        )
                                    )
                                    supplier_vals.update(
                                        self.env[
                                            "tripletex.customer.load"
                                        ].get_address_vals(
                                            supplier_info.get(
                                                "physicalAddress", {}
                                            ).get("id")
                                            or 0,
                                            prefix="business_",
                                        )
                                    )
                                    supplier_partner = self.env[
                                        "res.partner"
                                    ].create(supplier_vals)
                            except Exception as e:
                                raise ValidationError(
                                    "Failed to fetch supplier: %s" % e
                                )

                        if supplier_partner:
                            cost = data.get("costExcludingVatCurrency") or 0.0
                            existing_vendor = product.seller_ids.filtered(
                                lambda s: s.partner_id.id
                                == supplier_partner.id
                            )
                            if existing_vendor:
                                existing_vendor.unlink()
                            tripletex_seller = self.env[
                                "product.supplierinfo"
                            ].create(
                                {
                                    "product_tmpl_id": product.id,
                                    "partner_id": supplier_partner.id,
                                    "min_qty": 1,
                                    "price": cost,
                                    "delay": 1,
                                    "sequence": 0,  # Make it appear first
                                }
                            )
                            for i, seller in enumerate(
                                product.seller_ids.filtered(
                                    lambda s: s.id != tripletex_seller.id
                                ),
                                start=1,
                            ):
                                seller.sequence = i

    # HELPER METHOD

    def convert_html_to_normal_text(self, raw_html):
        """
        Converts HTML content into plain text by stripping HTML tags.

        :param raw_html: HTML string
        :return: Plain text with tags removed and elements separated by newlines
        """
        soup = BeautifulSoup(raw_html or "", "html.parser")
        return soup.get_text(separator="\n")

    def fetch_tripletex_uom_map(self, base_url, headers, auth):
        """
        Fetches all UoMs from Tripletex and returns a dictionary mapping short name to Tripletex unit ID.

        :param base_url: Base API URL for Tripletex
        :param headers: HTTP headers for authentication
        :param auth: Authentication tuple (user, token/password)
        :return: Dictionary of {short_name: unit_id}
        """
        tripletex_uom = {}
        try:
            unit_response = requests.get(
                f"{base_url}/product/unit", headers=headers, auth=auth
            )
            if unit_response.status_code == 200:
                for unit in json.loads(unit_response.text).get("values", []):
                    short_name = unit.get("nameShort", "").lower()
                    if short_name:
                        tripletex_uom[short_name] = unit["id"]
        except Exception as e:
            _logger.warning(f"Failed to fetch UoMs from Tripletex: {e}")
        return tripletex_uom

    def fetch_tripletex_image_base64(self, document_id, headers, auth):
        """
        Downloads an image from Tripletex document storage and returns it as a base64 string.

        :param document_id: ID of the image document in Tripletex
        :param headers: HTTP headers for the request
        :param auth: Authentication tuple (user, token/password)
        :return: base64-encoded image string if successful, otherwise None
        """
        content_url = f"https://api-test.tripletex.tech/v2/document/{document_id}/content"
        _logger.warning("\n🌐 Fetching image from:", content_url)

        headers_for_binary = headers.copy()
        headers_for_binary["Accept"] = "application/octet-stream"

        try:
            image_response = requests.get(
                content_url, headers=headers_for_binary, auth=auth
            )
            if image_response.status_code in [200, 201]:
                _logger.warning("\n✅ Image fetched and encoded successfully.")
                return base64.b64encode(image_response.content).decode("utf-8")
            else:
                _logger.warning(
                    f"❌ Failed to download image. Status: {image_response.status_code}, Reason: {image_response.text}"
                )
                return None
        except Exception as e:
            _logger.warning(
                f"❌ Exception while fetching image from Tripletex for document {document_id}: {e}"
            )
            return None
