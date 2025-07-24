from odoo import fields, models, _
import requests
from odoo.exceptions import ValidationError


class SessionTokenWizard(models.TransientModel):
    _name = "session.token.wizard"
    _description = "Session Token Wizard"

    expiration_date = fields.Date(string="Expire Date")

    def session_token_generate(self):
        """
        Generates a session token via the Tripletex API and stores it in Odoo.

        Steps:
        - Retrieves required configuration parameters from `ir.config_parameter`
        - Sends a PUT request to Tripletex's `/token/session/:create` endpoint
        - Parses the response and either creates or updates a record in `tripletex.session.token`

        Raises:
            ValidationError: If configuration is missing or the API call fails.
        """
        ICPSudo = self.env["ir.config_parameter"].sudo()
        base_url = ICPSudo.get_param("ak_tripletex_integration.base_url")
        customer_token = ICPSudo.get_param(
            "ak_tripletex_integration.customer_token"
        )
        employee_token = ICPSudo.get_param(
            "ak_tripletex_integration.employee_token"
        )

        # Validation
        if not base_url:
            raise ValidationError(
                _(
                    "Missing configuration: Set 'ak_tripletex_integration.base_url' in system parameters."
                )
            )
        if not (customer_token or employee_token):
            raise ValidationError(
                _(
                    "Missing configuration: Either customer or employee token must be set."
                )
            )

        # Make request
        try:
            response = requests.put(
                f"{base_url}/token/session/:create",
                params={
                    "consumerToken": customer_token,
                    "employeeToken": employee_token,
                    "expirationDate": self.expiration_date.strftime(
                        "%Y-%m-%d"
                    ),
                },
            )
        except Exception as e:
            raise ValidationError(
                _("Tripletex token request failed: %s") % str(e)
            )

        # Handle response
        if response.status_code == 200:
            response_data = response.json().get("value", {})
            token_val = response_data.get("token")
            expiry_val = response_data.get("expirationDate")

            if not token_val or not expiry_val:
                raise ValidationError(
                    _(
                        "Invalid response from Tripletex: missing token or expiration date."
                    )
                )

            vals = {
                "token": token_val,
                "expiration_date": expiry_val,
            }

            # Update or create token record
            token_record = self.env["tripletex.session.token"].search(
                [], limit=1
            )
            if token_record:
                token_record.write(vals)
            else:
                self.env["tripletex.session.token"].create(vals)
        else:
            raise ValidationError(
                _("Tripletex returned an error (%s): %s")
                % (response.status_code, response.text)
            )
