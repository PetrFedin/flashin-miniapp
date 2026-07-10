import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  scenarios: {
    catalog: { executor: 'constant-vus', vus: 10, duration: '2m' },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1200'],
  },
};

const API = __ENV.API_BASE || 'http://localhost:8000';

export default function () {
  let products = http.get(`${API}/api/products`);
  check(products, { 'products ok': (r) => r.status === 200 });

  let search = http.get(`${API}/api/search/products?q=dress`);
  check(search, { 'search ok or validation': (r) => [200, 422].includes(r.status) });

  let looks = http.get(`${API}/api/looks`);
  check(looks, { 'looks ok': (r) => r.status === 200 });

  sleep(1);
}
