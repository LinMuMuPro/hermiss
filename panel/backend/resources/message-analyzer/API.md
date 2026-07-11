# Hermes Memory Panel — API Reference

Base URL: `http://127.0.0.1:8765`

All endpoints are available with **both** the `/api/` prefix and without it:

```
GET /health       ≡  GET /api/health
GET /stats        ≡  GET /api/stats
GET /memories     ≡  GET /api/memories
...etc.
```

The non-prefixed form is provided for frontend compatibility. Both forms return identical responses.

---

## Common Patterns

### Error Response

All endpoints return this on failure:

```json
{
  "detail": "Human-readable error message"
}
```

HTTP status codes: `404` (not found), `422` (validation error), `500` (server error).

### Success Envelope

List endpoints wrap results in an object. Mutation endpoints return `{"ok": true}`.

---

## Health Check

```
GET /health        (or GET /api/health)
```

**Success** (200):

```json
{
  "status": "ok",
  "db": "/path/to/hermes_memory.db"
}
```

| Field    | Type   | Description              |
|----------|--------|--------------------------|
| status   | string | `"ok"` or `"error"`      |
| db       | string | Absolute path to SQLite  |

---

## Statistics

```
GET /api/stats
```

**Success** (200):

```json
{
  "total_memories": 18,
  "facts": 8,
  "preferences": 10,
  "sessions": 5
}
```

| Field           | Type    | Description              |
|-----------------|---------|--------------------------|
| total_memories  | integer | Total rows in `memories` |
| facts           | integer | `category = 'FACT'`      |
| preferences     | integer | `category = 'PREFERENCE'`|
| sessions        | integer | Session summaries        |
| by_category     | object  | `{fact:N, preference:N, ...}` 每类记忆数量 |
| by_importance   | object  | `{high:N, medium:N, low:N}` 重要度分布 |
| by_emotion      | object  | `{positive:N, neutral:N, ...}` 情绪分布 |
| memory_by_month | array   | `[{month:"2026-05", count:N}]` 按月记忆数 |
| session_activity| array   | `[{day:"...", count:N, messages:N}]` 会话活跃度 |

---

## Memories — List

```
GET /api/memories?search=&category=&importance=&limit=200&offset=0
```

**Query Parameters:**

| Param      | Type    | Default | Description                                    |
|------------|---------|---------|------------------------------------------------|
| search     | string  | `""`    | Substring match on `entry`                     |
| category   | string  | `""`    | Filter: `FACT`, `PREFERENCE`, `HEALTH`, `EMOTIONAL` |
| importance | string  | `""`    | Filter: `HIGH`, `MEDIUM`, `LOW`                |
| limit      | integer | `200`   | Max rows (1–1000)                              |
| offset     | integer | `0`     | Pagination skip                                |

**Success** (200):

```json
{
  "total": 18,
  "limit": 200,
  "offset": 0,
  "memories": [
    {
      "id": 12,
      "category": "PREFERENCE",
      "importance": "MEDIUM",
      "entry": "喜欢喝黑咖啡不加糖",
      "emotion": "neutral",
      "source_msg": "我还是更喜欢黑咖啡不加糖",
      "created_at": "2026-05-20T18:23:45"
    }
  ]
}
```

| Field       | Type           | Description                              |
|-------------|----------------|------------------------------------------|
| total       | integer        | Total matching rows (before pagination)  |
| limit       | integer        | Requested limit                          |
| offset      | integer        | Requested offset                         |
| memories[]  | array[object]  | See Memory Object below                  |

**Memory Object:**

| Field      | Type           | Description                              |
|------------|----------------|------------------------------------------|
| id         | integer        | Primary key                              |
| category   | string         | `FACT` / `PREFERENCE` / `HEALTH` / `EMOTIONAL` |
| importance | string         | `HIGH` / `MEDIUM` / `LOW`                |
| entry      | string         | The memory content                       |
| emotion    | string or null | Detected emotion, e.g. `positive`, `neutral` |
| source_msg | string or null | Original user message that triggered it  |
| created_at | string         | ISO 8601 timestamp                       |

---

## Memories — Get One

```
GET /api/memories/{id}
```

**Success** (200): Returns all columns for the row.

```json
{
  "id": 12,
  "category": "PREFERENCE",
  "importance": "MEDIUM",
  "entry": "喜欢喝黑咖啡不加糖",
  "emotion": "neutral",
  "source_msg": "我还是更喜欢黑咖啡不加糖",
  "created_at": "2026-05-20T18:23:45",
  "updated_at": null,
  "access_count": 0,
  "last_accessed": null
}
```

