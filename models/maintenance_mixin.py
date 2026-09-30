# -*- coding: utf-8 -*-
from odoo import api, fields, models

GROUP_USER = 'smart_maintenance.group_maintenance_user'
GROUP_MANAGER = 'smart_maintenance.group_maintenance_manager'


class MaintenanceAccessMixin(models.AbstractModel):
    """Shared helpers for role checks.

    Architecture note: security is enforced server-side (ACL + record rules +
    guards in ``write``/``create``). ``is_manager`` only exists so that views can
    render fields read-only for non-managers; it is never the only protection.
    """
    _name = 'maintenance.access.mixin'
    _description = 'Maintenance Access Helpers'

    is_manager = fields.Boolean(compute='_compute_is_manager')

    @api.depends_context('uid')
    def _compute_is_manager(self):
        is_manager = self._is_maintenance_manager()
        for record in self:
            record.is_manager = is_manager

    @api.model
    def _is_maintenance_manager(self):
        return bool(self.env.su or self.env.user.has_group(GROUP_MANAGER))

    @api.model
    def _get_users_in_group(self, group_xmlid, company=None):
        """Internal users belonging to a group (optionally limited to a company).

        Done with ``has_group`` on purpose: it does not depend on the technical
        name of the users<->groups relation field, which differs between versions.
        """
        users = self.env['res.users'].sudo().search([('share', '=', False)])
        users = users.filtered(lambda user: user.has_group(group_xmlid))
        if company:
            users = users.filtered(lambda user: company in user.company_ids)
        return users
