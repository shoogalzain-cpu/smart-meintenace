# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MaintenancePart(models.Model):
    _name = 'maintenance.part'
    _description = 'Maintenance Part'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    request_id = fields.Many2one('maintenance.request', string='Request', required=True,
                                 ondelete='cascade', index=True)
    product_name = fields.Char(string='Part / Product', required=True)
    quantity = fields.Float(string='Quantity', default=1.0, digits=(16, 2), required=True)
    unit_cost = fields.Monetary(string='Unit Cost', currency_field='currency_id')
    total_cost = fields.Monetary(string='Total Cost', compute='_compute_total_cost',
                                 store=True, currency_field='currency_id')
    currency_id = fields.Many2one(related='request_id.currency_id', string='Currency')
    company_id = fields.Many2one(related='request_id.company_id', string='Company',
                                 store=True, index=True)

    _quantity_positive = models.Constraint(
        'CHECK(quantity > 0)', 'The quantity of a part must be strictly positive.',
    )
    _unit_cost_positive = models.Constraint(
        'CHECK(unit_cost >= 0)', 'The unit cost cannot be negative.',
    )

    @api.depends('quantity', 'unit_cost')
    def _compute_total_cost(self):
        for part in self:
            part.total_cost = part.quantity * part.unit_cost

    # Parts of a closed request are frozen: costs must not change after the fact.
    @api.model_create_multi
    def create(self, vals_list):
        request_ids = [vals['request_id'] for vals in vals_list if vals.get('request_id')]
        self.env['maintenance.request'].browse(request_ids)._check_open_for_changes()
        return super().create(vals_list)

    def write(self, vals):
        self.request_id._check_open_for_changes()
        if vals.get('request_id'):
            self.env['maintenance.request'].browse(vals['request_id'])._check_open_for_changes()
        return super().write(vals)

    def unlink(self):
        self.request_id._check_open_for_changes()
        return super().unlink()
