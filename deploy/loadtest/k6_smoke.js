import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 5,
  duration: '1m',
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1000'],
  },
};

const API = __ENV.API_BASE || 'http://localhost:8000';

export default function () {
  let health = http.get(`${API}/health`);
  check(health, { 'health 200': (r) => r.status === 200 });

  let products = http.get(`${API}/api/products`);
  check(products, { 'products 200': (r) => r.status === 200 });

  let looks = http.get(`${API}/api/looks`);
  check(looks, { 'looks 200': (r) => r.status === 200 });

  sleep(1);
}
