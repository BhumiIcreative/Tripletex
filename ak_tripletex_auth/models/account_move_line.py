from odoo import fields, models, api


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    tripletex_product = fields.Char(
        string="Tripletex product",
        related='product_id.tripletex_product',
        readonly=True
    )
    # serial_lot_number = fields.Char(string="Serial/Lot Number", compute="_compute_serial_lot_number", store=True)

    @api.onchange('product_id')
    def onchange_product_id(self):
        """
        Onchange handler for `product_id`.
        Automatically sets the `tripletex_product` field with the corresponding
        value from the selected product, if available.
        """
        print('\n---------onchange_product_id')
        if self.product_id:
            self.tripletex_product = self.product_id.tripletex_product

    # @api.depends('product_id', 'move_id')
    # def _compute_serial_lot_number(self):
    #    for line in self:
    #        if line.product_id and line.move_id.move_type in ('out_invoice', 'out_refund'):
    #            # Find stock move lines that relate to the product and the move
    #            stock_moves = self.env['stock.move.line'].search([
    #                ('move_id.picking_id.sale_id.name', '=', line.move_id.invoice_origin),
    #                ('product_id', '=', line.product_id.id),
    #                ('state', '=', 'done'),
    #            ])
    #            # Extract the lot names from the stock move lines
    #            lot_names = stock_moves.mapped('lot_id.name')
    #            # Join the lot names as a comma-separated string
    #            line.serial_lot_number = ', '.join(lot_names) if lot_names else ''
