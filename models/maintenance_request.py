# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .maintenance_mixin import GROUP_MANAGER, GROUP_USER

STATES = [
    ('new', 'New'),
    ('under_review', 'Under Review'),
    ('assigned', 'Assigned'),
    ('in_progress', 'In Progress'),
    ('waiting_parts', 'Waiting Parts'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]
PRIORITIES = [
    ('0', 'Low'),
    ('1', 'Medium'),
    ('2', 'High'),
    ('3', 'Critical'),
]
CLOSED_STATES = ('completed', 'cancelled')
OPEN_STATES = ['new', 'under_review', 'assigned', 'in_progress', 'waiting_parts']

# Workflow: allowed transitions. Anything else is rejected server-side.
TRANSITIONS = {
    'new': {'under_review', 'cancelled'},
    'under_review': {'assigned', 'cancelled'},
    'assigned': {'in_progress', 'cancelled'},
    'in_progress': {'waiting_parts', 'completed', 'cancelled'},
    'waiting_parts': {'in_progress', 'cancelled'},
    'completed': set(),
    'cancelled': {'new'},
}
# Only managers may move a request to these states.
MANAGER_TARGET_STATES = {'new', 'under_review', 'assigned', 'cancelled'}
# Only managers may write these fields.
MANAGER_ONLY_FIELDS = {'assigned_technician_id', 'estimated_cost', 'service_cost'}
# Business fields frozen once a request is completed or cancelled.
LOCKED_FIELDS = {
    'name', 'request_type', 'plan_id', 'asset_id', 'requested_by', 'department_id',
    'description', 'priority', 'request_date', 'assigned_technician_id',
    'expected_completion_date', 'actual_completion_date', 'estimated_cost',
    'service_cost', 'resolution_notes',
}

# Stable (untranslated) markers used to avoid duplicate activities.
ASSIGN_SUMMARY = 'Maintenance request assigned'
OVERDUE_SUMMARY = 'Maintenance request overdue'
COMPLETED_SUMMARY = 'Maintenance request completed'


class MaintenanceRequest(models.Model):
    _name = 'maintenance.request'
    _inherit = ['maintenance.access.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Maintenance Request'
    _order = 'priority desc, request_date desc, id desc'
    _rec_names_search = ['request_number', 'name']
    _mail_post_access = 'read'

    name = fields.Char(string='Subject', required=True, tracking=True)
    request_number = fields.Char(string='Request Number', default='New', copy=False,
                                 readonly=True, index=True)
    request_type = fields.Selection(
        [('corrective', 'Corrective'), ('preventive', 'Preventive')],
        string='Type', default='corrective', required=True, tracking=True)
    plan_id = fields.Many2one('maintenance.plan', string='Preventive Plan', copy=False,
                              ondelete='set null', index='btree_not_null')
    asset_id = fields.Many2one('maintenance.asset', string='Asset', required=True,
                               ondelete='restrict', tracking=True, index=True)
    asset_code = fields.Char(related='asset_id.asset_code', string='Asset Code', store=True)
    requested_by = fields.Many2one('res.users', string='Requested By', required=True,
                                   default=lambda self: self.env.user, ondelete='restrict',
                                   tracking=True)
    department_id = fields.Many2one('hr.department', string='Department',
                                    compute='_compute_department_id', store=True,
                                    readonly=False, index=True)
    description = fields.Text(string='Problem Description')
    priority = fields.Selection(PRIORITIES, string='Priority', default='1',
                                required=True, tracking=True, index=True)
    request_date = fields.Date(string='Request Date', required=True, tracking=True,
                               default=fields.Date.context_today, index=True)
    assigned_technician_id = fields.Many2one('res.users', string='Technician', copy=False,
                                             tracking=True, index=True)
    expected_completion_date = fields.Date(string='Expected Completion', tracking=True)
    actual_completion_date = fields.Date(string='Completion Date', readonly=True, copy=False)
    maintenance_duration = fields.Integer(string='Duration (days)', compute='_compute_duration',
                                          store=True)
    is_overdue = fields.Boolean(string='Overdue', compute='_compute_is_overdue',
                                search='_search_is_overdue')

    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency')
    estimated_cost = fields.Monetary(string='Estimated Cost', currency_field='currency_id', tracking=True)
    service_cost = fields.Monetary(string='Additional Service Cost', currency_field='currency_id',
                                   tracking=True)
    parts_cost = fields.Monetary(string='Parts Cost', currency_field='currency_id',
                                 compute='_compute_costs', store=True)
    actual_cost = fields.Monetary(string='Actual Cost', currency_field='currency_id',
                                  compute='_compute_costs', store=True, tracking=True)
    part_ids = fields.One2many('maintenance.part', 'request_id', string='Parts Used', copy=True)

    resolution_notes = fields.Text(string='Resolution Notes')
    state = fields.Selection(STATES, string='Status', default='new', required=True,
                             copy=False, tracking=True, index=True,
                             group_expand='_read_group_expand_states')

    _request_number_uniq = models.Constraint(
        'UNIQUE(request_number)', 'The request number must be unique.',
    )

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------
    @api.depends('name', 'request_number')
    def _compute_display_name(self):
        for request in self:
            request.display_name = (f'{request.request_number} - {request.name}'
                                    if request.request_number and request.name else request.name)

    @api.depends('asset_id', 'requested_by')
    def _compute_department_id(self):
        for request in self:
            # sudo: a plain Maintenance User may not read HR data, only the result is stored.
            request.department_id = (request.asset_id.sudo().department_id
                                     or request.requested_by.sudo().employee_id.department_id)

    @api.depends('part_ids.total_cost', 'service_cost')
    def _compute_costs(self):
        for request in self:
            parts_cost = sum(request.part_ids.mapped('total_cost'))
            request.parts_cost = parts_cost
            request.actual_cost = parts_cost + request.service_cost

    @api.depends('request_date', 'actual_completion_date')
    def _compute_duration(self):
        for request in self:
            if request.request_date and request.actual_completion_date:
                request.maintenance_duration = (request.actual_completion_date - request.request_date).days
            else:
                request.maintenance_duration = 0

    @api.depends('expected_completion_date', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for request in self:
            request.is_overdue = bool(
                request.expected_completion_date
                and request.expected_completion_date < today
                and request.state not in CLOSED_STATES)

    def _search_is_overdue(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_('Unsupported search on "Overdue".'))
        today = fields.Date.context_today(self)
        if (operator == '=') == value:
            return [('expected_completion_date', '<', today), ('state', 'not in', list(CLOSED_STATES))]
        return ['|', '|', ('expected_completion_date', '=', False),
                ('expected_completion_date', '>=', today), ('state', 'in', list(CLOSED_STATES))]

    @api.model
    def _read_group_expand_states(self, values, domain):
        """Show every workflow column in the kanban view, even the empty ones."""
        return [key for key, _label in STATES]

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('request_date', 'expected_completion_date', 'actual_completion_date')
    def _check_dates(self):
        for request in self:
            if (request.expected_completion_date and request.request_date
                    and request.expected_completion_date < request.request_date):
                raise ValidationError(_('The expected completion date cannot be before the request date.'))
            if (request.actual_completion_date and request.request_date
                    and request.actual_completion_date < request.request_date):
                raise ValidationError(_('The completion date cannot be before the request date.'))

    @api.constrains('assigned_technician_id')
    def _check_technician_group(self):
        for request in self:
            technician = request.assigned_technician_id
            if technician and not technician.has_group(GROUP_USER):
                raise ValidationError(_('%s is not a Maintenance User.', technician.display_name))

    @api.constrains('estimated_cost', 'service_cost')
    def _check_costs(self):
        for request in self:
            if request.estimated_cost < 0 or request.service_cost < 0:
                raise ValidationError(_('Costs cannot be negative.'))

    def _check_open_for_changes(self):
        for request in self:
            if request.state in CLOSED_STATES:
                raise UserError(_('Request %s is %s and can no longer be modified.',
                                  request.request_number,
                                  dict(STATES)[request.state].lower()))

    # ------------------------------------------------------------------
    # ORM overrides: server-side security and workflow enforcement
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        is_manager = self._is_maintenance_manager()
        for vals in vals_list:
            if not is_manager:
                if MANAGER_ONLY_FIELDS & set(vals):
                    raise AccessError(_('Only Maintenance Managers can set the technician or the costs.'))
                if vals.get('state', 'new') != 'new':
                    raise AccessError(_('New requests must start in the "New" state.'))
            if vals.get('request_number', 'New') in (False, 'New'):
                vals['request_number'] = self.env['ir.sequence'].next_by_code('maintenance.request') or 'New'
            if vals.get('state') == 'completed' and not vals.get('actual_completion_date'):
                vals['actual_completion_date'] = fields.Date.context_today(self)
        requests = super().create(vals_list)
        requests._sync_asset_status()
        requests.filtered(
            lambda r: r.assigned_technician_id and r.state in ('assigned', 'in_progress', 'waiting_parts')
        )._schedule_assignment_activity()
        return requests

    def write(self, vals):
        if MANAGER_ONLY_FIELDS & set(vals) and not self._is_maintenance_manager():
            raise AccessError(_('Only Maintenance Managers can change the technician or the costs.'))
        if LOCKED_FIELDS & set(vals):
            self._check_open_for_changes()
        previous_states = {request.id: request.state for request in self}
        if 'state' in vals:
            self._check_transitions(vals['state'])
            if vals['state'] == 'completed' and 'actual_completion_date' not in vals:
                vals = dict(vals, actual_completion_date=fields.Date.context_today(self))
        result = super().write(vals)
        if 'state' in vals or 'assigned_technician_id' in vals:
            self._after_state_or_technician_change(previous_states, vals)
        return result

    def unlink(self):
        if any(request.state not in ('new', 'cancelled') for request in self):
            raise UserError(_('Only new or cancelled requests can be deleted. Cancel the request instead.'))
        return super().unlink()

    def _check_transitions(self, target):
        is_manager = self._is_maintenance_manager()
        for request in self:
            if request.state == target:
                continue
            if target not in TRANSITIONS[request.state]:
                raise UserError(_('A request cannot go from "%(source)s" to "%(target)s".',
                                  source=dict(STATES)[request.state], target=dict(STATES)[target]))
            if target in MANAGER_TARGET_STATES and not is_manager:
                raise AccessError(_('Only Maintenance Managers can move a request to "%s".',
                                    dict(STATES)[target]))

    # ------------------------------------------------------------------
    # Side effects of state changes
    # ------------------------------------------------------------------
    def _after_state_or_technician_change(self, previous_states, vals):
        changed = self.filtered(lambda r: previous_states[r.id] != r.state)
        changed._sync_asset_status()
        for request in changed:
            if request.state == 'assigned':
                request._schedule_assignment_activity()
            elif request.state == 'completed':
                request._on_completed()
            elif request.state == 'cancelled':
                request._close_reminders(_('Request cancelled.'))
        if 'assigned_technician_id' in vals:
            (self - changed).filtered(
                lambda r: r.state in ('assigned', 'in_progress', 'waiting_parts')
            )._schedule_assignment_activity()

    def _sync_asset_status(self):
        """Keep the asset status coherent with the work in progress.

        Intentional behaviour: an active asset becomes "Under Maintenance" while at
        least one of its requests is in progress / waiting parts, and returns to
        "Active" afterwards. Retired assets are never touched. sudo() is required
        because Maintenance Users have read-only access to assets.
        """
        for asset in self.asset_id.sudo():
            working = asset.request_ids.filtered(lambda r: r.state in ('in_progress', 'waiting_parts'))
            if working and asset.status == 'active':
                asset.status = 'under_maintenance'
            elif not working and asset.status == 'under_maintenance':
                asset.status = 'active'

    def _schedule_assignment_activity(self):
        for request in self:
            technician = request.assigned_technician_id
            if not technician:
                continue
            stale = request.activity_ids.filtered(
                lambda a: a.summary == ASSIGN_SUMMARY and a.user_id != technician)
            if stale:
                stale.sudo().unlink()
                request.invalidate_recordset(['activity_ids'])
            if request.activity_ids.filtered(
                    lambda a: a.summary == ASSIGN_SUMMARY and a.user_id == technician):
                continue
            request.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=request.expected_completion_date or fields.Date.context_today(request),
                summary=ASSIGN_SUMMARY,
                note=_('%(number)s (%(asset)s) has been assigned to you.',
                       number=request.request_number, asset=request.asset_id.display_name),
                user_id=technician.id,
            )

    def _close_reminders(self, feedback):
        for request in self:
            reminders = request.activity_ids.filtered(
                lambda a: a.summary in (ASSIGN_SUMMARY, OVERDUE_SUMMARY))
            reminders.sudo().action_feedback(feedback=feedback)

    def _on_completed(self):
        for request in self:
            request._close_reminders(_('Request completed.'))
            requester = request.requested_by
            # "If appropriate": only notify a different person who can open the request.
            if requester and requester != self.env.user and requester.has_group(GROUP_USER):
                request.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=fields.Date.context_today(request),
                    summary=COMPLETED_SUMMARY,
                    note=_('Your maintenance request %(number)s has been completed.',
                           number=request.request_number),
                    user_id=requester.id,
                )
            if request.plan_id:
                # sudo: the technician of the request is not necessarily the plan's technician.
                request.plan_id.sudo().action_complete_maintenance(request.actual_completion_date)

    # ------------------------------------------------------------------
    # Workflow buttons
    # ------------------------------------------------------------------
    def action_review(self):
        return self.write({'state': 'under_review'})

    def action_assign(self):
        for request in self:
            if not request.assigned_technician_id:
                raise UserError(_('Select a technician before assigning request %s.', request.request_number))
        return self.write({'state': 'assigned'})

    def action_start(self):
        return self.write({'state': 'in_progress'})

    def action_wait_parts(self):
        return self.write({'state': 'waiting_parts'})

    def action_resume(self):
        return self.write({'state': 'in_progress'})

    def action_complete(self):
        return self.write({'state': 'completed'})

    def action_cancel(self):
        return self.write({'state': 'cancelled'})

    def action_reset_to_new(self):
        return self.write({'state': 'new'})

    def action_view_asset(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'maintenance.asset',
            'res_id': self.asset_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Scheduled action: overdue requests -> activity for the managers
    # ------------------------------------------------------------------
    @api.model
    def _cron_notify_overdue_requests(self):
        for request in self.search([('is_overdue', '=', True)]):
            for manager in self._get_users_in_group(GROUP_MANAGER, request.company_id):
                already = request.activity_ids.filtered(
                    lambda a, manager=manager: a.user_id == manager and a.summary == OVERDUE_SUMMARY)
                if already:
                    continue
                request.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=fields.Date.context_today(request),
                    summary=OVERDUE_SUMMARY,
                    note=_('%(number)s (%(asset)s) was expected on %(date)s and is still open.',
                           number=request.request_number, asset=request.asset_id.display_name,
                           date=request.expected_completion_date),
                    user_id=manager.id,
                )
        return True

    # ------------------------------------------------------------------
    # Dashboard (called by the Owl client action)
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self):
        """Return every figure of the dashboard, computed live with the caller's access rights."""
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        month_end = month_start + relativedelta(months=1, days=-1)
        not_cancelled = [('state', '!=', 'cancelled')]
        open_domain = [('state', 'in', OPEN_STATES)]
        month_domain = [('state', '=', 'completed'),
                        ('actual_completion_date', '>=', month_start),
                        ('actual_completion_date', '<=', month_end)]

        def count(domain):
            return self.search_count(domain)

        total_cost = sum((cost or 0.0) for (cost,) in self._read_group(not_cancelled, [], ['actual_cost:sum']))
        kpis = [
            {'key': 'assets', 'label': _('Total Assets'), 'value': self.env['maintenance.asset'].search_count([]),
             'model': 'maintenance.asset', 'domain': [], 'icon': 'fa-cubes'},
            {'key': 'open', 'label': _('Open Requests'), 'value': count(open_domain),
             'model': self._name, 'domain': open_domain, 'icon': 'fa-folder-open-o'},
            {'key': 'in_progress', 'label': _('In Progress'), 'value': count([('state', '=', 'in_progress')]),
             'model': self._name, 'domain': [('state', '=', 'in_progress')], 'icon': 'fa-cogs'},
            {'key': 'overdue', 'label': _('Overdue'), 'value': count([('is_overdue', '=', True)]),
             'model': self._name, 'domain': [('is_overdue', '=', True)], 'icon': 'fa-exclamation-triangle',
             'tone': 'danger'},
            {'key': 'completed_month', 'label': _('Completed This Month'), 'value': count(month_domain),
             'model': self._name, 'domain': month_domain, 'icon': 'fa-check-circle', 'tone': 'success'},
            {'key': 'cost', 'label': _('Total Maintenance Cost'), 'value': total_cost,
             'model': self._name, 'domain': not_cancelled, 'icon': 'fa-money', 'is_money': True},
        ]

        state_labels = dict(self._fields['state']._description_selection(self.env))
        priority_labels = dict(self._fields['priority']._description_selection(self.env))
        by_state = [
            {'key': key, 'label': state_labels[key], 'value': value}
            for key, value in self._read_group([], ['state'], ['__count'])
        ]
        by_priority = [
            {'key': key, 'label': priority_labels[key], 'value': value}
            for key, value in sorted(self._read_group([], ['priority'], ['__count']))
        ]
        by_department = [
            {'key': dept.id, 'label': dept.display_name if dept else _('No department'), 'value': value}
            for dept, value in self._read_group([], ['department_id'], ['__count'])
        ]

        first_month = month_start - relativedelta(months=11)
        cost_by_month_raw = {
            fields.Date.to_date(month): cost
            for month, cost in self._read_group(
                [('state', '=', 'completed'), ('actual_completion_date', '>=', first_month)],
                ['actual_completion_date:month'], ['actual_cost:sum'])
        }
        cost_by_month = []
        for index in range(12):
            month = first_month + relativedelta(months=index)
            cost_by_month.append({'key': str(month), 'label': month.strftime('%b %y'),
                                  'value': cost_by_month_raw.get(month) or 0.0})

        currency = self.env.company.currency_id
        return {
            'kpis': kpis,
            'charts': {
                'by_state': by_state,
                'by_priority': by_priority,
                'by_department': by_department,
                'cost_by_month': cost_by_month,
            },
            'currency': {'symbol': currency.symbol, 'position': currency.position,
                         'decimals': currency.decimal_places},
        }
