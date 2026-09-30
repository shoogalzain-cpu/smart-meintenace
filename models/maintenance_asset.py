# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ASSET_TYPES = [
    ('equipment', 'Equipment'),
    ('vehicle', 'Vehicle'),
    ('computer', 'Computer'),
    ('hvac', 'HVAC'),
    ('machinery', 'Machinery'),
    ('building', 'Building'),
    ('other', 'Other'),
]
ASSET_STATUSES = [
    ('active', 'Active'),
    ('under_maintenance', 'Under Maintenance'),
    ('retired', 'Retired'),
]
OPEN_STATES = ['new', 'under_review', 'assigned', 'in_progress', 'waiting_parts']


class MaintenanceAsset(models.Model):
    _name = 'maintenance.asset'
    _inherit = ['maintenance.access.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Maintenance Asset'
    _order = 'asset_code, id'
    _rec_names_search = ['name', 'asset_code', 'serial_number']
    # Users may only *read* assets, but must still be able to comment in the chatter.
    _mail_post_access = 'read'

    name = fields.Char(string='Asset Name', required=True, tracking=True)
    asset_code = fields.Char(string='Asset Code', default='New', copy=False, readonly=True, index=True)
    asset_type = fields.Selection(ASSET_TYPES, string='Asset Type', default='equipment',
                                  required=True, tracking=True)
    serial_number = fields.Char(string='Serial Number', copy=False)
    location = fields.Char(string='Location')
    department_id = fields.Many2one('hr.department', string='Department', tracking=True)
    responsible_employee_id = fields.Many2one('hr.employee', string='Responsible Employee', tracking=True)
    purchase_date = fields.Date(string='Purchase Date')
    warranty_expiry = fields.Date(string='Warranty Expiry')
    status = fields.Selection(ASSET_STATUSES, string='Status', default='active',
                              required=True, tracking=True)
    notes = fields.Text(string='Notes')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)

    request_ids = fields.One2many('maintenance.request', 'asset_id', string='Maintenance Requests')
    plan_ids = fields.One2many('maintenance.plan', 'asset_id', string='Maintenance Plans')
    total_request_count = fields.Integer(string='Total Requests', compute='_compute_request_counts')
    open_request_count = fields.Integer(string='Open Requests', compute='_compute_request_counts')
    plan_count = fields.Integer(string='Plans', compute='_compute_plan_count')

    _asset_code_uniq = models.Constraint(
        'UNIQUE(asset_code)', 'The asset code must be unique.',
    )

    @api.depends('request_ids.state')
    def _compute_request_counts(self):
        # One grouped query per metric instead of one query per asset.
        totals = dict(self.env['maintenance.request']._read_group(
            [('asset_id', 'in', self.ids)], ['asset_id'], ['__count']))
        opened = dict(self.env['maintenance.request']._read_group(
            [('asset_id', 'in', self.ids), ('state', 'in', OPEN_STATES)], ['asset_id'], ['__count']))
        for asset in self:
            asset.total_request_count = totals.get(asset, 0)
            asset.open_request_count = opened.get(asset, 0)

    @api.depends('plan_ids')
    def _compute_plan_count(self):
        counts = dict(self.env['maintenance.plan']._read_group(
            [('asset_id', 'in', self.ids)], ['asset_id'], ['__count']))
        for asset in self:
            asset.plan_count = counts.get(asset, 0)

    @api.constrains('purchase_date', 'warranty_expiry')
    def _check_warranty_dates(self):
        for asset in self:
            if asset.purchase_date and asset.warranty_expiry and asset.warranty_expiry < asset.purchase_date:
                raise ValidationError(_('The warranty cannot expire before the purchase date.'))

    @api.depends('name', 'asset_code')
    def _compute_display_name(self):
        for asset in self:
            asset.display_name = f'[{asset.asset_code}] {asset.name}' if asset.asset_code else asset.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('asset_code', 'New') in (False, 'New'):
                vals['asset_code'] = self.env['ir.sequence'].next_by_code('maintenance.asset') or 'New'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def _open_requests_action(self, domain):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('smart_maintenance.action_maintenance_request')
        action['domain'] = domain
        action['context'] = {'default_asset_id': self.id}
        return action

    def action_view_requests(self):
        return self._open_requests_action([('asset_id', '=', self.id)])

    def action_view_open_requests(self):
        return self._open_requests_action([('asset_id', '=', self.id), ('state', 'in', OPEN_STATES)])

    def action_view_plans(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('smart_maintenance.action_maintenance_plan')
        action['domain'] = [('asset_id', '=', self.id)]
        action['context'] = {'default_asset_id': self.id}
        return action
