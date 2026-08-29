# NAV_SOURCE.md — verified facts about the official NAV job-vacancy source

**Verified 2026-08-29 against live endpoints and official documentation.**
Everything below was confirmed by an actual request from this environment, or
quoted from official NAV documentation. Nothing here is guessed; where a
behavior was inferred from observation rather than documentation, it says so.

## 1. Official source

| Item | Value |
|---|---|
| Service | **NAV Job Vacancy Feed** (`pam-stilling-feed`), the feed behind arbeidsplassen.nav.no |
| Docs | https://navikt.github.io/pam-stilling-feed/ · repo https://github.com/navikt/pam-stilling-feed |
| Base URL | `https://pam-stilling-feed.nav.no` |
| Feed page | `GET /api/v1/feed` → next pages at `GET /api/v1/feed/{next_id}` |
| Ad detail | `GET /api/v1/feedentry/{uuid}` |
| Public token | `GET /api/publicToken` |
| Terms | https://arbeidsplassen.nav.no/vilkar-api |
| Predecessor | `pam-public-feed` (the older "Ledige stillinger publisert av NAV" dataset was retired in 2024; this feed replaces it) |

## 2. Authentication (verified)

- Header: `Authorization: Bearer <JWT>`.
- The public token endpoint returns **prose, not a bare token**:
  `Current public token for Nav Job Vacancy Feed:\n<JWT>` — the JWT must be
  extracted (this adapter does so with a regex). Sending the whole response
  as the header yields an opaque HTML `400 Bad request`.
- Observed public-token JWT claims: `iss=nav-no`, `aud=feed-api-v2`, HS256,
  ~35-day validity window. Docs state the public token "will rotate at
  irregular intervals" and is intended for experimentation.
- A stable private token is issued on request to
  `nav.team.arbeidsplassen@nav.no` with company identifier and contact
  details, after accepting the terms. **Recommended before any sustained or
  production ingestion**; the public token is adequate for this validation
  snapshot.

## 3. Transport quirk (verified in this environment)

Requests negotiated over **HTTP/2 through the agent proxy fail** with
`curl (43) Failed sending HTTP request`. Forcing **HTTP/1.1 works**. The
adapter therefore pins HTTP/1.1. This is an environment/transport
observation, not a NAV limitation.

## 4. Feed semantics (verified)

- A feed page returns `{version, title, home_page_url, feed_url, description,
  id, next_id, next_url, items[]}`; observed **1000 items per page** when
  reading history, fewer at the head of the feed.
- Traverse forward via `next_url` / `next_id`; **end of feed is `next_url` /
  `next_id` = `null`**.
- `If-Modified-Since` (RFC-1123, e.g. `Mon, 24 Aug 2026 00:00:00 GMT`)
  anchors the starting point in time. `ETag` / `Last-Modified` +
  `If-None-Match` return `304` when unchanged.
- **The feed is an event log, not a catalogue.** Each item is a state change
  at a point in time; the same ad appears again when it is updated. Verified
  consequence: an item listed as `ACTIVE` on 24 Aug returned `INACTIVE` with
  masked content when its detail was fetched on 29 Aug.

## 5. Historical and inactive ads (verified + documented)

- Documented: the feed "contains all ads and their state registered at NAV
  since ca. 2019"; ads that are filled or expired are marked **`INACTIVE`**.
- Documented + verified: when an ad is stopped, NAV **masks or removes
  fields** — title, employer, business and contact information. A detail
  fetch for an inactive ad returns only `{uuid, status, sistEndret}` with
  **no `ad_content`**.
- Observed on a history page anchored 24 Aug: **874/1000 ACTIVE, 126
  INACTIVE**. On an 18-hour-fresh anchor: 62/72 ACTIVE.
- Consequence for research: **ad content must be captured close to
  publication.** Content for ads that have since gone inactive is not
  retrievable retroactively. Ads are valid at most ~6 months.

## 6. Fields available on an active ad (verified from a live record)

`ad_content` keys observed in full:

```
uuid, title, jobtitle, description (HTML), published, expires, updated,
applicationDue, applicationUrl, link, sourceurl, source,
employer{name, orgnr, description, homepage},
workLocations[{country, address, city, postalCode, county, municipal}],
occupationCategories[{level1, level2}],
categoryList[{categoryType, code, name, description, score}],
contactList[{name, email, phone, role, title}],
engagementtype, extent, starttime, positioncount, sector
```

Wrapper: `{uuid, status, sistEndret, ad_content}`.
Feed item (listing level): `{id, url, title, content_text, date_modified,
_feed_entry{uuid, status, title, businessName, municipal, sistEndret}}`.

Observed values: `extent` ∈ {Heltid, Deltid, …}, `engagementtype` ∈ {Fast,
Vikariat, Annet, …}, `sector` ∈ {Privat, Offentlig, …}; `categoryList`
carries **ESCO**, **JANZZ** and **STYRK08** codes with confidence `score`;
`description` is **HTML**, not plain text.

## 7. Known exclusions and limits

- Only ads NAV is authorized to share (directly registered, or received from
  third-party/ATS systems). Ads posted exclusively elsewhere are absent.
- Inactive ads are content-masked (see §5) — a hard limit on retrospective
  corpus building.
- `contactList` carries **personal data**; terms require deletion when no
  longer necessary for the original purpose. This adapter **drops
  `contactList` by default** (`--keep-contacts` opts back in) and the
  validation corpus is stored without it.
- No documented rate limits; this adapter is nonetheless polite (sequential,
  configurable delay).

## 8. Usage terms (quoted, https://arbeidsplassen.nav.no/vilkar-api)

- Permitted: *"Konsumenter av APIet har rett til å republisere og vise
  mottekne jobbannonsar på sine tenester, og/eller bruke dei til
  statistiske/analytiske formål."* → **statistical/analytical use, which is
  what this validation track is, is explicitly permitted.**
- Obligation: *"Alle annonsar … skal straks fjernast frå resultatlista til
  Konsumenten når annonsen blir inaktiv eller sletta hos Nav."* → if any of
  this is ever published in a product surface, inactive ads must be removed
  promptly. (Not applicable to an offline analysis snapshot, but binding for
  any future product use.)
- Obligation: republished ads must be updated when updated in the API, and
  application links must deep-link to the source system's application
  function.
- Personal data must be handled per Norwegian data-protection law and deleted
  when no longer necessary.
- Free of charge, subject to accepting the terms.
