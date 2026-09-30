# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .maintenance_mixin import GROUP_MANAGER

FREQUENCIES = [
    ('days', 'Days'),
    ('weeks', 'Weeks'),
    ('months', 'Months'),
    ('years', 'Years'),
]
PLAN_DUE_SUMMARY = 'Preventive maintenance due'
PLAN_OVERDUE_SUMMARY = 'Preventive maintenance overdue'


class MaintenancePlan(models.Model):
    _name = 'maintenance.plan'
    _inherit = ['maintenance.access.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Preventive Maintenance Plan'
    _order = 'next_maintenance_date, id'
    _mail_post_access = 'read'

    name = fields.Char(string='Plan', required=True, tracking=True)
    asset_id = fields.Many2one('maintenance.asset', string='Asset', required=True,
                               ondelete='cascade', tracking=True, index=True)
    technician_id = fields.Many2one('res.users', string='Technician', tracking=True)
    frequency = fields.Selection(FREQUENCIES, string='Frequency', default='months',
                                 required=True, tracking=True)
    interval = fields.Integer(string='Every', default=1, required=True, tracking=True)
    next_maintenance_date = fields.Date(string='Next Maintenance', required=True, tracking=True)
    last_maintenance_date = fields.Date(string='Last Maintenance', readonly=True, copy=False)
    reminder_days = fields.Integer(string='Remind Before (days)', default=7,
                                   help='The scheduled action creates an activity this many days before the due date.')
    active = fields.Boolean(default=True)
    instructions = fields.Text(string='Instructions')
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)

    request_ids = fields.One2many('maintenance.request', 'plan_id', string='Generated Requests')
    request_count = fields.Integer(compute='_compute_request_count')
    is_overdue = fields.Boolean(string='Overdue', compute='_compute_is_overdue',
                                search='_search_is_overdue')

    _interval_positive = models.Constraint(
        'CHECK(interval > 0)', 'The interval must be strictly positive.',
    )
    _reminder_days_positive = models.Constraint(
        'CHECK(reminder_days >= 0)', 'The reminder delay cannot be negative.',
    )

    @api.depends('next_maintenance_date', 'active')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for plan in self:
            plan.is_overdue = bool(plan.active and plan.next_maintenance_date
                                   and plan.next_maintenance_date < today)

    def _search_is_overdue(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_('Unsupported search on "Overdue".'))
        today = fields.Date.context_today(self)
        positive = (operator == '=') == value
        if positive:
            return [('active', '=', True), ('next_maintenance_date', '<', today)]
        return ['|', ('active', '=', False), ('next_maintenance_date', '>=', today)]

    @api.depends('request_ids')
    def _compute_request_count(self):
        counts = dict(self.env['maintenance.request']._read_group(
            [('plan_id', 'in', self.ids)], ['plan_id'], ['__count']))
        for plan in self:
            plan.request_count = counts.get(plan, 0)

    @api.constrains('technician_id')
    def _check_technician(self):
        for plan in self:
            if plan.technician_id and not plan.technician_id.has_group('smart_maintenance.group_maintenance_user'):
                raise ValidationError(_('%s is not a Maintenance User.', plan.technician_id.display_name))

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------
    def _get_next_date(self, base_date):
        """Add the configured interval to ``base_date`` (frequency keys are relativedelta kwargs)."""
        self.ensure_one()
        return base_date + relativedelta(**{self.frequency: self.interval})

    def action_complete_maintenance(self, completion_date=None):
        """Register a completed preventive maintenance and schedule the next one.

        Design decision: the next date is computed from the *completion* date, so a
        late intervention does not trigger two back-to-back interventions.
        """
        completion_date = fields.Date.to_date(completion_date) or fields.Date.context_today(self)
        for plan in self:
            plan.write({
                'last_maintenance_date': completion_date,
                'next_maintenance_date': plan._get_next_date(completion_date),
            })
            reminders = plan.activity_ids.filtered(
                lambda a: a.summary in (PLAN_DUE_SUMMARY, PLAN_OVERDUE_SUMMARY))
            reminders.sudo().action_feedback(feedback=_('Preventive maintenance completed.'))
        return True

    def action_create_request(self):
        """Generate the corrective-workflow request that carries out this plan."""
        self.ensure_one()
        if not self._is_maintenance_manager():
            raise UserError(_('Only Maintenance Managers can generate requests from a plan.'))
        open_request = self.request_ids.filtered(
            lambda r: r.state not in ('completed', 'cancelled'))
        if open_request:
            raise UserError(_('An open request already exists for this plan: %s',
                              open_request[0].request_number))
        request = self.env['maintenance.request'].create({
            'name': _('Preventive: %s', self.name),
            'request_type': 'preventive',
            'plan_id': self.id,
            'asset_id': self.asset_id.id,
            'priority': '1',
            'description': self.instructions,
            'assigned_technician_id': self.technician_id.id,
            'expected_completion_date': self.next_maintenance_date,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'maintenance.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_requests(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('smart_maintenance.action_maintenance_request')
        action['domain'] = [('plan_id', '=', self.id)]
        action['context'] = {'default_plan_id': self.id, 'default_asset_id': self.asset_id.id,
                             'default_request_type': 'preventive'}
        return action

    # ------------------------------------------------------------------
    # Scheduled action
    # ------------------------------------------------------------------
    @api.model
    def _cron_check_maintenance_plans(self):
        """Create reminders for plans that are due soon or overdue.

        Recipient: the plan's technician, otherwise every Maintenance Manager of
        the plan's company. Idempotent: one open activity per (plan, user, summary).
        """
        today = fields.Date.context_today(self)
        plans = self.search([('active', '=', True), ('next_maintenance_date', '!=', False)])
        for plan in plans:
            days_left = (plan.next_maintenance_date - today).days
            if days_left > plan.reminder_days:
                continue
            overdue = days_left < 0
            summary = PLAN_OVERDUE_SUMMARY if overdue else PLAN_DUE_SUMMARY
            recipients = plan.technician_id or self._get_users_in_group(GROUP_MANAGER, plan.company_id)
            for user in recipients:
                already = plan.activity_ids.filtered(
                    lambda a, user=user, summary=summary: a.user_id == user and a.summary == summary)
                if already:
                    continue
                plan.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=plan.next_maintenance_date,
                    summary=summary,
                    note=_('Plan "%(plan)s" on %(asset)s is due on %(date)s.',
                           plan=plan.name, asset=plan.asset_id.display_name,
                           date=plan.next_maintenance_date),
                    user_id=user.id,
                )
        return True
