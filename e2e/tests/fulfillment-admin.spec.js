import { expect, test } from "@playwright/test";

async function mockFulfillmentApi(page) {
  const state = {
    task: {
      id: 9101,
      order_id: 9100,
      status: "new",
      assigned_admin_id: null,
      comment: "",
    },
    taskItem: {
      task_item_id: 9102,
      order_item_id: 9103,
      title: "Pilot Fulfillment Jacket",
      size: "M",
      quantity: 2,
      picked_qty: 0,
      status: "to_pick",
      issue: "",
    },
    shipment: null,
    order: {
      id: 9100,
      status: "paid",
      payment_status: "paid",
      delivery_status: "not_started",
      total_amount: 18000,
      currency: "RUB",
      customer: { first_name: "Fulfillment Pilot" },
      items: [{ id: 9103, title: "Pilot Fulfillment Jacket", size: "M", quantity: 2 }],
    },
  };

  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

    if (path === "/api/admin/login" && method === "POST") {
      return json({ access_token: "fulfillment-admin-token" });
    }
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 42,
        email: "fulfillment@flashin.test",
        role: "warehouse",
        all_access: false,
        permissions: ["orders.read", "fulfillment.write"],
      });
    }
    if (path === "/api/admin/products" && method === "GET") return json([]);
    if (path === "/api/admin/orders" && method === "GET") return json([state.order]);
    if (path === "/api/admin/audit-logs" && method === "GET") return json([]);
    if (path === "/api/ops/inventory/low-stock" && method === "GET") return json([]);
    if (path === "/api/ops/abandoned-carts" && method === "GET") return json([]);
    if (path === "/api/ops/pilot-runtime" && method === "GET") {
      return json({
        schema_version: 1,
        checkout_decision: "NO-GO",
        generated_at: "2026-08-05T00:00:00Z",
        enforced: true,
        runtime: {
          present: false,
          status: "missing",
          max_orders: 20,
          accepted_orders: 0,
          remaining_orders: 0,
        },
        database_integrity: { healthy: true, codes: [] },
        artifact_integrity: { applicable: false, healthy: false, codes: [] },
        money_attention: {
          payment_review_orders: 0,
          refund_attention_orders: 0,
          reconciliation_mismatches: 0,
          attention_required: false,
        },
      });
    }
    if (path === "/api/support/admin/tickets" && method === "GET") return json([]);
    if (path === "/api/privacy/admin/requests" && method === "GET") return json([]);
    if (path === "/api/admin/returns" && method === "GET") return json([]);
    if (path === "/api/platform/admin/events/summary" && method === "GET") {
      return json({ counts: { failed: 0, pending: 0, processed: 0 }, oldest_failed_at: null });
    }
    if (path === "/api/platform/admin/events" && method === "GET") return json([]);

    if (path === "/api/fulfillment/tasks" && method === "GET") {
      return json([state.task]);
    }
    if (path === "/api/fulfillment/sla" && method === "GET") {
      return json([{
        id: 9104,
        order_id: 9100,
        event_type: state.task.status === "ready" ? "assembling_to_ready" : "paid_to_assembling",
        due_at: "2026-08-05T02:00:00Z",
        status: state.task.status === "ready" ? "resolved" : "open",
      }]);
    }
    if (path === "/api/delivery-providers/shipments" && method === "GET") {
      return json(state.shipment ? [state.shipment] : []);
    }
    if (path === "/api/fulfillment/tasks/9101" && method === "PATCH") {
      const body = request.postDataJSON();
      const allowed = {
        new: ["picking"],
        picking: ["packed"],
        packed: ["ready"],
      };
      if (!allowed[state.task.status]?.includes(body.status)) {
        return json({ detail: `Invalid task transition ${state.task.status} -> ${body.status}` }, 409);
      }
      if (body.status === "packed" && (
        state.taskItem.status !== "picked"
        || state.taskItem.picked_qty !== state.taskItem.quantity
      )) {
        return json({ detail: "Every picklist item must be fully picked before packing" }, 409);
      }
      state.task = {
        ...state.task,
        status: body.status,
        assigned_admin_id: state.task.assigned_admin_id || 42,
        comment: body.comment || "",
      };
      if (body.status === "picking") {
        state.order.status = "assembling";
        state.order.delivery_status = "assembling";
      }
      if (body.status === "ready") {
        state.order.status = "ready";
        state.order.delivery_status = "ready";
      }
      return json(state.task);
    }
    if (path === "/api/fulfillment/tasks/9101/picklist" && method === "GET") {
      return json({
        task_id: 9101,
        order_id: 9100,
        status: state.task.status,
        items: [state.taskItem],
      });
    }
    if (path === "/api/fulfillment/task-items/9102" && method === "PATCH") {
      state.taskItem = {
        ...state.taskItem,
        picked_qty: Number(url.searchParams.get("picked_qty")),
        status: url.searchParams.get("status"),
      };
      return json({
        ok: true,
        task_item_id: 9102,
        status: state.taskItem.status,
        picked_qty: state.taskItem.picked_qty,
        ordered_qty: state.taskItem.quantity,
        issue: "",
      });
    }
    if (path === "/api/delivery-providers/orders/9100/shipment" && method === "POST") {
      if (state.order.status !== "ready" || state.order.delivery_status !== "ready") {
        return json({ detail: "Only a ready order can be transferred to delivery" }, 409);
      }
      state.shipment = state.shipment || {
        id: 9201,
        order_id: 9100,
        provider_code: url.searchParams.get("provider_code") || "courier",
        tracking_number: "",
        status: "created",
        price: 500,
      };
      return json(state.shipment);
    }
    if (path === "/api/delivery-providers/shipments/9201" && method === "PATCH") {
      const nextStatus = url.searchParams.get("status");
      const tracking = url.searchParams.get("tracking_number") || state.shipment.tracking_number;
      if (nextStatus === "shipped") {
        state.shipment = { ...state.shipment, status: "shipped", tracking_number: tracking };
        state.order = {
          ...state.order,
          status: "shipped",
          delivery_status: "shipped",
          tracking_number: tracking,
        };
      } else if (nextStatus === "delivered") {
        state.shipment = { ...state.shipment, status: "delivered" };
        state.order = {
          ...state.order,
          status: "completed",
          delivery_status: "delivered",
        };
      } else {
        return json({ detail: "Unsupported shipment status" }, 409);
      }
      return json(state.shipment);
    }

    return json({ detail: `Unmocked ${method} ${path}` }, 501);
  });

  return state;
}