| Field        | Type           | Description                            |
|--------------|----------------|----------------------------------------|
| id           | integer        | Primary key                            |
| category     | string         | `FACT` / `PREFERENCE` / `HEALTH` / `EMOTIONAL` |
| importance   | string         | `HIGH` / `MEDIUM` / `LOW`              |
| entry        | string         | Memory content                         |
| emotion      | string or null | Detected emotion                       |
| source_msg   | string or null | Original user message                  |
| created_at   | string         | ISO 8601                               |
| updated_at   | string or null | ISO 8601, set on edit                  |
| access_count | integer        | Times retrieved by the plugin          |
| last_accessed| string or null | ISO 8601                               |

**Errors:**

| Status | Body                                   | When          |
|--------|----------------------------------------|---------------|
| 404    | `{"detail": "记忆 #99 不存在"}`          | Bad id        |

---

## Memories — Update

```
PUT /api/memories/{id}
Content-Type: application/json
```

**Request Body** (all fields optional — only send what you want to change):

```json
{
  "entry": "修改后的记忆内容",
  "category": "PREFERENCE",
  "importance": "HIGH"
}
```

| Field      | Type   | Required | Description                    |
|------------|--------|----------|--------------------------------|
| entry      | string | No       | New memory content             |
| category   | string | No       | New category                   |
| importance | string | No       | New importance                 |

**Success** (200):

```json
{
  "ok": true,
  "id": 12
}
```

| Field | Type     | Description           |
|-------|----------|-----------------------|
| ok    | boolean  | Always `true`         |
| id    | integer  | Updated memory id     |

**Errors:**

| Status | Body                                   | When          |
|--------|----------------------------------------|---------------|
| 404    | `{"detail": "记忆 #99 不存在"}`          | Bad id        |
| 422    | `{"detail": [{"loc":["body","entry"],"msg":"..."}]}` | Invalid type |

---

## Memories — Delete

```
DELETE /api/memories/{id}
```

**Success** (200):

```json
{
  "ok": true,
  "id": 12
}
```

| Field | Type     | Description           |
|-------|----------|-----------------------|
| ok    | boolean  | Always `true`         |
| id    | integer  | Deleted memory id     |

**Errors:**

| Status | Body                                   | When          |
|--------|----------------------------------------|---------------|
| 404    | `{"detail": "记忆 #99 不存在"}`          | Bad id        |

This also removes the corresponding row from the FTS5 index (`memories_fts`).

---

## Cron Jobs — List

```
GET /api/cron-jobs
```

Returns all cron jobs — active (from `jobs.json`) + completed (from `cron/output/`).

**Success** (200): `{"cron_jobs": [{"id", "name", "schedule", "state", "response_preview", ...}]}`

- `state`: `scheduled` / `completed` / `failed`
- `response_preview`: first 100 chars of the completion output for finished jobs

---

## Sessions — List

```
GET /api/sessions?limit=50
```

**Query Parameters:**

| Param  | Type    | Default | Description       |
|--------|---------|---------|-------------------|
| limit  | integer | `50`    | Max rows (1–500)  |

**Success** (200):

```json
{
  "sessions": [
    {
      "id": 5,
      "session_id": "abc123-def456",
      "summary": "用户讨论了咖啡偏好和项目进展",
      "message_count": 12,
      "last_emotion": "positive",
      "ended_at": "2026-05-20T18:30:00"
    }
  ]
}
```

| Field          | Type           | Description                            |
|----------------|----------------|----------------------------------------|
| id             | integer        | Primary key                            |
| session_id     | string         | Hermes session identifier              |
| summary        | string         | LLM-generated summary of the session   |
| message_count  | integer        | Number of messages in this session     |
| last_emotion   | string or null | Dominant emotion at session end        |
| ended_at       | string         | ISO 8601 when session ended            |

---

## Export

```
GET /api/export
```

Exports all memories as JSON.

**Success** (200):

```json
{
  "exported_at": "2026-05-21T12:00:00.123456",
  "memories": [
    {
      "id": 12,
      "category": "PREFERENCE",
      "importance": "MEDIUM",
      "entry": "喜欢喝黑咖啡不加糖",
      "emotion": "neutral",
      "created_at": "2026-05-20T18:23:45"
    }
  ]
}
```

| Field        | Type           | Description                          |
|--------------|----------------|--------------------------------------|
| exported_at  | string         | ISO 8601 timestamp of export         |
| memories[]   | array[object]  | All memories (id, category, importance, entry, emotion, created_at) |

Note: Only the `memories` table is exported. Sessions and tasks are not included.

---

## Summary

| Method | Path                     | Auth | Description              |
|--------|--------------------------|------|--------------------------|
| GET    | `/api/health`            | No   | Database status          |
| GET    | `/api/stats`             | No   | Row counts               |
| GET    | `/api/memories`          | No   | List with search/filter  |
| GET    | `/api/memories/{id}`     | No   | Full row with all columns|
| PUT    | `/api/memories/{id}`     | No   | Edit entry/category/importance |
| DELETE | `/api/memories/{id}`     | No   | Delete + FTS cleanup     |
| GET    | `/api/sessions`          | No   | Session history          |
| GET    | `/api/export`            | No   | Full memory dump         |
