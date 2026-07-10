import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 20,
  duration: '30s',
  thresholds: {
    http_req_failed: ['rate<0.20'],
  },
};

const API = __ENV.API_BASE || 'http://localhost:8000';

export default function () {
  const payload = JSON.stringify({
    type: "notification",
    event: "payment.succeeded",
    object: {
      id: `test_${__VU}_${__ITER}`,
      status: "succeeded",
      amount: { value: "1.00", currency: "RUB" },
      metadata: { order_id: "0" }
    }
  });
  let res = http.post(`${API}/api/payments/webhook/yookassa`, payload, { headers: { 'Content-Type': 'application/json' } });
  check(res, { 'webhook endpoint responds': (r) => [200, 400, 404, 422].includes(r.status) });
  sleep(0.2);
}
