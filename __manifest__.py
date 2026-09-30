# -*- coding: utf-8 -*-
{
    'name': 'Smart Maintenance Operations',
    'version': '19.0.1.0.0',
    'category': 'Operations/Maintenance',
    'summary': 'Assets, maintenance requests, parts & costs, preventive plans and a live dashboard',
    'description': """
Smart Maintenance Operations
============================
* Asset register with automatic codes and smart buttons
* Maintenance requests with a controlled workflow, costs and activities
* Parts used per request, automatic cost roll-up
* Preventive maintenance plans with scheduled reminders
* Owl dashboard built from live data, QWeb PDF report, security groups and rules
    """,
    'author': 'Technical demonstration',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'hr'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        # The report action must exist before the request form references it.
        'report/maintenance_report.xml',
        'report/maintenance_report_template.xml',
        'views/asset_views.xml',
        'views/request_views.xml',
        'views/plan_views.xml',
        'views/dashboard_views.xml',
        'views/menu_views.xml',
        'data/cron.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'smart_maintenance/static/src/css/dashboard.scss',
            'smart_maintenance/static/src/js/dashboard.js',
            'smart_maintenance/static/src/xml/dashboard.xml',
        ],
    },
    'pre_init_hook': 'pre_init_check',
    'application': True,
    'installable': True,
    'auto_install': False,
}
