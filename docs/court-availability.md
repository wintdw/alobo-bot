# Court availability — findings

How `alobo-bot` answers "is this court actually free?" without logging in.
Reverse-engineered from the live app with a headless browser (captured the
booking flow on `datlich.alobo.vn`), verified against the live origins.

## The endpoint

```
GET {global_url}/v2/user/branch/get_onetime_bookings
      ?branchId=<branchId>&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
```

**Public — no account, no token.** The app sends an `Authorization: Bearer`
header, but the endpoint answers the same payload without one, so the bot never
authenticates. It returns the branch's **existing** one-time bookings, i.e. the
slots that are *taken*:

```json
[
  {
    "id": "1EdtLXv2pxzZad5Omuks",
    "time": "2026-09-23T18:00:00.000",
    "duration": 120,
    "type": "groupOneTime",
    "totalPrice": 0,
    "status": 1,
    "services": [
      {"serviceId": "pickleball_1", "startTime": "2026-09-23T18:00:00.000",
       "duration": 120, "price": 0, "amount": 2, "branchServiceType": "core"},
      {"serviceId": "pickleball_2", "startTime": "2026-09-23T18:00:00.000",
       "duration": 120, "price": 0, "amount": 2, "branchServiceType": "core"}
    ]
  }
]
```

`services[].serviceId` is a court id — the same id as a core from `get_cores`
and the `setting` that links to `get_core_types`. A group booking holds several
courts at once, so one payload entry becomes one **leg per court**
(`models.Booking`).

A court is free for a window exactly when **no leg overlaps it**
(`availability.court_availability`); there is no separate "free slots" endpoint.

## The verdict is per window, not per court

The window is the one you asked for (`--from`/`--to`), so the same court can be
`free`, `partial` or `booked` depending on it. The bot subtracts the booking legs
from the window and reports what is left:

| Open part of the window | Verdict | Shown as |
|---|---|---|
| all of it | `free` | `available` |
| some of it | `partial` | `partial 20:00-21:00` (the open spans) |
| none of it | `booked` | _not listed at all_ |
| unknown (lookup failed) | `unknown` | `?` |

So a court booked **18:00-20:00** is `partial 20:00-21:00` for an **18:00-21:00**
search — not "unavailable" — and `--availability free` / the Free-only control
narrows the list to courts whose verdict is `free` (i.e. open for the whole
window). A `booked` court is dropped rather than listed, because it has nothing
left to sell and no price to quote.

The price follows the same rule: a court is quoted for the part of the window it
can actually sell. Asked for **18:00-24:00** at La Khê, whose only open hour is
**22:00-23:00**, you get `200.000đ` for that hour — not five hours of somebody
else's court. Disjoint open spans are summed, since the app's grid lets you pick
separate slots; `hours` in the JSON says how much time that total covers.

The window is first clipped to the branch's **working hours**
(`morningStartWorkingTime`..`afternoonEndWorkingTime`, which the branch list
already carries), because a court cannot be free, or priced, outside them. A
venue showing "Giờ hoạt động 05:00-22:00" renders a booking-grid column per hour
from opening *through* closing, so its last sold hour starts at the closing time
and runs one slot past it — an 18:00-24:00 request against La Khê is therefore
priced and checked for **18:00-23:00**. That is what fixes a span like
`partial 22:00-00:00`, which claimed an hour the app never offers.

Finally, an hour the venue publishes **no rate** for is not on sale either: some
branches' rate tables stop before their stated closing time (blocks to 22:00 with
a `0` base), and such an hour is trimmed off the window instead of being offered
at nothing. It is rarely a whole hour on its own — La Khê prices 22:00-23:00,
while SELA, whose table stops at 22:00, is offered for 20:00-22:00 only.

## Quirks worth knowing (all handled)

- **Dates are the venue's local day (UTC+7).** A day before the venue's today is
  rejected with `400`, so the caller must treat a failure as *unknown*, never as
  *booked*. The bot does exactly that (`availability.UNKNOWN`), which is why a
  run on a host whose `TZ` is not `Asia/Ho_Chi_Minh` degrades to `?` instead of
  crashing. Same trap as the signing clock — see
  [updating-api-keys.md](./updating-api-keys.md).
- **Date *ranges* are flaky.** Single days answer reliably for any date (today
  out to months ahead); a `startDate..endDate` span sometimes `400`s (observed:
  `2026-09-23..2026-10-07` fails, `2026-09-23..2026-09-30` and
  `2026-10-01..2026-10-23` succeed). The bot therefore asks **one day at a time**
  — plus the next day only when the window crosses midnight — rather than one
  range.
- **`status` was `1` on every live booking** and `type` was `groupOneTime`; a
  cancelled booking was never observed, so the bot treats every returned leg as
  occupying its court rather than second-guessing `status`.
- **The branch list keeps withdrawn venues.** A branch whose `status` is `-1`
  (locked — its name often says so, e.g. "789 Pickleball Club (đã khóa tạo cn
  mới)" or "(khóa)Stamina …") or `-2` (removed) is still returned by both
  `/v2/user/branch/branches` and `branches_first`, and its `get_cores` /
  `get_core_types` / `get_onetime_bookings` calls answer normally — so a locked
  venue prices up and looks free. The app drops exactly these two codes
  (`main.dart.js`: `![-2,-1].contains(branch.status)`) before it lists anything;
  the bot does the same (`models.Branch.is_locked`), because their courts and
  tickets are no longer for sale. `status` `0` and `1` are live and are kept.
- **Month-level schedules are separate.** The app also calls
  `get_schedule_bookings?branchId=&month=YYYY-MM`, which returned `[]` for every
  branch tried. It is not needed to answer "is this court free", so the bot
  ignores it.

## The court calendar that *is* gated

`get_booking/{branchId}/core/{coreId}` — the per-court calendar the app can
open — answers `403` anonymously and `401` to a bad token, and returned
`{"booking": {}, "ticket": {}}` even with a valid session. It is *not* the
endpoint the app uses for the booking grid, is not needed for availability, and
is deliberately not used.

## Verifying

```bash
# tags every priced court free/booked for the window
alobo-bot find --place "Hà Nội" --date 2026-09-23 --from 18:00 --to 21:00

# hides the ones already taken
alobo-bot find --place "Hà Nội" --date 2026-09-23 --from 18:00 --to 21:00 --availability free
```
