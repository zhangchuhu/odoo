import { Component } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

export const storeSelectionService = {
    dependencies: ["orm"],
    async start(env, { orm }) {
        if (!(await user.hasGroup("meli_oerp.group_mercadolibre_manager"))) {
            return { enabled: false };
        }
        const stores = await orm.searchRead("meli.independent.store", [["state", "=", "connected"]], ["name"], { order: "id" });
        const key = `meli.currentStore.${session.db}.${user.userId}`;
        let selection;
        try {
            selection = JSON.parse(browser.localStorage.getItem(key));
        } catch {
            selection = null;
        }
        if (selection !== "unassigned" && !stores.some((store) => store.id === selection)) {
            selection = stores[0]?.id || "unassigned";
        }
        // Odoo restores the previous action dictionary from sessionStorage,
        // including its domain, even after a full page reload.
        const loadedKey = `${key}.loaded`;
        if (browser.sessionStorage.getItem(loadedKey) !== JSON.stringify(selection)) {
            browser.sessionStorage.removeItem("current_action");
            browser.sessionStorage.removeItem("current_state");
            browser.sessionStorage.setItem(loadedKey, JSON.stringify(selection));
        }
        user.updateContext({ meli_store_selection: selection });
        return {
            enabled: true,
            stores,
            selection,
            label: selection === "unassigned" ? "待归属商品" : stores.find((store) => store.id === selection).name,
            select(value) {
                if (value !== "unassigned" && !stores.some((store) => store.id === value)) {
                    return;
                }
                browser.localStorage.setItem(key, JSON.stringify(value));
                browser.sessionStorage.removeItem("current_action");
                browser.sessionStorage.removeItem("current_state");
                // Reload clears prior action domains, saved pagination and open product forms.
                browser.location.assign("/odoo/meli-store-products");
            },
        };
    },
};
registry.category("services").add("meli_store_selection", storeSelectionService);

export class StoreSwitcher extends Component {
    static template = "meli_accounts.StoreSwitcher";
    static components = { Dropdown, DropdownItem };
    static props = {};
    setup() {
        this.store = useService("meli_store_selection");
    }
}
registry.category("systray").add("meli_accounts.StoreSwitcher", { Component: StoreSwitcher }, { sequence: 2 });
