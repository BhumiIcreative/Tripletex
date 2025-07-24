from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError
from bs4 import BeautifulSoup
import logging

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = "product.template"
    _description = "Product Template"

    tripletex_product = fields.Char(string="Tripletex Id", copy=False)

    def get_tripletex_connection(self):
        """
        This method fetches the Tripletex base URL and authentication token
        stored in Odoo, and prepares the required headers and auth tuple for use
        in Tripletex API calls.

        Raises:
            ValidationError: If the base URL or token is missing.
        """
        Param = self.env["ir.config_parameter"].sudo()
        base_url = Param.get_param("ak_tripletex_integration.base_url")
        token_record = self.env["tripletex.session.token"].search([], limit=1)

        if not base_url or not token_record or not token_record.token:
            raise ValidationError(_("Tripletex Base URL or Token is missing."))

        auth = ("0", token_record.token)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return base_url, headers, auth

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override of create() to sync newly created products with Tripletex.

        This method checks whether product creation should be skipped via context,
        then creates the product in Odoo and attempts to push each new product to Tripletex.

        - Fetches UoMs from Tripletex once to reuse across multiple products.
        - For each product, builds a payload including name, code, tax, UoM, pricing, and stock info.
        - Sends the product to Tripletex's `/product` endpoint.
        - On success, stores the Tripletex product ID and pushes product image and supplierProduct info.

        Context Flags:
            - skip_tripletex_sync: If True, Tripletex sync is skipped entirely.

        Raises:
            ValidationError: On Tripletex request failure or data issue.
        """
        if self.env.context.get("skip_tripletex_sync"):
            return super().create(vals_list)

        products = super().create(vals_list)
        base_url, headers, auth = self.get_tripletex_connection()

        # Fetch Tripletex UoMs once to reuse
        tripletex_uom_map = {}
        try:
            response = requests.get(
                f"{base_url}/product/unit", headers=headers, auth=auth
            )
            if response.ok:
                tripletex_uom_map = {
                    unit.get("nameShort", "").lower(): unit["id"]
                    for unit in response.json().get("values", [])
                    if unit.get("nameShort")
                }
        except Exception as e:
            raise ValidationError(f"Failed to fetch UoMs from Tripletex: {e}")

        for vals, product in zip(vals_list, products):
            if not vals.get("default_code"):
                continue  # Skip if product has no internal reference

            # --- Tax Handling
            tax_ids = vals.get("taxes_id", [])
            tax_record = None
            tripletex_tax_id = None
            if (
                tax_ids
                and isinstance(tax_ids[0], list)
                and len(tax_ids[0]) > 1
            ):
                tax_record = self.env["account.tax"].browse(tax_ids[0][1])
                tripletex_tax_id = (
                    tax_record.tripletex_tax if tax_record else None
                )

            # --- UoM Handling
            uom = product.uom_id
            (
                tripletex_uom_id,
                tripletex_uom_map,
            ) = self._get_or_create_tripletex_uom_id(
                uom, base_url, headers, auth, tripletex_uom_map
            )

            # --- Payload Construction
            payload = {
                "name": vals.get("name"),
                "number": vals.get("default_code"),
                "isInactive": not vals.get("active"),
                "costExcludingVatCurrency": vals.get("standard_price", 0),
                "priceExcludingVatCurrency": vals.get("list_price", 0),
                "isStockItem": vals.get("detailed_type") == "product",
            }

            if tripletex_tax_id:
                payload["vatType"] = {
                    "id": tripletex_tax_id,
                    "percentage": tax_record.amount if tax_record else None,
                }

            if tripletex_uom_id:
                payload["productUnit"] = {"id": tripletex_uom_id}

            try:
                response = requests.post(
                    f"{base_url}/product",
                    headers=headers,
                    auth=auth,
                    data=json.dumps(payload),
                )
            except Exception as e:
                raise ValidationError(
                    f"Failed to create product in Tripletex: {e}"
                )

            if response.status_code in [200, 201]:
                product_data = response.json().get("value", {})
                product.tripletex_product = product_data.get("id")

                # Upload image to Tripletex
                self.env[
                    "tripletex.product.map"
                ].upload_odoo_image_to_tripletex(
                    product, product_data.get("id"), base_url, headers, auth
                )

                # Create supplierProduct
                try:
                    self.env[
                        "tripletex.product.map"
                    ].create_tripletex_supplier_product(
                        product,
                        product_data,
                        base_url,
                        headers,
                        auth,
                        tripletex_uom_id,
                    )
                    _logger.warning(
                        f"SupplierProduct created for product '{product.name}'"
                    )
                except Exception as e:
                    _logger.warning(
                        f"Failed to create supplierProduct for '{product.name}' in Tripletex: {e}"
                    )
            else:
                raise ValidationError(
                    f"Tripletex product creation failed: {response.text}"
                )

        return products

    def write(self, vals):
        """
        Override of write method to sync updated product data with Tripletex.

        This method automatically updates corresponding product information in Tripletex
        when a product is updated in Odoo and already linked (has `tripletex_product`).
        It includes the following:
            - Updates product fields like name, prices, and UoM.
            - Updates descriptions after converting HTML to plain text.
            - Syncs tax (vatType) and active status.
            - Replaces any existing supplierProduct records in Tripletex.
            - Creates or updates supplierProduct entry with new supplier info.
            - Re-uploads product image if `image_1920` exists.

        Skips Tripletex sync if context contains `skip_tripletex_sync`.
        Raises:
            ValidationError: If any Tripletex API call fails (product or supplier sync).
        """
        _logger.warning("\n--------product-------write")
        if self.env.context.get("skip_tripletex_sync"):
            return super().write(vals)

        res = super().write(vals)

        if any(self.mapped("tripletex_product")):
            base_url, headers, auth, _ = self.env[
                "tripletex.product.map"
            ].get_tripletex_data
            tripletex_uom = {}
            try:
                response = requests.get(
                    f"{base_url}/product/unit", headers=headers, auth=auth
                )
                if response.ok:
                    tripletex_uom = {
                        unit.get("nameShort", "").lower(): unit["id"]
                        for unit in response.json().get("values", [])
                        if unit.get("nameShort")
                    }
            except Exception as e:
                _logger.warning(f"Failed to fetch UoMs from Tripletex: {e}")

            for product in self.filtered("tripletex_product"):
                data = {}
                field_map = {
                    "name": "name",
                    "standard_price": "costExcludingVatCurrency",
                    "list_price": "priceExcludingVatCurrency",
                    "default_code": "number",
                }
                data.update(
                    {
                        trip_key: vals[field]
                        for field, trip_key in field_map.items()
                        if field in vals
                    }
                )

                description_map = {
                    "description": "description",
                    "description_sale": "orderLineDescription",
                }
                data.update(
                    {
                        trip_key: self.convert_html_to_normal_text(vals[field])
                        for field, trip_key in description_map.items()
                        if field in vals
                    }
                )

                uom = (
                    self.env["uom.uom"].browse(vals.get("uom_id"))
                    if vals.get("uom_id")
                    else product.uom_id
                )
                (
                    trip_uom_id,
                    tripletex_uom,
                ) = self._get_or_create_tripletex_uom_id(
                    uom, base_url, headers, auth, tripletex_uom
                )

                if "active" in vals:
                    data["isInactive"] = not vals["active"]

                if trip_uom_id:
                    data["productUnit"] = {"id": trip_uom_id}

                tax = next(
                    (t for t in product.taxes_id if t.tripletex_tax), None
                )
                data["vatType"] = {
                    "id": tax.tripletex_tax if tax else None,
                    "percentage": tax.amount if tax else None,
                }
                trip_supplier_id = None
                supplier = next(
                    (
                        s.partner_id
                        for s in product.seller_ids.sorted("sequence")
                        if s.partner_id
                    ),
                    None,
                )
                if supplier:
                    if not supplier.trip_customer:
                        try:
                            self.env[
                                "tripletex.product.map"
                            ].create_tripletex_supplier(
                                supplier, base_url, headers, auth
                            )
                        except Exception as e:
                            _logger.warning(
                                f"Tripletex supplier creation failed: {e}"
                            )
                    trip_supplier_id = supplier.trip_customer

                    try:
                        supplier_response = requests.get(
                            f"{base_url}/product/supplierProduct",
                            headers=headers,
                            auth=auth,
                            params={"product.id": product.tripletex_product},
                        )
                        if supplier_response.ok:
                            for s in supplier_response.json().get(
                                "values", []
                            ):
                                if s.get("id"):
                                    requests.delete(
                                        f"{base_url}/product/supplierProduct/{s['id']}",
                                        headers=headers,
                                        auth=auth,
                                    )
                    except Exception as e:
                        _logger.warning(
                            f" Failed to delete old supplierProducts for product '{product.name}': {e}"
                        )

                if data:
                    try:
                        response = requests.put(
                            url=f"{base_url}/product/{product.tripletex_product}",
                            headers=headers,
                            auth=auth,
                            data=json.dumps(data),
                        )
                        if response.status_code == 404:
                            product.write({"tripletex_product": False})
                        elif not response.ok:
                            raise ValidationError(
                                _(
                                    "Error updating Product in Tripletex: {}"
                                ).format(response.text or "Unknown error")
                            )
                        if product.image_1920 and product.tripletex_product:
                            tripletex_product_id = product.tripletex_product
                            self.env[
                                "tripletex.product.map"
                            ].upload_odoo_image_to_tripletex(
                                product,
                                tripletex_product_id,
                                base_url,
                                headers,
                                auth,
                            )
                    except Exception as e:
                        raise ValidationError(str(e))

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
                            data=json.dumps(supplier_payload),
                        )
                        if supplier_product_response.status_code == 404:
                            _logger.warning(
                                f" SupplierProduct creation failed (404), retrying after creating supplier: {supplier_product_response.text}"
                            )
                            try:
                                self.env[
                                    "tripletex.product.map"
                                ].create_tripletex_supplier(
                                    supplier, base_url, headers, auth
                                )
                            except Exception as e:
                                _logger.warning(
                                    f" Retry supplier creation failed: {e}"
                                )
                                raise ValidationError(
                                    _(
                                        "Failed to re-create missing supplier in Tripletex: {}"
                                    ).format(e)
                                )
                            trip_supplier_id = supplier.trip_customer
                            supplier_payload["supplier"] = {
                                "id": trip_supplier_id
                            }
                            retry_response = requests.post(
                                f"{base_url}/product/supplierProduct",
                                headers=headers,
                                auth=auth,
                                data=json.dumps(supplier_payload),
                            )
                            if retry_response.status_code not in [200, 201]:
                                raise ValidationError(
                                    _(
                                        "Retry failed: SupplierProduct creation error in Tripletex: {}"
                                    ).format(retry_response.text)
                                )
                        elif supplier_product_response.status_code not in [
                            200,
                            201,
                        ]:
                            _logger.warning(
                                f" SupplierProduct creation failed: {supplier_product_response.text}"
                            )
                    except Exception as e:
                        _logger.warning(
                            f"Failed to create supplierProduct in Tripletex: {e}"
                        )

        return res

    def unlink(self):
        """
        Deletes the product in Tripletex when the corresponding product is deleted in Odoo.

        Steps:
        - For each product, check if a Tripletex product ID exists.
        - If yes, send a DELETE request to Tripletex to remove the product.
        - If the Tripletex deletion fails, raise a ValidationError to prevent unlinking in Odoo.
        - If Tripletex returns 404 (not found), clear the Tripletex ID in Odoo.
        - If Tripletex returns 204 (success), proceed with the unlink.

        Raises:
            ValidationError: If Tripletex product deletion fails.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        if not base_url or not password:
            return super().unlink()

        for product in self:
            if product.tripletex_product:
                try:
                    response = requests.delete(
                        url=f"{base_url}/product/{product.tripletex_product}",
                        auth=("0", password),
                        headers=headers,
                    )
                    if response.status_code == 404:
                        product.write({"tripletex_product": False})
                    elif response.status_code == 204:
                        _logger.warning(
                            f"Deleted Tripletex product: {product.tripletex_product}"
                        )
                    else:
                        raise ValidationError(
                            _("Error deleting product in Tripletex: %s")
                            % response.text
                        )
                except Exception as e:
                    raise ValidationError(
                        _("Tripletex deletion failed: %s") % str(e)
                    )

        return super().unlink()

    # HELPER METHODS
    def convert_html_to_normal_text(self, raw_html):
        """
        Convert an HTML string into plain text.

        This method strips all HTML tags using BeautifulSoup
        and returns only the plain text content.
        Returns:
            str: Cleaned plain text with line breaks between block elements.
        """
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        return soup.get_text(separator="\n")

    def _get_or_create_tripletex_uom_id(
        self, uom, base_url, headers, auth, tripletex_uom_map
    ):
        """
        Ensure that a given UoM (Unit of Measure) exists in Tripletex.

        - If UoM already exists in the provided map, return its Tripletex ID.
        - If not, attempt to create the UoM in Tripletex using the common code.
        - After creation, refresh the UoM list and return the updated ID and map.

        Raises:
            ValidationError: If UoM creation fails or common code is missing.
        """
        if not uom:
            return None, tripletex_uom_map

        uom_key = uom.name.lower()
        if tripletex_uom_id := tripletex_uom_map.get(uom_key):
            return tripletex_uom_id, tripletex_uom_map

        if not uom.common_code:
            raise ValidationError(
                _(
                    f"Missing or invalid Tripletex common code for UoM '{uom.name}'. "
                    "Please assign a valid numeric common code."
                )
            )

        # Create UoM in Tripletex
        self.env["tripletex.product.map"].create_tripletex_uom(
            base_url, headers, auth, uom
        )

        # Refresh the map after creation
        try:
            response = requests.get(
                f"{base_url}/product/unit", headers=headers, auth=auth
            )
            if response.ok:
                new_map = {
                    unit.get("nameShort", "").lower(): unit["id"]
                    for unit in response.json().get("values", [])
                    if unit.get("nameShort")
                }
                return new_map.get(uom_key), new_map
            raise ValidationError(
                "Failed to refresh UoM list from Tripletex after creation."
            )
        except Exception as e:
            raise ValidationError(
                f"Failed to re-fetch UoMs from Tripletex: {e}"
            )
