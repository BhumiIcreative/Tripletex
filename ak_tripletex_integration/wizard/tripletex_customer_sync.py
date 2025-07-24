from odoo import models, _
import requests
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from odoo.api import Environment
from odoo.exceptions import ValidationError
from bs4 import BeautifulSoup
import logging

_logger = logging.getLogger(__name__)


class TripletexCustomerDataLoad(models.TransientModel):
    _name = "tripletex.customer.load"
    _description = "Tripletex Customer Data Load"

    def customer_data_get_from_tripletex(self):
        """
        Retrieve Tripletex API connection details required for authenticated requests.

        This method:
        - Fetches the Tripletex base URL from `ir.config_parameter`
        - Retrieves the current session token from the `tripletex.session.token` model
        - Prepares standard headers for JSON API requests

        Raises:
            ValidationError: If the base URL or session token is missing from configuration.
        """
        base_url = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("ak_tripletex_integration.base_url")
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        password = (
            self.env["tripletex.session.token"].search([], limit=1).token
        )

        if not base_url:
            raise ValidationError(
                _(
                    "Error: Base URL not configured. Please set 'ak_tripletex_integration.base_url' in the config parameters."
                )
            )
        if not password:
            raise ValidationError(_("Error: Authentication token not found."))

        return base_url, headers, password

    def customer_data_get(self):
        """
        Fetches all customers from Tripletex and updates them in Odoo.
        Runs sequentially (no threading).
        """
        base_url, headers, password = self.customer_data_get_from_tripletex()

        try:
            response = requests.get(
                f"{base_url}/customer", headers=headers, auth=("0", password)
            )
            response.raise_for_status()
        except Exception as e:
            raise ValidationError(_("Tripletex connection failed: %s") % str(e))

        customers = response.json().get("values", [])
        Partner = self.env["res.partner"].with_context(skip_tripletex_sync=True)

        for customer in customers:
            partner_vals = {
                "name": customer.get("name"),
                "email": customer.get("email"),
                "vat": customer.get("organizationNumber"),
                "phone": customer.get("phoneNumber"),
                "mobile": customer.get("phoneNumberMobile"),
                "website": customer.get("website"),
                "customer_number": customer.get("customerNumber"),
                "comment": customer.get("description"),
                "trip_customer": customer.get("id"),
                "company_type": "person" if customer.get("isPrivateIndividual") else "company",
                "due_date": customer.get("invoicesDueIn"),
                "send_invoice_via": customer.get("invoiceSendMethod"),
                "is_single_customer_invoice": customer.get("singleCustomerInvoice"),
                "is_soft_reminder": customer.get("isAutomaticSoftReminderEnabled"),
                "is_reminder": customer.get("isAutomaticReminderEnabled"),
                "is_notice_od_debt": customer.get("isAutomaticNoticeOfDebtCollectionEnabled"),
                "invoices_due_in_type": customer.get("invoicesDueInType", "DAYS"),
                "email_attachment_type": customer.get("emailAttachmentType"),
                "discountPercentage": customer.get("discountPercentage"),
            }
            # Add address values from Tripletex
            partner_vals.update(
                self.get_address_vals(customer.get("postalAddress", {}).get("id") or 0, prefix="")
            )
            partner_vals.update(
                self.get_address_vals(customer.get("physicalAddress", {}).get("id") or 0, prefix="business_")
            )

            # Update the partner in Odoo
            partner = Partner.search([("trip_customer", "=", customer.get("id"))], limit=1)
            if partner:
                partner.write(partner_vals)

    def import_customer_from_tripletex(self):
        """
        Imports customers from Tripletex into Odoo if they do not already exist.

        - Checks for existing customers by matching `trip_customer`.
        - Only creates new customers; does NOT update existing ones.
        - Uses multithreading for faster imports.
        - Automatically sets `skip_tripletex_sync` context to avoid duplicate sync back to Tripletex.
        - Populates postal and business addresses via helper methods.

        Raises:
            ValidationError: If API call fails.
        """
        response = self.prepare_url_for_data_get()
        if not response or response.status_code != 200:
            raise ValidationError(_("Failed to fetch data from Tripletex."))

        all_customers = json.loads(response.text).get("values", [])

        def process_customer(data):
            trip_id = data.get("id")
            if not trip_id:
                return

            with self.pool.cursor() as cr:
                env = Environment(cr, self.env.uid, self.env.context)

                # Skip if already exists
                if env["res.partner"].search(
                    [("trip_customer", "=", trip_id)], limit=1
                ):
                    return

                postal_vals = self.get_address_vals(
                    data.get("postalAddress", {}).get("id") or 0
                )
                business_vals = self.get_address_vals(
                    data.get("physicalAddress", {}).get("id") or 0,
                    prefix="business_",
                )

                vals = {
                    "name": data.get("name"),
                    "email": data.get("email", ""),
                    "phone": data.get("phoneNumber", ""),
                    "mobile": data.get("phoneNumberMobile", ""),
                    "email_attachment_type": data.get(
                        "emailAttachmentType", ""
                    ),
                    "is_company": not data.get("isPrivateIndividual"),
                    "customer_number": data.get("customerNumber", ""),
                    "company_type": "company"
                    if not data.get("isPrivateIndividual")
                    else "person",
                    "trip_customer": trip_id,
                    "supplier_rank": 1 if data.get("isSupplier") else 0,
                    "customer_rank": 1 if data.get("isCustomer") else 0,
                    "vat": data.get("organizationNumber") or "",
                    "alternate_email": data.get("overdueNoticeEmail", ""),
                    "website": data.get("website", ""),
                    "due_date": data.get("invoicesDueIn"),
                    "is_single_customer_invoice": data.get(
                        "singleCustomerInvoice", False
                    ),
                    "is_soft_reminder": data.get(
                        "isAutomaticSoftReminderEnabled", False
                    ),
                    "is_reminder": data.get(
                        "isAutomaticReminderEnabled", False
                    ),
                    "is_notice_od_debt": data.get(
                        "isAutomaticNoticeOfDebtCollectionEnabled", False
                    ),
                    "discountPercentage": data.get("discountPercentage") or 0,
                    "comment": data.get("description") or "",
                    **postal_vals,
                    **business_vals,
                }

                env["res.partner"].with_context(
                    skip_tripletex_sync=True
                ).sudo().create(vals)
                cr.commit()

        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(process_customer, all_customers)

    def import_customer_from_odoo(self):
        """
        Imports Odoo customers to Tripletex using threading for efficiency.

        - Fetches existing customers from Tripletex to avoid duplicates.
        - Prepares and sends data to Tripletex for partners not yet synced.
        - Uses multithreading to speed up export of many customers.

        Raises:
            ValidationError: If a customer lacks required fields like email.
        """
        response = self.prepare_url_for_data_get()
        existing_ids = set()

        if response and response.status_code == 200:
            existing_ids = {
                int(item["id"])
                for item in json.loads(response.text).get("values", [])
                if item.get("id")
            }

        customers = self.env["res.partner"].search([("customer_rank", ">", 0)])

        def process_customer(customer):
            trip_id = int(customer.trip_customer or 0)
            if not customer.trip_customer or (
                trip_id not in existing_ids and customer.customer_rank
            ):
                if not customer.email:
                    raise ValidationError(
                        _("Customer '%s' has no email.") % customer.name
                    )

                data = {
                    "name": customer.name,
                    "isSupplier": bool(customer.supplier_rank),
                    "organizationNumber": customer.vat or None,
                    "email": customer.email,
                    "overdueNoticeEmail": customer.alternate_email or None,
                    "phoneNumber": customer.phone or None,
                    "phoneNumberMobile": customer.mobile or None,
                    "isPrivateIndividual": customer.company_type == "person",
                    "website": customer.website or None,
                    "invoicesDueIn": customer.due_date,
                    "invoicesDueInType": customer.invoices_due_in_type,
                    "emailAttachmentType": customer.email_attachment_type,
                    "singleCustomerInvoice": customer.is_single_customer_invoice,
                    "isAutomaticSoftReminderEnabled": customer.is_soft_reminder,
                    "isAutomaticReminderEnabled": customer.is_reminder,
                    "isAutomaticNoticeOfDebtCollectionEnabled": customer.is_notice_od_debt,
                    "discountPercentage": customer.discountPercentage,
                    "invoiceEmail": customer.email,
                    "description": self.convert_html_to_normal_text(
                        customer.comment or ""
                    ),
                    "physicalAddress": self.format_address(
                        customer, "business_"
                    ),
                    "postalAddress": self.format_address(customer),
                }

                self.env["res.partner"].create_customer_in_tripletex(
                    data, customer
                )

        # Run customer processing in threads
        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(process_customer, customers)

    # HELPER METHOD
    def get_country_id_from_tripletex(self, tripletex_country_id):
        """
        Retrieve the Odoo country ID based on a Tripletex country ID.

        This method:
        - Sends a GET request to the Tripletex API to fetch country details
        - Extracts the alpha-2 country code (`alpha2Code`) from the Tripletex response
        - Searches for a matching record in `res.country` using the alpha-2 code
        - Returns the ID of the matching `res.country` record, or False if not found

        Raises:
            ValidationError: If the API request fails or an exception occurs.
        """
        if not tripletex_country_id:
            return False
        base_url, headers, password = self.customer_data_get_from_tripletex()
        try:
            response = requests.get(
                f"{base_url}/country/{tripletex_country_id}",
                headers=headers,
                auth=("0", password),
            )
            if response.status_code == 200:
                alpha2 = response.json().get("value", {}).get("alpha2Code")
                if alpha2:
                    country = self.env["res.country"].search(
                        [("code", "=", alpha2)], limit=1
                    )
                    return country.id if country else False
            else:
                _logger.warning(
                    "Tripletex country fetch failed: [%s] %s",
                    response.status_code,
                    response.text,
                )

        except Exception as e:
            raise ValidationError(
                _("Error while fetching country from Tripletex: %s") % str(e)
            )

        return False

    def convert_html_to_normal_text(self, raw_html):
        """
        Converts raw HTML content into plain text by stripping tags.

        This method uses BeautifulSoup to parse and extract readable text
        from HTML, maintaining line breaks between block elements.

        """
        soup = BeautifulSoup(raw_html or "", "html.parser")
        return soup.get_text(separator="\n")

    def format_address(self, customer, prefix=""):
        """
        Formats an Odoo partner's address fields into the Tripletex-compatible address format.
        Example:
            format_address(partner, 'business_') → {
                "addressLine1": "Some Street",
                "addressLine2": "Suite 101",
                "postalCode": "12345",
                "city": "Oslo",
                "country": {"id": 200}
            }
        """
        country_id = getattr(customer, f"{prefix}country_id", False)
        country_code = country_id.code if country_id else ""
        country_trip_id = (
            self.env["res.partner"].get_country_id(country_code)
            if country_code
            else None
        )

        return {
            "addressLine1": getattr(customer, f"{prefix}street", ""),
            "addressLine2": getattr(customer, f"{prefix}street2", ""),
            "postalCode": getattr(customer, f"{prefix}zip", ""),
            "city": getattr(customer, f"{prefix}city", ""),
            "country": {"id": country_trip_id} if country_trip_id else None,
        }

    def get_address_vals(self, address_id, prefix=""):
        """
        Retrieves and maps Tripletex address data to Odoo partner address fields.

        This method:
        - Fetches the address details from Tripletex by `address_id`
        - Extracts the country data and maps it to Odoo's `res.country`
        - Builds a dictionary of address values with optional prefixing for nested/related addresses
        """
        address = self.fetch_address_data(address_id)
        if not address or not address.get("value"):
            return {}

        val = address.get("value")
        country_data = (
            self.get_country_data(val.get("country", {}).get("id"))
            if val.get("country")
            else {}
        )

        return {
            f"{prefix}street": val.get("addressLine1"),
            f"{prefix}street2": val.get("addressLine2"),
            f"{prefix}zip": val.get("postalCode", ""),
            f"{prefix}city": val.get("city", ""),
            f"{prefix}country_id": (
                self.env["res.country"]
                .search(
                    [("code", "=", country_data.get("iso_alpha2_code", ""))],
                    limit=1,
                )
                .id
                if prefix
                else country_data.get("id")
            ),
            f"{prefix}country_code": country_data.get("iso_alpha2_code", "")
            if prefix
            else "",
        }

    def get_country_data(self, country_id):
        """
        Fetches country details from Tripletex and maps them to an Odoo country record.
        Raises:
            ValidationError: If the authentication token is missing or the API call fails.
        """
        if not country_id:
            return {}
        base_url, headers, password = self.customer_data_get_from_tripletex()
        url = f"{base_url}/country/{country_id}"
        try:
            response = requests.get(url, headers=headers, auth=("0", password))
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValidationError(
                _("Failed to fetch country data from Tripletex: %s") % str(e)
            )
        data = response.json().get("value", {})
        iso_code = data.get("isoAlpha2Code", "")
        return {
            "name": data.get("name", ""),
            "iso_alpha2_code": iso_code,
            "id": self.env["res.country"]
            .search([("code", "=", iso_code)], limit=1)
            .id,
        }

    def fetch_address_data(self, address_id):
        """
        Fetches address data from Tripletex using the given address ID.
        Raises:
            ValidationError: If authentication token is missing or if the request fails.
        """
        token = self.env["tripletex.session.token"].search([], limit=1).token
        if not token:
            raise ValidationError(_("Error: Authentication token not found."))

        base_url = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("ak_tripletex_integration.base_url")
        )
        url = f"{base_url}/address/{address_id}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = requests.get(url, headers=headers, auth=("0", token))
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValidationError(
                _("Tripletex address fetch failed: %s") % str(e)
            )

        return response.json() if response.status_code == 200 else None

    def prepare_url_for_data_get(self):
        """
        Prepares and executes a full data fetch from the Tripletex `/customer` endpoint.

        This method:
        - Fetches the initial response to determine the `fullResultSize` (total number of customers)
        - Constructs a new GET request with the correct `from` and `count` parameters to retrieve all customer records
        - Returns the full customer list response object (if successful), otherwise returns None

        Raises:
            ValidationError: If an exception occurs during the API call.
        """
        base_url, headers, password = self.customer_data_get_from_tripletex()
        try:
            initial_response = requests.get(
                f"{base_url}/customer", headers=headers, auth=("0", password)
            )
            initial_response.raise_for_status()
        except Exception as e:
            raise ValidationError(
                _("Initial customer fetch failed: %s") % str(e)
            )

        if initial_response.status_code == 200:
            full_result_length = initial_response.json().get(
                "fullResultSize", 0
            )
            try:
                response = requests.get(
                    f"{base_url}/customer?from=0&count={full_result_length}",
                    headers=headers,
                    auth=("0", password),
                )
                response.raise_for_status()
                return response
            except Exception as e:
                raise ValidationError(
                    _("Full customer fetch failed: %s") % str(e)
                )
        return None
