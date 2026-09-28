# Mercado Libre ERP BFF

Odoo 19 addon depending on `meli_accounts` and the existing `meli_oerp` connector.
The client uses an **Odoo API Key**, while Odoo manages the shop's Mercado Libre
OAuth credentials. The fixed upstream is `https://api.mercadolibre.com`.

## Installation and authorization

Put this addon and the updated `meli_accounts` on the server's addons path.
Install `meli_bff` and upgrade `meli_accounts` during an agreed maintenance window.
The `/api/meli` routes are unavailable until the module is installed.

Assign **Mercado Libre BFF Reader** or **Mercado Libre BFF Writer** to an internal
user. Existing Mercado Libre managers inherit Writer. Writer authorizes platform
writes, but does not grant ORM permission to change store credentials. Company
record rules still apply. API calls can access all companies authorized for the user, without taking
company or user identity from request headers, query parameters, or cookies.

Create an API Key in the user's Odoo account security settings and send it as
`Authorization: Bearer <ODOO_API_KEY>`. Session cookies alone are rejected.
Inactive users and revoked keys are rejected. Keys and shop credentials are never
included in shop status responses.

Connect a shop through the existing browser-session OAuth flow at
`/meli/stores/{shopId}/authorize`; callback remains `/meli_login`. Retain the
registered redirect URI, state and PKCE configuration. BFF Reader access alone
does not authorize the browser OAuth management flow; a shop manager must connect it.

Set system parameter `meli_bff.timeout_seconds` for the upstream read timeout
(default 120 seconds; connect timeout is at most 15 seconds). Invalid settings
fall back to the default. A reverse proxy must allow the required upload size and
timeout. The upstream address cannot be configured through a request.

## Routes

Prefix every route below with `/api/meli`.

| Method and path | Upstream / result |
| --- | --- |
| GET `/shops` | `{ "shops": [...] }`, visible shops only |
| GET `/shops/{shopId}` | Shop status, expiry and browser authorization URL |
| GET `/shops/{shopId}/items` | `/marketplace/users/{sellerId}/items/search` |
| GET `/shops/{shopId}/items/{itemId}` | `/items/{itemId}`, seller checked |
| POST `/shops/{shopId}/items` | `/global/items`, traditional Global Selling |
| PUT `/shops/{shopId}/items/{itemId}` | `/global/items/{itemId}` |
| GET `/shops/{shopId}/items/{itemId}/description` | `/marketplace/items/{itemId}/description` |
| PUT `/shops/{shopId}/items/{itemId}/description` | `/global/items/{itemId}` |
| POST `/shops/{shopId}/pictures` | `/pictures/items/upload` |

Item listing defaults to the bound global seller. `scope=marketplace` selects only
the shop's bound marketplace seller; a missing binding is an error. Supported
listing filters are defined explicitly in `protocol.py`; repeated values are
preserved, including their interleaved order and original percent encoding after
removing BFF-only selectors. Percent-encoded paths are rejected. Identity and
arbitrary upstream parameters are rejected.

Creation/update accepts native platform JSON. The payload must match the shop's
bound site and logistics; creation needs `sites_to_sell`. User Products creation
returns 409. Description routes accept CBT item IDs. Description update accepts
only `{ "plain_text": "..." }` and constructs the site's and logistics' bound
`description.plain_text` payload. All other accepted request bodies retain bytes.

Upload one multipart `file` part with a file no larger than 10,000,000 bytes.
Additional form fields are rejected. Total request limit is 200 MiB.

```bash
curl -H "Authorization: Bearer $ODOO_API_KEY" \
  'https://erp.yingshi.dev/api/meli/shops'
curl -H "Authorization: Bearer $ODOO_API_KEY" \
  'https://erp.yingshi.dev/api/meli/shops/3/items?status=active'
curl -H "Authorization: Bearer $ODOO_API_KEY" \
  'https://erp.yingshi.dev/api/meli/shops/3/items?scope=marketplace'
```

BFF errors use `{ "success": false, "error": "..." }`. Authentication is 401,
permission/identity mismatch 403, unsupported routes 404, unsupported methods 405,
authorization state/unsupported account mode 409, upload limit 413, content type
415, and network/upstream read failure 502. Platform business statuses and bodies
pass through; hop-by-hop, Connection-nominated and Set-Cookie headers are removed.
Redirects and write requests are never automatically retried. Responses are spooled
to temporary storage before returning, so a truncated upstream response becomes a
502 before client response headers are sent. Large responses require temp disk.

OAuth rotation is persisted in an independent transaction with a two-second lock
wait. Only a database conflict before sending a refresh request can retry. Refresh
does not commit caller business changes. Save new shops before calling `_token()`;
do not hold the same store row lock while acquiring credentials.

## Verification

From the Odoo checkout, with PostgreSQL available to the current Unix user:

```bash
.venv/bin/python deploy/mercado/tests/run_bff.py
.venv/bin/python deploy/mercado/tests/run_bff.py --unit-only
```

The runner creates a unique `meli_bff_test_*` database, uses `/dev/null` configuration,
disables cron, and retains the database and printed temporary artifact directory.
It never loads production configuration or production tokens. To upgrade and rerun
the same disposable database, use `--reuse <printed-artifact-directory>`.
Tests simulate Mercado Libre; no real product writes have been exercised.

Mappings rechecked using mercadolibre-mcp-server (CBT/en_us):
[create/update](https://global-selling.mercadolibre.com/devsite/global-selling-item-create-update-global-items),
[description](https://global-selling.mercadolibre.com/devsite/item-description).
No `/api/proxy/platform`, Amazon, shipping batch or generic proxy endpoints exist.
