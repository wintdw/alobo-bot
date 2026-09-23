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
A branch also keeps some slots locked for its own courts
(`get_lock_yards` below), which occupy a court just as firmly while holding it for
nobody — both come off the window.

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

## The slots the venue keeps locked ("Khóa")

A booking is not the only thing that makes a court unsellable. A branch can also
keep its own courts off sale for a stretch — the app's booking grid paints those
**grey** and refuses to book them (the legend's "Khóa") — and it does so far more
often than its bookings suggest: 125 Hoàng Ngân states hours of 05:00-**24:00**,
yet locks **22:00-24:00 every day** for all eight courts, so an 18:00-23:59
search has nothing to sell after 22:00. Reading only `get_onetime_bookings` calls
those hours free: the complaint that started this was a court offered as
`partial 22:00-23:59` when the app will not book a minute of it.

```
GET {global_url}/v2/user/branch/get_lock_yards/{branchId}
```

**Public — no account, no token**, and a branch-level list rather than a
per-date one, so one call covers every day:

```json
[
  {
    "id": "98k2uiSVy9ZKu2D3m624",
    "servicesId": ["pickleball_1", "pickleball_2", "...", "pickleball_8"],
    "startTime": "2025-12-04T22:00:00.000",
    "endTime": "2025-12-04T23:59:59.000",
    "frequency": [1, 2, 3, 4, 5, 6, 7],
    "skipDates": [],
    "note": ""
  }
]
```

`frequency` decides which of two things an entry is, and the **dates** on
`startTime`/`endTime` are never a range:

| `frequency` | Meaning |
|---|---|
| a weekday list (`1`=Mon … `7`=Sun) | a **recurring** window: `startTime`..`endTime` read as a *time of day*, blocked on every day whose weekday is listed, except `skipDates` |
| empty | a **one-off**: the exact `startTime`..`endTime` stretch, on the date it names and no other |

Both readings were confirmed against `main.dart.js`, which carries only those two
branches of logic: the lock model matches on `frequency.contains(day.weekday)` for
a listed frequency and on `startTime`'s calendar date for an empty one, and the
grid painter then draws the lock's *clock* hours (`startTime.hour` .. `endTime.hour`,
clamped to the grid) down that day's column. The dates riding along are just the
day the lock was filed — hence the 22:00-24:00 entry above, filed 2025-12-04,
still greying today's grid, and hence Hoàng Ngân's own bookings never starting
before 05:30 (a 05:00-05:30 lock has been filed daily since 2025 too).

`servicesId` names the courts the entry takes off. Whole-day closures are filed
as one-offs (05:00-23:59:59 on a single date), which is why a lock's end is
rounded **up to the next minute**: `23:59:59` means "through midnight", and
keeping the leftover second would leave a bookable minute at the end of a window
asked to 24:00.

The bot subtracts these from the window exactly as it subtracts a booking
(`models.LockYard`, `availability._locked_spans`), so a court free only in a
locked stretch reads `partial` up to the lock rather than `available`, and one
locked for the whole window is dropped like a booked one. A lock list that cannot
be read marks the branch's courts `unknown` — the same "no answer" a failed
booking lookup gives, never "free".

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
- **The branch list keeps venues the app will not sell.** `/v2/user/branch/
  branches` and `branches_first` return every venue, and `get_cores` /
  `get_core_types` / `get_onetime_bookings` answer normally for the unbookable
  ones too — so such a venue prices up and looks free. `status` is the field
  that separates them, and the app's own search settles the rule exactly:
  `get_filtered_branch_booking` returned 2058 branches, *all* of them
  `status == 1` (2058 of the 2059), and none of the 352 `status == 0` ones nor
  any `-1`/`-2`/`2`. Confirmed against the live site: a `status == 0` venue
  ("CLB  PICKLEBALL", `sport_clb_tennis_pickleball_cau_giay`, address the
  placeholder "Ha Noi") and a `status == -1` one cannot be booked from their
  `/san/{id}` pages, while a `status == 1` venue books normally. `-1`/`-2` are
  locked/removed (the name often says so: "(khóa)", "(đã khóa tạo cn mới)"); `0`
  is a draft or paused listing that can still hold bookings the venue took itself
  (several `status == 0` venues show recent bookings) yet cannot be booked by a
  customer. The bot keeps only `status == 1` (`models.Branch.is_bookable`), a
  missing `status` counting as bookable.
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

To re-check the locks by hand, compare the bot's answer for a venue against its
grid on `datlich.alobo.vn/san/{branchId}` (the grid's grey "Khóa" cells must be
outside every `free span` the bot reports), and against the raw payloads:

```bash
# the locks a branch filed, and the bookings that must never cross them
curl -s "$GLOBAL/v2/user/branch/get_lock_yards/sport_125_hoang_ngan" -H "$HEADERS"
```
