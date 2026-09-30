# -*- coding: utf-8 -*-
import re
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger

from odoo.addons.smart_maintenance.models.maintenance_plan import PLAN_DUE_SUMMARY, PLAN_OVERDUE_SUMMARY
from odoo.addons.smart_maintenance.models.maintenance_request import ASSIGN_SUMMARY, OVERDUE_SUMMARY


@tagged('post_install', '-at_install', 'smart_maintenance')
class TestSmartMaintenance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user_group = 'smart_maintenance.group_maintenance_user'
        manager_group = 'smart_maintenance.group_maintenance_manager'
        cls.manager = new_test_user(cls.env, login='sm_manager', groups=f'base.group_user,{manager_group}')
        cls.tech = new_test_user(cls.env, login='sm_tech', groups=f'base.group_user,{user_group}')
        cls.tech2 = new_test_user(cls.env, login='sm_tech2', groups=f'base.group_user,{user_group}')
        cls.outsider = new_test_user(cls.env, login='sm_outsider', groups='base.group_user')
        cls.Asset = cls.env['maintenance.asset']
        cls.Request = cls.env['maintenance.request']
        cls.Plan = cls.env['maintenance.plan']
        cls.Part = cls.env['maintenance.part']
        cls.asset = cls.Asset.create({'name': 'Test Air Conditioner', 'asset_type': 'hvac'})

    # ------------------------------------------------------------------ helpers
    def _today(self):
        return fields.Date.context_today(self.Request)

    def _make_request(self, **values):
        vals = {'name': 'Test request', 'asset_id': self.asset.id}
        vals.update(values)
        return self.Request.create(vals)

    def _run_workflow_to_progress(self, request):
        request.write({'assigned_technician_id': self.tech.id})
        request.action_review()
        request.action_assign()
        request.action_start()

    # ------------------------------------------------------------------ 1. asset code
    def test_01_asset_code_generation(self):
        first = self.Asset.create({'name': 'Asset A'})
        second = self.Asset.create({'name': 'Asset B'})
        self.assertRegex(first.asset_code, r'^AST/\d{4}$')
        self.assertRegex(second.asset_code, r'^AST/\d{4}$')
        self.assertEqual(int(second.asset_code[4:]), int(first.asset_code[4:]) + 1)
        self.assertNotEqual(first.asset_code, second.asset_code)

    # ------------------------------------------------------------------ 2. request sequence
    def test_02_request_sequence(self):
        first = self._make_request()
        second = self._make_request()
        self.assertRegex(first.request_number, r'^MR/\d{4}/\d{4}$')
        self.assertEqual(int(second.request_number[-4:]), int(first.request_number[-4:]) + 1)
        self.assertEqual(first.request_number[:8], second.request_number[:8])

    # ------------------------------------------------------------------ 3-4. parts and costs
    def test_03_parts_total_calculation(self):
        request = self._make_request()
        part = self.Part.create({'request_id': request.id, 'product_name': 'Filter',
                                 'quantity': 3, 'unit_cost': 12.5})
        self.assertAlmostEqual(part.total_cost, 37.5)
        part.write({'quantity': 4})
        self.assertAlmostEqual(part.total_cost, 50.0)

    def test_04_actual_cost_calculation(self):
        request = self._make_request(service_cost=100.0)
        self.Part.create({'request_id': request.id, 'product_name': 'Filter', 'quantity': 2, 'unit_cost': 10})
        self.Part.create({'request_id': request.id, 'product_name': 'Gas', 'quantity': 1, 'unit_cost': 30})
        self.assertAlmostEqual(request.parts_cost, 50.0)
        self.assertAlmostEqual(request.actual_cost, 150.0)
        request.service_cost = 20.0
        self.assertAlmostEqual(request.actual_cost, 70.0)
        request.part_ids[0].unlink()
        self.assertAlmostEqual(request.actual_cost, 50.0)

    def test_04b_parts_frozen_after_completion(self):
        request = self._make_request()
        self._run_workflow_to_progress(request)
        request.action_complete()
        with self.assertRaises(UserError):
            self.Part.create({'request_id': request.id, 'product_name': 'Late part', 'quantity': 1})

    # ------------------------------------------------------------------ 5. overdue
    def test_05_overdue_calculation(self):
        today = self._today()
        late = self._make_request(request_date=today - timedelta(days=10),
                                  expected_completion_date=today - timedelta(days=1))
        on_time = self._make_request(expected_completion_date=today + timedelta(days=3))
        no_date = self._make_request()
        self.assertTrue(late.is_overdue)
        self.assertFalse(on_time.is_overdue)
        self.assertFalse(no_date.is_overdue)

        found = self.Request.search([('is_overdue', '=', True), ('id', 'in', (late | on_time | no_date).ids)])
        self.assertEqual(found, late)
        not_found = self.Request.search([('is_overdue', '!=', True), ('id', 'in', (late | on_time | no_date).ids)])
        self.assertEqual(not_found, on_time | no_date)

        self._run_workflow_to_progress(late)
        late.action_complete()
        self.assertFalse(late.is_overdue, 'A completed request is never overdue.')

    # ------------------------------------------------------------------ 6-7. completion
    def test_06_completion_date_and_lock(self):
        request = self._make_request()
        self.assertFalse(request.actual_completion_date)
        self._run_workflow_to_progress(request)
        request.action_complete()
        self.assertEqual(request.state, 'completed')
        self.assertEqual(request.actual_completion_date, self._today())
        with self.assertRaises(UserError):
            request.write({'description': 'Editing a completed request'})

    def test_07_maintenance_duration(self):
        request = self._make_request(request_date=self._today() - timedelta(days=10))
        self.assertEqual(request.maintenance_duration, 0)
        self._run_workflow_to_progress(request)
        request.action_complete()
        self.assertEqual(request.maintenance_duration, 10)

    # ------------------------------------------------------------------ 8. preventive plans
    def test_08_plan_next_date_calculation(self):
        base = date(2026, 1, 31)
        cases = [
            ('days', 10, date(2026, 2, 10)),
            ('weeks', 2, date(2026, 2, 14)),
            ('months', 2, date(2026, 3, 31)),
            ('months', 1, date(2026, 2, 28)),  # month-end is clamped
            ('years', 1, date(2027, 1, 31)),
        ]
        for frequency, interval, expected in cases:
            with self.subTest(frequency=frequency, interval=interval):
                plan = self.Plan.create({
                    'name': 'Plan', 'asset_id': self.asset.id, 'frequency': frequency,
                    'interval': interval, 'next_maintenance_date': base})
                plan.action_complete_maintenance(base)
                self.assertEqual(plan.last_maintenance_date, base)
                self.assertEqual(plan.next_maintenance_date, expected)

    @mute_logger('odoo.sql_db')
    def test_08b_plan_interval_must_be_positive(self):
        with self.assertRaises(Exception):  # database CHECK constraint
            with self.cr.savepoint():
                self.Plan.create({'name': 'Bad', 'asset_id': self.asset.id, 'interval': 0,
                                  'next_maintenance_date': self._today()})

    def test_08c_completing_a_preventive_request_reschedules_the_plan(self):
        today = self._today()
        plan = self.Plan.create({
            'name': 'Monthly check', 'asset_id': self.asset.id, 'technician_id': self.tech.id,
            'frequency': 'months', 'interval': 1, 'next_maintenance_date': today})
        plan.action_create_request()
        plan.invalidate_recordset(['request_ids'])
        request = plan.request_ids
        self.assertEqual(request.request_type, 'preventive')
        self.assertEqual(request.assigned_technician_id, self.tech)
        with self.assertRaises(UserError):  # only one open request per plan
            plan.action_create_request()
        request.write({'state': 'under_review'})
        request.write({'state': 'assigned'})
        request.write({'state': 'in_progress'})
        request.action_complete()
        self.assertEqual(plan.last_maintenance_date, request.actual_completion_date)
        self.assertEqual(plan.next_maintenance_date,
                         request.actual_completion_date + relativedelta(months=1))

    # ------------------------------------------------------------------ workflow
    def test_09_workflow_rules(self):
        request = self._make_request()
        with self.assertRaises(UserError):
            request.write({'state': 'completed'})  # cannot skip steps
        request.action_review()
        with self.assertRaises(UserError):
            request.action_assign()  # no technician selected yet
        request.write({'assigned_technician_id': self.tech.id})
        request.action_assign()
        request.action_start()
        request.action_wait_parts()
        self.assertEqual(request.state, 'waiting_parts')
        request.action_resume()
        self.assertEqual(request.state, 'in_progress')
        request.action_cancel()
        self.assertEqual(request.state, 'cancelled')
        with self.assertRaises(UserError):
            request.write({'name': 'Renamed after cancellation'})

    def test_09b_asset_status_follows_the_work(self):
        request = self._make_request()
        self._run_workflow_to_progress(request)
        self.assertEqual(self.asset.status, 'under_maintenance')
        request.action_complete()
        self.assertEqual(self.asset.status, 'active')

    def test_09c_assignment_activity(self):
        request = self._make_request()
        request.action_review()
        request.write({'assigned_technician_id': self.tech.id})
        request.with_user(self.manager).action_assign()
        activities = request.activity_ids.filtered(lambda a: a.summary == ASSIGN_SUMMARY)
        self.assertEqual(activities.user_id, self.tech)
        # Re-assigning moves the activity to the new technician, without duplicates.
        request.with_user(self.manager).write({'assigned_technician_id': self.tech2.id})
        activities = request.activity_ids.filtered(lambda a: a.summary == ASSIGN_SUMMARY)
        self.assertEqual(activities.user_id, self.tech2)

    def test_09d_completion_notifies_requester(self):
        request = self._make_request(requested_by=self.tech2.id)
        self._run_workflow_to_progress(request)
        request.action_complete()
        self.assertTrue(request.activity_ids.filtered(
            lambda a: a.user_id == self.tech2 and a.summary == 'Maintenance request completed'))

    # ------------------------------------------------------------------ 9. security
    def test_10_user_without_group_has_no_access(self):
        with self.assertRaises(AccessError):
            self.Request.with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            self.Asset.with_user(self.outsider).search([])

    def test_10b_user_can_read_assets_but_not_write_them(self):
        assets = self.Asset.with_user(self.tech).search([('id', '=', self.asset.id)])
        self.assertEqual(assets, self.asset)
        with self.assertRaises(AccessError):
            assets.write({'name': 'Hacked'})
        with self.assertRaises(AccessError):
            self.Asset.with_user(self.tech).create({'name': 'Not allowed'})

    def test_10c_user_creates_own_request_but_cannot_set_manager_fields(self):
        request = self.Request.with_user(self.tech).create({'name': 'Broken lamp', 'asset_id': self.asset.id})
        self.assertEqual(request.requested_by, self.tech)
        self.assertRegex(request.request_number, r'^MR/\d{4}/\d{4}$')
        with self.assertRaises(AccessError):
            self.Request.with_user(self.tech).create({
                'name': 'Sneaky', 'asset_id': self.asset.id, 'assigned_technician_id': self.tech.id})
        with self.assertRaises(AccessError):
            request.write({'service_cost': 50.0})
        with self.assertRaises(AccessError):
            request.unlink()

    def test_10d_user_updates_only_assigned_requests(self):
        mine = self._make_request(requested_by=self.manager.id)
        mine.write({'assigned_technician_id': self.tech.id})
        mine.action_review()
        mine.action_assign()
        others = self._make_request(requested_by=self.manager.id)
        others.write({'assigned_technician_id': self.tech2.id})

        mine.with_user(self.tech).write({'resolution_notes': 'Working on it'})
        self.assertEqual(mine.resolution_notes, 'Working on it')
        mine.with_user(self.tech).action_start()
        self.assertEqual(mine.state, 'in_progress')
        with self.assertRaises(AccessError):
            others.with_user(self.tech).write({'resolution_notes': 'Not mine'})
        with self.assertRaises(AccessError):
            mine.with_user(self.tech).action_cancel()  # cancelling is a manager privilege

    def test_10e_manager_can_do_everything(self):
        request = self.Request.with_user(self.manager).create({'name': 'Managed', 'asset_id': self.asset.id})
        request.action_review()
        request.write({'assigned_technician_id': self.tech.id, 'service_cost': 75.0})
        request.action_assign()
        request.action_cancel()
        self.assertEqual(request.state, 'cancelled')
        request.action_reset_to_new()
        self.assertEqual(request.state, 'new')

    def test_10f_plan_security(self):
        plan = self.Plan.create({'name': 'Secured plan', 'asset_id': self.asset.id,
                                 'technician_id': self.tech.id, 'next_maintenance_date': self._today()})
        self.assertEqual(self.Plan.with_user(self.tech).search([('id', '=', plan.id)]), plan)
        self.assertFalse(self.Plan.with_user(self.tech2).search([('id', '=', plan.id)]))
        with self.assertRaises(AccessError):
            self.Plan.with_user(self.tech).create({
                'name': 'Not allowed', 'asset_id': self.asset.id, 'next_maintenance_date': self._today()})
        plan.with_user(self.tech).action_complete_maintenance()
        self.assertEqual(plan.last_maintenance_date, self._today())

    def test_10g_technician_must_belong_to_the_maintenance_group(self):
        request = self._make_request()
        with self.assertRaises(ValidationError):
            request.write({'assigned_technician_id': self.outsider.id})

    # ------------------------------------------------------------------ cron
    def test_11_cron_overdue_requests(self):
        today = self._today()
        request = self._make_request(request_date=today - timedelta(days=10),
                                     expected_completion_date=today - timedelta(days=2))
        self.Request._cron_notify_overdue_requests()
        self.Request._cron_notify_overdue_requests()  # idempotent
        activities = request.activity_ids.filtered(
            lambda a: a.summary == OVERDUE_SUMMARY and a.user_id == self.manager)
        self.assertEqual(len(activities), 1)

    def test_12_cron_preventive_plans(self):
        today = self._today()
        soon = self.Plan.create({'name': 'Soon', 'asset_id': self.asset.id, 'technician_id': self.tech.id,
                                 'next_maintenance_date': today + timedelta(days=3), 'reminder_days': 7})
        late = self.Plan.create({'name': 'Late', 'asset_id': self.asset.id, 'technician_id': self.tech2.id,
                                 'next_maintenance_date': today - timedelta(days=3)})
        far = self.Plan.create({'name': 'Far', 'asset_id': self.asset.id, 'technician_id': self.tech.id,
                                'next_maintenance_date': today + timedelta(days=90)})
        no_technician = self.Plan.create({'name': 'Unassigned', 'asset_id': self.asset.id,
                                          'next_maintenance_date': today})
        self.assertTrue(late.is_overdue)
        self.assertEqual(self.Plan.search([('is_overdue', '=', True), ('id', 'in', (soon | late | far).ids)]), late)
        self.Plan._cron_check_maintenance_plans()
        self.Plan._cron_check_maintenance_plans()  # idempotent
        self.assertEqual(soon.activity_ids.filtered(lambda a: a.summary == PLAN_DUE_SUMMARY).user_id, self.tech)
        self.assertEqual(late.activity_ids.filtered(lambda a: a.summary == PLAN_OVERDUE_SUMMARY).user_id, self.tech2)
        self.assertFalse(far.activity_ids)
        # No technician -> managers are notified.
        self.assertIn(self.manager, no_technician.activity_ids.user_id)
        # Completing the maintenance closes the reminders and moves the date forward.
        soon.action_complete_maintenance()
        self.assertFalse(soon.activity_ids.filtered(lambda a: a.summary == PLAN_DUE_SUMMARY))

    # ------------------------------------------------------------------ dashboard
    def test_13_dashboard_data_comes_from_records(self):
        request = self._make_request(priority='3', service_cost=200.0)
        self._run_workflow_to_progress(request)
        request.action_complete()
        data = self.Request.get_dashboard_data()
        kpis = {kpi['key']: kpi for kpi in data['kpis']}
        self.assertEqual(set(kpis), {'assets', 'open', 'in_progress', 'overdue', 'completed_month', 'cost'})
        self.assertEqual(kpis['assets']['value'], self.Asset.search_count([]))
        self.assertGreaterEqual(kpis['completed_month']['value'], 1)
        self.assertGreaterEqual(kpis['cost']['value'], 200.0)
        self.assertEqual(len(data['charts']['cost_by_month']), 12)
        self.assertTrue(any(row['key'] == '3' for row in data['charts']['by_priority']))
        self.assertIn('symbol', data['currency'])
