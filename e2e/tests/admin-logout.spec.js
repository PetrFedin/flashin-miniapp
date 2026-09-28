import { expect, test } from "@playwright/test";


test("Admin explicit logout revokes the exact browser session before replacement login", async ({ page }) => {
  let logoutAuthorization = "";
  let logoutCalls = 0;

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
      return json({ access_token: "admin-logout-browser-token" });
    }
    if (path === "/api/admin/session" && method === "GET") {
      return json({
        id: 1,
        email: "logout@flashin.test",
        role: "manager",
        all_access: false,
        permissions: [],
      });
    }
    if (path === "/api/admin/logout" && method === "POST") {
      logoutCalls += 1;
      logoutAuthorization = request.headers().authorization || "";
      return route.fulfill({
        status: 204,
        headers: { "cache-control": "no-store, max-age=0" },
        body: "",
      });
    }

    return json({ detail: `Unexpected ${method} ${path}` }, 501);
  });

  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill("logout@flashin.test");
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("admin_token"))).toBe(
    "admin-logout-browser-token",
  );

  const logoutRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/admin/logout" && request.method() === "POST";
  });
  await page.getByRole("button", { name: "Выйти" }).click();
  await logoutRequest;

  await expect(page.getByRole("button", { name: "Войти" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("admin_token"))).toBeNull();
  await expect.poll(() => logoutCalls).toBe(1);
  expect(logoutAuthorization).toBe("Bearer admin-logout-browser-token");
});
