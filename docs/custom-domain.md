# Serving the API from a Custom Domain

Putting the API behind `api.redsparrowenterprise.in` instead of a generated
`execute-api` URL. Substitute your own domain throughout.

## How the pieces fit

```
Browser / client
   │  https://api.redsparrowenterprise.in/order
   ▼
Route 53  ── A/ALIAS record ──▶  API Gateway custom domain
                                 (terminates TLS with your ACM certificate)
                                        │
                                        ▼
                                 API mapping  ──▶  HTTP API, "prod" stage
                                        │
                                        ▼
                                 OrderService Lambda
```

There are three things to configure: attach a certificate to a **custom domain
name** in API Gateway, **map** that domain to your API and stage, and point
**DNS** at it.

There is no port to set. API Gateway custom domains serve HTTPS on 443 and
nothing else — there is no plaintext port 80 to redirect from, unlike an ALB or
CloudFront distribution.

## Two prerequisites that catch people out

**The certificate must be in the same region as your API.** For a *regional*
custom domain, ACM must have issued the certificate in the region your HTTP API
lives in. The well-known "certificates must be in `us-east-1`" rule applies to
CloudFront and edge-optimized endpoints, not this. If yours is in the wrong
region, request a new one in the right region — DNS validation takes a few
minutes when you already control the domain.

**Route 53 must be authoritative for the domain.** Holding the certificate is
not enough; the hosted zone has to be the one actually answering DNS queries.
If you registered the domain elsewhere, delegate it by pointing your
registrar's `NS` records at the Route 53 hosted zone first.

---

## Step 1 — Confirm the certificate

1. Open **ACM** in the **same region** as your HTTP API
2. Confirm a certificate covers `api.redsparrowenterprise.in` — either exactly,
   or as a wildcard `*.redsparrowenterprise.in` — and its status is **Issued**
3. If there is none, **Request a certificate** → public → enter the hostname →
   validation method **DNS**. On the certificate's detail page, click
   **Create records in Route 53** and ACM inserts the validation `CNAME`
   itself. Status moves to **Issued** within a few minutes

## Step 2 — Create the custom domain name

1. **API Gateway → Custom domain names → Create**
2. Domain name: `api.redsparrowenterprise.in`
3. Endpoint type: **Regional**
4. Minimum TLS version: **TLS 1.2**
5. ACM certificate: the one from Step 1
6. **Create domain name**
7. On the detail page, copy the **API Gateway domain name** — something like
   `d-abc123xyz.execute-api.REGION.amazonaws.com`. This is what DNS points at,
   not your API's invoke URL

## Step 3 — Map the domain to your API

1. On the custom domain's page, open the **API mappings** tab →
   **Configure API mappings** → **Add new mapping**
2. API: `OrderingHttpApi`
3. Stage: `prod`
4. Path: **leave blank**, so `https://api.redsparrowenterprise.in/order` maps
   straight onto the `/order` route with no stage prefix
5. **Save**

## Step 4 — Point Route 53 at it

1. **Route 53 → Hosted zones → `redsparrowenterprise.in`**
2. **Create record**
3. Record name: `api`
4. Record type: **A**
5. Turn **Alias** on
6. Route traffic to: **Alias to API Gateway API** → your region → select the
   custom domain, which auto-fills the target
7. **Create records**

An alias record points straight at the regional endpoint with no extra CNAME
hop, and AWS keeps the underlying addresses current. Resolution inside Route 53
is near-immediate; allow a few minutes if you are testing from a network that
caches DNS aggressively.

## Step 5 — Test

```bash
curl -X POST https://api.redsparrowenterprise.in/order \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: domain-test-001" \
  -d '{"customerId":"cust_1","items":[{"sku":"PIZZA","quantity":1}],"amount":9.99}'
```

![Order placed through the custom domain](../screenshots/02-api-test-custom-domain.png)

The response is identical to the one from the generated `execute-api` URL, and
the path carries no `/prod` prefix. A few seconds later the confirmation email
arrives — see
[outputs.md §3](outputs.md#3-end-to-end--placed-paid-confirmed) for the full
end-to-end trace.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| TLS/certificate error | The certificate is in a different region from the API, or does not cover this exact hostname |
| `403 Forbidden` from API Gateway | The API mapping is missing, or points at the wrong stage |
| `404 Not Found` | A path was set on the mapping, so the route is now `/<path>/order` |
| `NXDOMAIN` | The alias record was created in a hosted zone that is not the one answering for this domain |
| Works on the invoke URL, not the domain | DNS has not propagated yet, or the alias targets the invoke URL rather than the `d-...` regional domain name |

> On the [`iac-sam`](../../tree/iac-sam) branch, all four steps are three
> template parameters — the certificate ARN, the domain name, and the hosted
> zone ID — and CloudFormation creates the domain, the mapping and the DNS
> record for you.
