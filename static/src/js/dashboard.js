/** @odoo-module **/
// Architecture note: a client action (ir.actions.client, tag "smart_maintenance.dashboard")
// is looked up in the "actions" registry and rendered as an Owl component.
// All numbers come from maintenance.request.get_dashboard_data() (live ORM queries);
// nothing is hard-coded here.
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class MaintenanceDashboard extends Component {
    static template = "smart_maintenance.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null });
        onWillStart(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        this.state.data = await this.orm.call("maintenance.request", "get_dashboard_data", []);
        this.state.loading = false;
    }

    formatNumber(value, decimals = 0) {
        return new Intl.NumberFormat(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        }).format(value || 0);
    }

    formatMoney(value) {
        const { symbol, position, decimals } = this.state.data.currency;
        const amount = this.formatNumber(value, decimals);
        return position === "before" ? `${symbol} ${amount}` : `${amount} ${symbol}`;
    }

    kpiValue(kpi) {
        return kpi.is_money ? this.formatMoney(kpi.value) : this.formatNumber(kpi.value);
    }

    /** Width (in %) of a bar relative to the largest value of its chart. */
    barPercent(rows, row) {
        const max = Math.max(...rows.map((r) => r.value), 0);
        return max ? Math.max((row.value / max) * 100, row.value ? 3 : 0) : 0;
    }

    openKpi(kpi) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: kpi.label,
            res_model: kpi.model,
            views: [[false, "list"], [false, "form"]],
            domain: kpi.domain,
        });
    }

    openRequests(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "maintenance.request",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    openState(row) {
        this.openRequests([["state", "=", row.key]], row.label);
    }

    openPriority(row) {
        this.openRequests([["priority", "=", row.key]], row.label);
    }

    openDepartment(row) {
        this.openRequests([["department_id", "=", row.key || false]], row.label);
    }
}

registry.category("actions").add("smart_maintenance.dashboard", MaintenanceDashboard);
