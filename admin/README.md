# FLASHIN Admin v28

Backend admin API is now available.

## Login

POST `/api/admin/login`

```json
{
  "email": "admin@flashin.store",
  "password": "change-this-before-launch"
}
```

Returns JWT. Use it as:

```http
Authorization: Bearer <token>
```

## Endpoints

- `GET /api/admin/products`
- `POST /api/admin/products`
- `PATCH /api/admin/products/{id}/active`
- `PATCH /api/admin/variants/{id}/stock`
- `GET /api/admin/orders`
- `PATCH /api/admin/orders/{id}`
- `POST /api/admin/promocodes`
- `GET /api/admin/notifications`
- `POST /api/media/upload`

## Still required before launch

A React admin UI should be built on top of these endpoints.
The backend API is connected; the visual panel is not yet implemented.
