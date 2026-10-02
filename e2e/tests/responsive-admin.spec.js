import { expect, test } from "@playwright/test";

async function mockAdmin(page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("admin_token", "responsive-admin-token");
  });
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/admin/session" && request.method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          email: "responsive@test.local",
          role: "viewer",
          all_access: false,
          permissions: [],
        }),
      });
    }
    if (url.pathname === "/api/admin/logout" && request.method() === "POST") {
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ detail: `Unmocked ${request.method()} ${url.pathname}` }),
    });
  });
}

async function expectNoHorizontalOverflow(page) {
  const geometry = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    bodyWidth: document.body.scrollWidth,
  }));
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  expect(geometry.bodyWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1);
}

test("Admin remains readable and bounded on tablet viewport", async ({ page }) => {
  await mockAdmin(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "FLASHIN Admin" })).toBeVisible();
  await expect(page.getByText("responsive@test.local · viewer")).toBeVisible();
  await expect(page.getByRole("button", { name: "Обновить" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const header = page.locator("header").first();
  const box = await header.boundingBox();
  expect(box).not.toBeNull();
  expect(box.width).toBeLessThanOrEqual(1024);

  const buttons = header.getByRole("button");
  const count = await buttons.count();
  expect(count).toBeGreaterThanOrEqual(2);
  for (let index = 0; index < count; index += 1) {
    const buttonBox = await buttons.nth(index).boundingBox();
    expect(buttonBox).not.toBeNull();
    expect(buttonBox.width).toBeGreaterThanOrEqual(44);
    expect(buttonBox.height).toBeGreaterThanOrEqual(34);
  }
});
