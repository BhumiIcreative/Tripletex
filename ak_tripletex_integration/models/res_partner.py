from odoo import fields, models, api, _
import requests
import json
from odoo.exceptions import ValidationError
import concurrent.futures
import re
import logging

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = "res.partner"

    trip_customer = fields.Char(string="Tripletex Id")
    is_hidden = fields.Boolean(
        default=False,
        string="Hidden",
    )
    discountPercentage = fields.Float(string="Discount Chargeable Order line")
    alternate_email = fields.Char(string="Alternate email")
    due_date = fields.Integer(default=14, string="Due Days")
    is_notice_od_debt = fields.Boolean(string="Notice of debt collection")
    is_reminder = fields.Boolean(string="Reminder")
    is_soft_reminder = fields.Boolean(string="Soft reminder")
    send_invoice_via = fields.Selection(
        [("EMAIL", "Email"), ("MANUAL", "Manually Sent"), ("EHF", "EHF")],
        default="EMAIL",
        string="Invoice Send Method",
        required=True,
        store=True,
    )
    email_attachment_type = fields.Selection(
        [("ATTACHMENT", "Attachment"), ("LINK", "Link")],
        default="ATTACHMENT",
        string="Send Invoice By Email as",
        required=True,
        store=True,
    )
    INVOICE_DUE_TYPES_ALL = [
        ("DAYS", "Days"),
        ("MONTHS", "Month"),
        ("DAYS_AFTER_INVOICE_DATE", "Days after invoice date (TT)"),
        ("FIXED_DAY_OF_MONTH", "Fixed day of month (TT)"),
        ("RECURRING_DAY_OF_MONTH", "Recurring day of month (TT)"),
    ]

    INVOICE_DUE_TYPES_UI = [
        ("DAYS", "Days"),
        ("MONTHS", "Month"),
    ]
    invoices_due_in_type = fields.Selection(
        INVOICE_DUE_TYPES_ALL,
        string="Invoice Due Type (Hidden)",
        default="DAYS",
        required=True,

    )
    invoices_due_in_type_ui = fields.Selection(
        INVOICE_DUE_TYPES_UI,
        string="Invoice Due Type",
        compute="_compute_invoice_due_type_ui",
        inverse="_inverse_invoice_due_type_ui",
    )

    customer_number = fields.Char(string="Customer Number")
    supplier_number = fields.Char(string="Supplier Number")
    business_street = fields.Char(string="Street", store=True)
    business_street2 = fields.Char(string="Street", store=True)
    business_city = fields.Char(string="City", store=True)
    business_zip = fields.Char(string="Zip", store=True)
    business_country_id = fields.Many2one(
        "res.country",
        string="Business Country",
        store=True,
        ondelete="restrict",
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
             "• Mobile phone number for SMS notifications",
    )

    @api.constrains("vat")
    def _check_vat_format(self):
        """
        Validate the format of the VAT (Tax ID) field.

        This constraint ensures that the `vat` field:
        - Is exactly 9 digits long.
        - Contains only digits (no letters, symbols, or spaces).

        Raises:
            ValidationError: If the VAT format is invalid.
        """
        for rec in self.filtered(lambda x: x.vat):
            if not re.fullmatch(r"\d{9}", rec.vat):
                raise ValidationError(
                    _(
                        "Invalid Tax ID for customer '%s': The Tax ID must consist of exactly 9 digits and cannot contain spaces or letters."
                    )
                    % rec.name
                )

    @api.depends("invoices_due_in_type")
    def _compute_invoice_due_type_ui(self):
        for rec in self:
            if rec.invoices_due_in_type in dict(self.INVOICE_DUE_TYPES_UI):
                rec.invoices_due_in_type_ui = rec.invoices_due_in_type
            else:
                rec.invoices_due_in_type_ui = False

    def _inverse_invoice_due_type_ui(self):
        for rec in self:
            if rec.invoices_due_in_type_ui:
                rec.invoices_due_in_type = rec.invoices_due_in_type_ui

    def get_country_id(self, iso_alpha2_code):
        """
        Retrieve the Tripletex country ID based on the ISO Alpha-2 code.
        Args:
            iso_alpha2_code (str): Two-letter ISO country code (e.g., 'NO' for Norway)
        Raises:
            ValidationError: If the Tripletex API call fails or returns an error.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        try:
            response = requests.get(
                url=f"{base_url}/country",
                auth=("0", password),
                headers=headers,
            )
            if response.status_code != 200:
                raise ValidationError(
                    _(
                        "Error retrieving country data from Tripletex: {}"
                    ).format(response.text or "Unknown error")
                )
            countries = response.json().get("values", [])
            for country in countries:
                if country.get("isoAlpha2Code") == iso_alpha2_code:
                    return country.get("id")

            return None  # Country code not found
        except Exception as e:
            raise ValidationError(
                _("Tripletex Country Lookup Failed: %s") % str(e)
            )

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override the default `create` method to automatically create corresponding
        customer records in Tripletex when new company-type partners are created in Odoo.

        For each partner record in `vals_list`, if:
        - The `trip_customer` flag is not set
        - And the partner is of type 'company'
        Then construct a payload and send it to Tripletex using the `create_customer_in_tripletex` method.

        Skips Tripletex sync if the context contains `skip_tripletex_sync`.

        :raises ValidationError: If required data (like email or Tripletex API access) is missing
        """
        res = super(ResPartner, self).create(vals_list)

        if self.env.context.get("skip_tripletex_sync"):
            return res

        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        if not (base_url and password):
            return res

        for rec_vals, rec in zip(vals_list, res):
            if rec_vals.get("trip_customer"):
                continue

            if not rec_vals.get("email"):
                raise ValidationError(
                    _("Customer '%s' has no email.") % rec_vals["name"]
                )

            # Base Tripletex payload
            data = {
                "name": rec_vals["name"],
                "isSupplier": bool(rec_vals.get("supplier_rank")),
                "isCustomer": bool(rec_vals.get("customer_rank")),
                "organizationNumber": rec_vals.get("vat") or None,
                "email": rec_vals.get("email"),
                "overdueNoticeEmail": rec_vals.get("alternate_email") or None,
                "phoneNumber": rec_vals.get("phone") or None,
                "phoneNumberMobile": rec_vals.get("mobile") or None,
                "isPrivateIndividual": rec_vals.get("company_type")
                                       != "company",
                "website": rec_vals.get("website"),
                "invoiceEmail": rec_vals.get("email"),
                "isInactive": not rec_vals.get("active", True),
                "description": self.env[
                    "product.template"
                ].convert_html_to_normal_text(rec_vals.get("comment") or ""),
                "postalAddress": {
                    "addressLine1": rec_vals.get("street"),
                    "addressLine2": rec_vals.get("street2"),
                    "postalCode": rec_vals.get("zip"),
                    "city": rec_vals.get("city"),
                    "country": {
                        "id": self.get_country_id(
                            self.env["res.country"]
                            .browse(rec_vals.get("country_id"))
                            .code
                        )
                    },
                },
            }

            if not data.get("isSupplier"):
                data.update(
                    {
                        "invoicesDueIn": rec_vals.get("due_date"),
                        "invoiceSendMethod": rec_vals.get("send_invoice_via"),
                        "isPrivateIndividual": rec_vals.get("company_type")
                                               == "person",
                        "singleCustomerInvoice": rec_vals.get(
                            "is_single_customer_invoice"
                        ),
                        "isAutomaticSoftReminderEnabled": rec_vals.get(
                            "is_soft_reminder"
                        ),
                        "isAutomaticReminderEnabled": rec_vals.get(
                            "is_reminder"
                        ),
                        "isAutomaticNoticeOfDebtCollectionEnabled": rec_vals.get(
                            "is_notice_od_debt"
                        ),
                        "invoicesDueInType": rec_vals.get(
                            "invoices_due_in_type"
                        ),
                        "emailAttachmentType": rec_vals.get(
                            "email_attachment_type"
                        ),
                        "discountPercentage": rec_vals.get(
                            "discountPercentage"
                        ),
                        "physicalAddress": {
                            "addressLine1": rec_vals.get("business_street"),
                            "addressLine2": rec_vals.get("business_street2"),
                            "postalCode": rec_vals.get("business_zip"),
                            "city": rec_vals.get("business_city"),
                            "country": {
                                "id": self.get_country_id(
                                    self.env["res.country"]
                                    .browse(
                                        rec_vals.get("business_country_id")
                                    )
                                    .code
                                )
                            },
                        },
                    }
                )

            self.create_customer_in_tripletex(data, rec)

        return res

    def create_customer_in_tripletex(self, data, partner=None):
        """
        Send prepared customer or supplier data to Tripletex API and optionally
        store the returned Tripletex ID on the corresponding Odoo partner record.

        This method:
        - Determines whether to create a customer or supplier in Tripletex.
        - Sends the payload via POST request.
        - Handles connection and response errors gracefully.
        - Saves the Tripletex ID in the `trip_customer` field if a partner is provided.

        :param data: Dictionary with the customer or supplier data formatted for Tripletex.

        :raises ValidationError: If configuration is missing, API fails, or response is invalid.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        if not base_url or not password:
            raise ValidationError(
                _(
                    "Tripletex configuration is missing. Please check your credentials."
                )
            )

        # Determine if we are creating a customer or a supplier
        endpoint = (
            "customer"
            if data.get("isCustomer")
               or (partner and partner.customer_rank > 0)
            else "supplier"
        )

        try:
            response = requests.post(
                url=f"{base_url}/{endpoint}",
                data=json.dumps(data),
                auth=("0", password),
                headers=headers,
            )
        except Exception as e:
            raise ValidationError(
                _("Tripletex connection error: {}").format(str(e))
            )

        if response.status_code in [200, 201]:
            trip_id = response.json().get("value", {}).get("id")
            customer_number = (
                response.json().get("value", {}).get("customerNumber")
            )
            supplier_number = response.json().get("value", {}).get("supplierNumber")  # ✅ Get supplier number

            if not trip_id:
                raise ValidationError(
                    _(
                        "Tripletex returned success but no ID was found in the response."
                    )
                )

            if partner:
                partner.write(
                    {
                        "trip_customer": trip_id,
                        "customer_number": customer_number,
                        "supplier_number": supplier_number,
                    }
                )
            return trip_id

        raise ValidationError(
            _("Error creating customer/supplier in Tripletex: {}").format(
                response.text or "Unknown error"
            )
        )

    def update_data_into_tripletex(
            self, partner, base_url, headers, auth, endpoint, payload
    ):
        """
        Update data in Tripletex via PUT request.

        This method sends a JSON payload to a specific Tripletex API endpoint to update
        existing customer, contact, invoice, or delivery address data. It handles common
        error scenarios and cleans up the local Odoo partner's `trip_customer` field if
        the remote object no longer exists.

        :param partner: The `res.partner` record being updated.
        :param base_url: The Tripletex API base URL.
        :param headers: HTTP headers including authorization and content type.
        :param auth: Tuple for HTTP Basic Auth (e.g., ('0', token)).
        :param endpoint: API endpoint path to which the PUT request is sent (e.g., '/customer/123').
        :param payload: Dictionary of data to be updated in Tripletex.
        :raises ValidationError: If the Tripletex call fails or the server responds with an error.
        """
        try:
            response = requests.put(
                url=f"{base_url}{endpoint}",
                data=json.dumps(payload),
                auth=auth,
                headers=headers,
            )

            if response.status_code == 404:
                # Remote Tripletex object not found — reset sync flag
                partner.write(
                    {"trip_customer": False, "customer_number": False}
                )
            elif response.status_code not in [200, 201]:
                raise ValidationError(
                    _("Tripletex update error for endpoint '%s': %s")
                    % (endpoint, response.text or "Unknown error")
                )
        except Exception as e:
            raise ValidationError(_("Tripletex update failed: %s") % str(e))

    def write(self, vals):
        """
        Override of `write()` to sync partner updates from Odoo to Tripletex.

        This method ensures that any updates to `res.partner` records are reflected in Tripletex
        through appropriate API calls. It handles updates for:
        - Customers (via `/customer/{id}`)
        - Suppliers (via `/supplier/{id}`)
        - Invoice Contacts (via `/customer/{parent_id}`)
        - Delivery Addresses (via `/deliveryAddress/{id}`)

        The method also prepares and submits API calls asynchronously using a thread pool.

        Key Sync Behaviors:
        - If `trip_customer` is not set, the record is skipped.
        - If the partner is an `invoice` contact, only email is synced to parent customer.
        - If the partner is of type `delivery`, the delivery address is synced directly.
        - For regular customers and suppliers, postal and physical addresses are included.

        :raises ValidationError: If Tripletex API call fails and is not caught internally.
        """
        res = super(ResPartner, self).write(vals)
        if self.env.context.get("skip_tripletex_sync"):
            return res

        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        auth = ("0", password)
        if not base_url or not password:
            return res

        api_calls = []

        for rec in self:
            if not rec.trip_customer:
                continue

            country_id = (
                rec.get_country_id(rec.country_id.code)
                if rec.country_id
                else None
            )
            business_country_id = (
                rec.get_country_id(rec.business_country_id.code)
                if rec.business_country_id
                else None
            )

            postal_address = {
                "addressLine1": rec.street or None,
                "addressLine2": rec.street2 or None,
                "postalCode": rec.zip or None,
                "city": rec.city or None,
                "country": {"id": country_id} if country_id else None,
            }

            physical_address = {
                "addressLine1": rec.business_street or None,
                "addressLine2": rec.business_street2 or None,
                "postalCode": rec.business_zip or None,
                "city": rec.business_city or None,
                "country": {"id": business_country_id}
                if business_country_id
                else None,
            }

            # Invoice contact update
            if (
                    rec.type == "invoice"
                    and rec.parent_id
                    and rec.parent_id.trip_customer
            ):
                self.update_data_into_tripletex(
                    rec.parent_id.trip_customer,
                    base_url,
                    headers,
                    auth,
                    f"/customer/{rec.parent_id.trip_customer}",
                    {"invoiceEmail": rec.email or None},
                )
                continue

            # Delivery address update
            if rec.type == "delivery":
                self.update_data_into_tripletex(
                    rec.trip_customer,
                    base_url,
                    headers,
                    auth,
                    f"/deliveryAddress/{rec.trip_customer}",
                    postal_address,
                )
                continue

            # General customer/supplier/contact payload
            data = {
                "name": rec.name,
                "email": rec.email or None,
                "phoneNumber": rec.phone or None,
                "phoneNumberMobile": rec.mobile or None,
                "isPrivateIndividual": rec.company_type == "person",
                "website": rec.website or None,
                "supplierNumber": rec.supplier_number or None,
                "isInactive": not rec.active,
                "invoiceEmail": rec.email or None,
                "description": self.env[
                    "product.template"
                ].convert_html_to_normal_text(rec.comment or ""),
                "postalAddress": postal_address or False,
                "physicalAddress": physical_address or False,
            }

            endpoint = None

            if bool(rec.customer_rank):
                data.update(
                    {
                        "isCustomer": True,
                        "overdueNoticeEmail": rec.alternate_email or None,
                        "emailAttachmentType": rec.email_attachment_type,
                        "invoicesDueInType": rec.invoices_due_in_type,
                        "invoiceSendMethod": rec.send_invoice_via,
                        "invoicesDueIn": rec.due_date,
                        "customerNumber": rec.customer_number,
                        "singleCustomerInvoice": rec.is_single_customer_invoice,
                        "isAutomaticSoftReminderEnabled": rec.is_soft_reminder,
                        "isAutomaticReminderEnabled": rec.is_reminder,
                        "isAutomaticNoticeOfDebtCollectionEnabled": rec.is_notice_od_debt,
                        "discountPercentage": rec.discountPercentage,
                        "organizationNumber": rec.vat or None,
                    }
                )
                endpoint = f"/customer/{rec.trip_customer}"

            elif bool(rec.supplier_rank):
                data.update(
                    {
                        "isSupplier": True,
                        "organizationNumber": rec.vat or None,
                    }
                )
                endpoint = f"/supplier/{rec.trip_customer}"

            if (
                    rec.company_type == "person"
                    and rec.type == "contact"
                    and not rec.parent_id
            ):
                data.update({"isPrivateIndividual": True})
            elif rec.company_type == "company":
                data.update(
                    {
                        "organizationNumber": rec.vat or None,
                        "website": rec.website,
                        "isPrivateIndividual": False,
                    }
                )
            else:
                continue

            if endpoint:
                api_calls.append(self.prepare_api_call(rec, endpoint, data))

        # Execute API calls in parallel (if not skipped)
        if not self.env.context.get("skip_tripletex_sync") and api_calls:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=10
            ) as executor:
                futures = [executor.submit(call) for call in api_calls]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        _logger.warning(f"Tripletex API call failed: {str(e)}")

        return res

    def prepare_api_call(self, partner, endpoint, data):
        """
        Prepares a callable function that performs a Tripletex API update for the given partner.

        This is used to create a deferred function for concurrent execution via ThreadPoolExecutor.
        It fetches the necessary Tripletex authentication and configuration, and returns a closure
        that updates the specified endpoint with the provided data.

        Args:
            partner (res.partner): The partner record being synced.
            endpoint (str): The Tripletex API endpoint for the update (e.g., '/customer/{id}').
            data (dict): The payload to send in the PUT request.

        Returns:
            function: A callable that executes the update when invoked.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        auth = ("0", password)

        def call():
            self.update_data_into_tripletex(
                partner, base_url, headers, auth, endpoint, data
            )

        return call

    def unlink(self):
        """
        Overrides the unlink method to delete the corresponding customer or contact in Tripletex
        before deleting the partner record in Odoo.

        Logic:
        - Deletes the entity from Tripletex based on `company_type` and `type`.
        - Uses `/customer/{id}` for companies.
        - Uses `/contact/list?ids={id}` for individual contacts.

        Returns:
            Result of the superclass `unlink` method.

        Raises:
            ValidationError: If the Tripletex deletion fails or returns a status code other than 204.
        """
        base_url, headers, password = self.env[
            "tripletex.customer.load"
        ].customer_data_get_from_tripletex()
        if not base_url or not password:
            return super().unlink()

        auth = ("0", password)

        for rec in self:
            if not rec.trip_customer:
                continue

            url = None
            if rec.company_type == "company":
                url = f"{base_url}/customer/{rec.trip_customer}"
            elif rec.company_type == "person" or rec.type == "contact":
                url = f"{base_url}/contact/list?ids={rec.trip_customer}"

            if url:
                try:
                    response = requests.delete(url, auth=auth, headers=headers)
                    if response.status_code != 204:
                        raise ValidationError(
                            _(
                                f"Failed to delete Tripletex record (ID {rec.trip_customer}). "
                                f"Status code: {response.status_code}, Response: {response.text}"
                            )
                        )
                except Exception as e:
                    raise ValidationError(
                        f"Tripletex deletion error: {str(e)}"
                    )

        return super().unlink()
