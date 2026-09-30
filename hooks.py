# -*- coding: utf-8 -*-
from odoo.exceptions import UserError


def pre_init_check(env):
    """Refuse installation next to Odoo's standard ``maintenance`` app.

    Both apps declare the models ``maintenance.request`` and ``maintenance.plan``.
    Installing them in the same database would silently merge two unrelated
    schemas into the same tables, so we stop early with a clear message.
    """
    conflicting = env['ir.module.module'].search([
        ('name', '=', 'maintenance'),
        ('state', 'in', ('installed', 'to install', 'to upgrade')),
    ], limit=1)
    if conflicting:
        raise UserError(
            "Smart Maintenance Operations cannot be installed together with Odoo's "
            "standard 'Maintenance' app: both define the models maintenance.request "
            "and maintenance.plan. Use a database where 'maintenance' is not installed."
        )
