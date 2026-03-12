# Site Structure & Crawling Patterns

> internal reference for API routing and data flow.

## 1. Architecture

```
Browser                    APISIX Gateway                   Microservices
 │                              │                                │
 │  SPA (uni-app + Vue.js)      │  prefix-based routing          │
 │  v4.54.3                     │                                │
 │                              │  cyy_buyerapi ────────> buyer service
 │  JS bundle -> API calls ---> │  cyy_gatewayapi/home ─> home service
 │                              │  cyy_gatewayapi/show ─> show service
 │  CDN-hosted assets           │  cyy_gatewayapi/trade > trade service
 │                              │  cyy_gatewayapi/user ── user service
 │  WAF protected               │  angry_dog_outapi ────> security svc
```

Platform: a third-party SaaS ticketing system.

## 2. Gateway Route Map

| JS Variable | Actual Prefix | Purpose |
|-------------|--------------|---------|
| `buyerApi` | `cyy_buyerapi` | core ticketing (categories, coupons) |
| `homeApi` | `cyy_gatewayapi/home` | homepage, search, shop config |
| `showApi` | `cyy_gatewayapi/show` | show detail, sessions, seat plans |
| `tradeApi` | `cyy_gatewayapi/trade` | orders, payments |
| `userApi` | `cyy_gatewayapi/user` | auth, profile |

## 3. Public APIs (no auth required)

### 3.1 Shop Config
```
GET /{homeApi}/pub/v5/shop/configs
```

### 3.2 Homepage Layout
```
GET /{homeApi}/pub/v5/layouts?page=HOME
```

### 3.3 Show Search (paginated)
```
GET /{homeApi}/pub/v3/show_list/search?length=20&pageIndex=0
params: length, pageIndex, showType, cityId
returns: searchData[] + isLastPage
```

### 3.4 Backend Categories
```
GET /{buyerApi}/pub/v1/shows/backend_categories?level=2
```

### 3.5 Show Static Data
```
GET /{showApi}/pub/v3/show_static_data/{showId}?locationCityId=...&siteId=
```

### 3.6 Show Dynamic Data
```
GET /{showApi}/pub/v3/show_dynamic_data/{showId}?locationCityId=...&siteId=
```

### 3.7 Sessions
```
GET /{showApi}/pub/v3/show/{showId}/sessions_static_data
```

### 3.8 Seat Plans / Ticket Tiers
```
GET /{showApi}/pub/v3/show/{showId}/show_session/{sessionId}/seat_plans_static_data
```

### 3.9 Service Notes
```
GET /{showApi}/pub/v3/show/{showId}/service_notes
```

### 3.10 Rich Content
```
GET {contentUrl}  (from show_static_data response)
```

## 4. Data Flow

```
show_list/search  ──> showId[]
       │
       ├──> show_static_data/{showId}     basic info + contentUrl
       ├──> show_dynamic_data/{showId}    sale status
       ├──> sessions_static_data          sessions -> sessionId[]
       │        │
       │        └──> seat_plans_static_data/{sessionId}   ticket tiers
       └──> service_notes/{showId}        policies
```

**Pagination**: `pageIndex` (0-based) + `length` (default 20), `isLastPage=true` to stop.

**ID Format**: MongoDB ObjectId (24-char hex).

## 5. Response Format

```json
{
  "statusCode": 200,
  "comments": "成功",
  "mode": 0,
  "result": {},
  "data": { ... }
}
```

## 6. Rate Limiting Notes

- WAF protected — keep request interval >= 0.5s
- `acw_tc` cookie set on first request, carry forward
- All `/pub/` paths are unauthenticated
