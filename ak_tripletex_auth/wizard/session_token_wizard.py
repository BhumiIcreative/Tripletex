from odoo import fields, models, _
import requests
from odoo.exceptions import ValidationError


class SessionTokenWizard(models.TransientModel):
    _name = 'session.token.wizard'
    _description = "Session Token Wizard"

    expiration_date = fields.Date(
        string="Expire Date"
    )

    def session_token_generate(self):
        """
        Generate a session token using the Tripletex API and store it in Odoo.
        This method:
        - Retrieves configuration parameters from `ir.config_parameter`
        - Makes a PUT request to the Tripletex API endpoint to generate a session token
        - Stores the response token and expiration date in the `tripletex.session.token` model
         (updates existing record or creates a new one)
        Raises:
           ValidationError: If required config parameters are missing, the API call fails,
                            or Tripletex returns an error response.
        """
        base_url = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.base_url')
        customer_token = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.customer_token')
        employee_token = self.env['ir.config_parameter'].sudo().get_param('ak_tripletex_auth.employee_token')
        if not base_url:
            raise ValidationError(_("Error: Base URL not configured. Please set 'ak_tripletex_auth.base_url' in the config parameters."))
        if not customer_token and employee_token:
            raise ValidationError(_("Error: consumer and employee token not found."))
        try:
            response = requests.put(
                f'{base_url}/token/session/:create', params={'consumerToken': customer_token, 'employeeToken': employee_token, 'expirationDate': self.expiration_date.strftime('%Y-%m-%d')})
        except Exception as e:
            raise ValidationError(e)
        token = self.env['tripletex.session.token'].search([],limit=1)
        print('\n=======response0',response)
        if response.status_code == 200:
            response_data = response.json()
            if token:
                token.write({
                        'expiration_date': response_data['value']['expirationDate'],
                        'token': response_data['value']['token']
                    })
            else:
                self.env['tripletex.token'].create({
                    'expiration_date': response_data['value']['expirationDate'],
                    'token': response_data['value']['token']
                })
        else:
            raise ValidationError(_("Error create session token in Tripletex: %s") % response.text)
