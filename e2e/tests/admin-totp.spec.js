import { expect, test } from "@playwright/test";

const SESSION = {
  id: 91,
  email: "pilot-2fa@flashin.test",
  role: "observer",
  all_access: false,
  permissions: [],
};


test("admin login transports optional TOTP and clears the one-time code", async ({ page }) => {
  const loginBodies = [];

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
      loginBodies.push(request.postDataJSON());
      return json({ access_token: `token-${loginBodies.length}` });
    }
    if (path === "/api/admin/session" && method === "GET") return json(SESSION);
    return json({ detail: `Unexpected ${method} ${path}` }, 501);
  });

  await page.goto("/");
  await page.getByPlaceholder("Email администратора").fill(SESSION.email);
  await page.getByPlaceholder("Пароль").fill("pilot-password");
  await page.getByPlaceholder("Код 2FA (если включён)").fill("123456");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page.getByText(`${SESSION.email} · ${SESSION.role}`, { exact: true })).toBeVisible();
  expect(loginBodies).toHaveLength(1);
  expect(loginBodies[0]).toEqual({
    email: SESSION.email,
    password: "pilot-password",
    totp_code: "123456",
  });

  await page.getByRole("button", { name: "Выйти" }).click();
  const totpInput = page.getByPlaceholder("Код 2FA (если включён)");
  await expect(totpInput).toHaveValue("");

  await page.getByPlaceholder("Email администратора").fill(SESSION.email);
  await page.getByPlaceholder("Пароль").fill("pilot-password-2");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText(`${SESSION.email} · ${SESSION.role}`, { exact: true })).toBeVisible();

  expect(loginBodies).toHaveLength(2);
  expect(loginBodies[1]).toEqual({
    email: SESSION.email,
    password: "pilot-password-2",
  });
  expect(Object.hasOwn(loginBodies[1], "totp_code")).toBe(false);
});
