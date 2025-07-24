from odoo import models, _
import requests
import json
from odoo.exceptions import ValidationError
from concurrent.futures import ThreadPoolExecutor
import logging

_logger = logging.getLogger(__name__)


class TripletexSupplierSync(models.TransientModel):
    _name = "tripletex.supplier.sync"
    _description = "Tripletex Supplier Sync"

    def supplier_sync(self):
        """
        Updates existing suppliers in Odoo based on data from Tripletex (no creation).

        - Fetches suppliers from Tripletex via `/supplier` API.
        - Updates matching `res.partner` records by `trip_customer` ID.
        - Skips any suppliers not already in Odoo.
        - Sets `supplier_rank = 1` for all updated suppliers.

        Raises:
            ValidationError: If Tripletex API credentials are missing or the request fails.
        """
        base_url, headers, password = self.env["tripletex.customer.load"].customer_data_get_from_tripletex()

        if not base_url or not password:
            raise ValidationError(_("Tripletex base URL or token not configured."))

        try:
            response = requests.get(
                f"{base_url}/supplier", headers=headers, auth=("0", password)
            )
            response.raise_for_status()
        except Exception as e:
            raise ValidationError(_("Tripletex connection failed: %s") % str(e))

        suppliers = response.json().get("values", [])
        Partner = self.env["res.partner"].with_context(skip_tripletex_sync=True)
        Loader = self.env["tripletex.customer.load"]

        for supplier in suppliers:
            supplier_id = supplier.get("id")
            if not supplier_id:
                continue

            # Find existing supplier in Odoo
            existing_supplier = Partner.search([("trip_customer", "=", supplier_id)], limit=1)
            if not existing_supplier:
                _logger.info(f"Skipping Tripletex supplier ID {supplier_id} — not found in Odoo.")
                continue

            try:
                partner_vals = {
                    "name": supplier.get("name"),
                    "email": supplier.get("email"),
                    "supplier_number": supplier.get("supplierNumber", ""),
                    "active": not supplier.get("isInactive"),
                    "vat": supplier.get("organizationNumber"),
                    "phone": supplier.get("phoneNumber"),
                    "mobile": supplier.get("phoneNumberMobile"),
                    "website": supplier.get("website"),
                    "comment": supplier.get("description"),
                    "company_type": "person" if supplier.get("isPrivateIndividual") else "company",
                    "supplier_rank": 1,
                }

                # Add address fields
                partner_vals.update(
                    Loader.get_address_vals(supplier.get("postalAddress", {}).get("id") or 0, prefix="")
                )
                partner_vals.update(
                    Loader.get_address_vals(supplier.get("physicalAddress", {}).get("id") or 0, prefix="business_")
                )

                existing_supplier.write(partner_vals)

            except Exception as e:
                _logger.error(f"Supplier update failed for ID {supplier_id}: {e}")

    def prepare_url_for_data_get(self):
        """
        Fetch all suppliers from Tripletex using paginated API.
        Raises:
            ValidationError: If there's an error connecting to Tripletex.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()

        try:
            # First request to get total number of results
            initial_response = requests.get(
                f"{base_url}/supplier", headers=headers, auth=("0", password)
            )
            initial_response.raise_for_status()
            full_result_length = initial_response.json().get(
                "fullResultSize", 0
            )

            # Second request to fetch all supplier data
            response = requests.get(
                f"{base_url}/supplier?from=0&count={full_result_length}",
                headers=headers,
                auth=("0", password),
            )
            response.raise_for_status()
            return response

        except requests.RequestException as e:
            raise ValidationError(
                _("Failed to fetch supplier data: %s") % str(e)
            )

    def import_supplier_from_tripletex(self):
        """
        Imports suppliers from Tripletex into Odoo using multithreading.

        - Retrieves supplier data from Tripletex.
        - For each supplier:
            - Updates an existing `res.partner` record if found via `trip_customer`.
            - Otherwise, creates a new `res.partner` with the mapped data.
        - Includes address data from postal and physical addresses.
        - Improves performance using a thread pool for concurrent processing.

        Raises:
            ValidationError: If the Tripletex API call fails.
        """
        response = self.prepare_url_for_data_get()
        if not response or response.status_code != 200:
            raise ValidationError(_("Failed to fetch data from Tripletex."))

        supplier_data = response.json().get("values", [])
        if not supplier_data:
            return

        address_loader = self.env["tripletex.customer.load"]

        def process_supplier(data):
            trip_id = data.get("id")
            if not trip_id:
                return

            postal_vals = address_loader.get_address_vals(
                data.get("postalAddress", {}).get("id") or 0
            )
            business_vals = address_loader.get_address_vals(
                data.get("physicalAddress", {}).get("id") or 0, "business_"
            )

            vals = {
                "name": data.get("name"),
                "email": data.get("email", ""),
                "supplier_number": data.get("supplierNumber", ""),
                "phone": data.get("phoneNumber", ""),
                "mobile": data.get("phoneNumberMobile", ""),
                "active": not data.get("isInactive"),
                "is_company": not data.get("isPrivateIndividual"),
                "company_type": "company"
                if not data.get("isPrivateIndividual")
                else "person",
                "trip_customer": trip_id,
                "supplier_rank": 1 if data.get("isSupplier") else 0,
                "customer_rank": 1 if data.get("isCustomer") else 0,
                "vat": data.get("organizationNumber") or "",
                "website": data.get("website", ""),
                "comment": data.get("description") or "",
                **postal_vals,
                **business_vals,
            }

            partner = self.env["res.partner"].search(
                [("trip_customer", "=", trip_id)], limit=1
            )
            if partner:
                partner.with_context(skip_tripletex_sync=True).write(vals)
            else:
                self.env["res.partner"].with_context(
                    skip_tripletex_sync=True
                ).sudo().create(vals)

        # Use ThreadPoolExecutor to run supplier processing in parallel
        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(process_supplier, supplier_data)

    def import_supplier_from_odoo(self):
        """
        Export eligible Odoo supplier partners to Tripletex using multithreading.

        - Fetches all `res.partner` records with `supplier_rank > 0`.
        - Loads existing Tripletex supplier IDs to avoid exporting duplicates.
        - For each supplier that has no `trip_customer` or whose ID is not in Tripletex:
            - Constructs a supplier data dictionary.
            - Sends it to Tripletex using `create_customer_in_tripletex`.

        Optimized with ThreadPoolExecutor for concurrent processing.
        """
        # Get Tripletex supplier IDs
        response = self.prepare_url_for_data_get()
        external_numbers = (
            {
                int(d["id"])
                for d in json.loads(response.text).get("values", [])
                if d.get("id")
            }
            if response and response.status_code == 200
            else set()
        )

        # Fetch local Odoo suppliers
        suppliers = (
            self.env["res.partner"]
            .with_context(active_test=False)
            .search([("supplier_rank", ">", 0)])
        )

        address_loader = self.env["tripletex.customer.load"]

        def process_supplier(supplier):
            # Check if already synced
            if (
                supplier.trip_customer
                and int(supplier.trip_customer) in external_numbers
            ):
                return

            data = {
                "name": supplier.name,
                "isSupplier": True,
                "organizationNumber": supplier.vat or None,
                "isInactive": not supplier.active,
                "email": supplier.email or None,
                "overdueNoticeEmail": supplier.alternate_email or None,
                "phoneNumber": supplier.phone or None,
                "phoneNumberMobile": supplier.mobile or None,
                "isPrivateIndividual": supplier.company_type != "company",
                "website": supplier.website or None,
                "description": supplier.comment or "",
                "physicalAddress": address_loader.format_address(
                    supplier, "business_"
                ),
                "postalAddress": address_loader.format_address(supplier),
            }

            supplier.create_customer_in_tripletex(data, supplier)

        # Multithreading
        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(process_supplier, suppliers)
