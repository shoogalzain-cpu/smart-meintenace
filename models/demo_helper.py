# -*- coding: utf-8 -*-
from odoo import Command, api, models

from .maintenance_mixin import GROUP_MANAGER, GROUP_USER


class MaintenanceDemoHelper(models.AbstractModel):
    """Used only by demo/demo_data.xml to give the demo users their groups."""
    _name = 'maintenance.demo.helper'
    _description = 'Maintenance Demo Helper'

    @api.model
    def _setup_demo_users(self):
        users_model = self.env['res.users']
        # The relation field was renamed in recent versions; resolve it explicitly
        # instead of guessing, so a wrong name can never fail silently.
        field_name = 'group_ids' if 'group_ids' in users_model._fields else 'groups_id'
        mapping = {
            'smart_maintenance.demo_user_manager': GROUP_MANAGER,
            'smart_maintenance.demo_user_tech_1': GROUP_USER,
            'smart_maintenance.demo_user_tech_2': GROUP_USER,
            'smart_maintenance.demo_user_tech_3': GROUP_USER,
        }
        for user_xmlid, group_xmlid in mapping.items():
            user = self.env.ref(user_xmlid)
            group = self.env.ref(group_xmlid)
            user.write({field_name: [Command.link(group.id)]})
