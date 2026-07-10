# Load testing

Install k6:

```bash
brew install k6
```

Run:

```bash
API_BASE=http://localhost:8000 k6 run deploy/loadtest/k6_smoke.js
```

Production run should be coordinated. Do not load-test production during active sales without approval.