async function login(page) {
  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill("fulfillment@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
}

test("Admin completes picklist, shipment and delivery lifecycle", async ({ page }) => {
  const state = await mockFulfillmentApi(page);
  await login(page);

  await expect(page.getByRole("heading", { name: "Fulfillment & Delivery" })).toBeVisible();
  const task = page.getByRole("article", { name: "Заказ #9100" });
  await expect(task.getByText("Новая задача")).toBeVisible();

  await task.getByRole("button", { name: "Начать сборку" }).click();
  await expect(page.getByRole("status")).toContainText("Сборка заказа #9100 начата");
  await expect(task.getByText("Сборка", { exact: true })).toBeVisible();
  expect(state.order.delivery_status).toBe("assembling");

  await task.getByRole("button", { name: "Собрать все позиции и упаковать" }).click();
  await expect(page.getByRole("status")).toContainText("полностью собран и упакован");
  await expect(task.getByText("Упакован", { exact: true })).toBeVisible();
  expect(state.taskItem.status).toBe("picked");
  expect(state.taskItem.picked_qty).toBe(2);

  await task.getByRole("button", { name: "Подтвердить готовность" }).click();
  await expect(page.getByRole("status")).toContainText("готов к передаче в доставку");
  await expect(task.getByText("Готов к передаче", { exact: true })).toBeVisible();
  expect(state.order.status).toBe("ready");

  await task.getByRole("button", { name: "Создать отгрузку" }).click();
  await expect(page.getByRole("status")).toContainText("Отгрузка заказа #9100 создана");
  await expect(task.getByText("Создана", { exact: true })).toBeVisible();

  await task.getByLabel("Трек-номер заказа 9100").fill("PILOT-TRACK-9100");
  await task.getByRole("button", { name: "Передать в доставку" }).click();
  await expect(page.getByRole("status")).toContainText("передан в доставку");
  await expect(task.getByText("Отправлена", { exact: true })).toBeVisible();
  await expect(task.getByText(/PILOT-TRACK-9100/)).toBeVisible();
  expect(state.order.status).toBe("shipped");

  page.once("dialog", (dialog) => dialog.accept());
  await task.getByRole("button", { name: "Подтвердить доставку" }).click();
  await expect(page.getByRole("status")).toContainText("доставлен и завершён");
  await expect(task.getByText("Доставлена", { exact: true })).toBeVisible();
  await expect(task.getByText("Цикл завершён")).toBeVisible();

  expect(state.order.status).toBe("completed");
  expect(state.order.delivery_status).toBe("delivered");
  expect(state.shipment.status).toBe("delivered");
  expect(state.shipment.tracking_number).toBe("PILOT-TRACK-9100");
});
