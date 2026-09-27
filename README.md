# Rejseplanen Live for Home Assistant

Danish public transport in Home Assistant, with the details that matter when you are about to leave home: realtime delays, cancellations, track changes, traffic messages, A→B connections with changes, where your train is right now, and events you can build automations on. Covers everything in Rejseplanen: DSB, S-tog, Metro, Letbane, regional trains, buses and ferries.

It uses [Rejseplanen's API 2.0](https://labs.rejseplanen.dk/) and its own domain (`rejseplanen_live`), so it can run next to Home Assistant's built-in Rejseplanen integration.

## Compared to the built-in integration

| | Built-in Rejseplanen (2026.10) | Rejseplanen Live |
|---|---|---|
| Next departure, delay, track | ✅ | ✅ |
| Cancelled departures | Hidden | Shown, counted, and fire an event |
| Track changes | – | Scheduled vs. new track, event |
| Traffic messages (disruptions, works) | – | Per stop and per trip, with a ticker on the card |
| A→B trips with changes | – | Departure, arrival, travel time, changes, "leave by" |
| Where is the train now | – | Last passed stop + map tracker |
| Events for automations | – | departed, arrived, delayed, cancelled, track changed, disruption, connection at risk |
| Arrival boards | – | ✅ |
| Facilities (bikes, wifi, low floor) | – | ✅ |
| Polling | Fixed 5 min | Spread over the month to fit your quota; faster in your active hours |
| API usage sensors | – | Calls this month/today, left, projected, current interval |
| Departure board card | – | Built in, DSB style |
| Actions with responses | – | Search stops, any board, plan trip, journey details, messages |

## Requirements

- A Rejseplanen API 2.0 access key. It is free for non-commercial use (50,000 calls a month). Request one with the contact form at [labs.rejseplanen.dk](https://labs.rejseplanen.dk/hc/en-us/articles/21553113674909-Adgang-til-data-fra-Labs); approval is manual and can take a few days.
- Home Assistant 2025.10 or newer.

## Installation

**HACS:** HACS → ⋮ → Custom repositories → add `https://github.com/TheRealBatBro/Home-Assistant-Rejseplanen` as an *Integration*, install **Rejseplanen Live**, restart Home Assistant.

**Manual:** copy `custom_components/rejseplanen_live` into `config/custom_components/` and restart.

Then **Settings → Devices & services → Add integration → Rejseplanen Live** and paste the key. On the integration's page, add what you want to follow:

- **Add station board**: search for a stop (or leave the search empty to list stops near your home). Choose departures or arrivals, transport types, lines (`A, Re, 350S`), a direction, walking time to the stop, when a train counts as delayed, and whether to track the next vehicle live.
- **Add trip**: from and to. You get the next connection with changes, travel time and delays, plus the following ones.

Both have **Active hours**: set e.g. 06:30–08:30 on weekdays and the integration only polls then. The calls it saves go to faster updates in that window.

## Entities

A **station board** device has:

| Entity | |
|---|---|
| Next departure | Timestamp (realtime). Attributes: the whole board with line, direction, scheduled/expected time, delay, track, track change, cancelled, facilities, messages |
| Following departure | Timestamp of the one after |
| Minutes until departure | Counts down every 30 s |
| Line, Direction, Delay, Track | Of the next departure |
| Status | on time / delayed / cancelled / connection at risk / disrupted / no departures |
| Delayed departures, Cancelled departures | Counts, with the rows as attributes |
| Traffic messages | Count, with headline and texts |
| Problem | Binary sensor: on while something is delayed, cancelled or disrupted |
| Traffic event | Event entity (see below) |
| Vehicle position + Vehicle tracker | Only with live tracking on: last passed stop, and the train on the map |

A **trip** device has Departure, Arrival, Leave by, Minutes until departure, Travel time, Changes, Departure delay, Arrival delay, Track, Line, Status, Traffic messages, Problem, Traffic event and, with tracking on, Vehicle position/tracker. The Departure sensor's `trips` attribute holds all connections with every leg.

The **Rejseplanen** device shows API calls this month/today, calls left, projected calls this month and the current update interval.

## Events and automations

Every board and trip has an event entity, and every event is also fired on the bus as `rejseplanen_live_event`. Event types: `departed`, `arrived`, `delayed`, `cancelled`, `track_changed`, `disruption`, `connection_at_risk`.

```yaml
# Tell me when my train gets cancelled or delayed while I'm at home in the morning
triggers:
  - trigger: state
    entity_id: event.roskilde_st_kobenhavn_h_traffic_event
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type in ['cancelled', 'delayed', 'connection_at_risk'] }}"
  - condition: time
    after: "06:00:00"
    before: "09:00:00"
actions:
  - action: notify.mobile_app_phone
    data:
      title: "Toget: {{ trigger.to_state.attributes.event_type }}"
      message: >
        {{ trigger.to_state.attributes.line }} kl. {{ as_timestamp(trigger.to_state.attributes.departure) | timestamp_custom('%H:%M') }},
        forsinkelse {{ trigger.to_state.attributes.delay }} min, spor {{ trigger.to_state.attributes.track }}
```

```yaml
# Flash a light when it is time to leave
triggers:
  - trigger: time
    at: sensor.roskilde_st_kobenhavn_h_leave_by
actions:
  - action: light.turn_on
    target: { entity_id: light.hallway }
    data: { flash: short }
```

## The card

The integration serves a departure board card automatically; there is no resource to add. In a dashboard: **Add card → Rejseplanen Live**, or:

```yaml
type: custom:rejseplanen-live-card
entity: sensor.roskilde_st_next_departure   # a board's "Next departure" or a trip's "Departure"
rows: 6
show_messages: true    # scrolling traffic messages
show_notes: false      # facilities per departure
theme: board           # "board" (dark DSB look) or "card" (your HA theme)
title: Roskilde        # optional
```

It shows scheduled time, countdown, line badge (S-tog and Metro in their line colours), destination, track, new times, new tracks and cancellations. For trips it shows each connection's legs, arrival, travel time and changes.

## Actions

All of these return data (use them in scripts with `response_variable`):

| Action | Does |
|---|---|
| `rejseplanen_live.search_stops` | Find stops by name, or near a position (default: home) |
| `rejseplanen_live.get_departures` | Departure or arrival board for any stop (id or name) |
| `rejseplanen_live.plan_trip` | Connections between two stops, optionally "arrive by" |
| `rejseplanen_live.get_journey` | Every stop of a train/bus with realtime times and its position (`journey_ref` from a departure) |
| `rejseplanen_live.get_messages` | Traffic messages for a stop |
| `rejseplanen_live.refresh` | Update all boards and trips now |

## How polling fits in the quota

The key's calls are shared by everything you add. The integration counts every call and spreads what is left of the month over the minutes your boards and trips are active. With the free 50,000 calls, one board polling all day gets an update every minute. Ten live-tracked boards (2 calls each) running all day get one every ~19 minutes; limit five of them to 07–09 on weekdays and those get one every minute. A board costs 1 call per update (+0.2 for traffic messages, +1 with live tracking); a trip costs 1 (+1 with tracking). 5% of the quota is kept for actions. If the quota still runs out, polling drops to once an hour until the month rolls over. Limits can be changed under the integration's **Configure**.

## Notes

- Data: © Rejseplanen, used under the [Rejseplanen Labs guidelines](https://labs.rejseplanen.dk/).
- Tested against Rejseplanen's published XML schema and real responses. Live train positions depend on what the operator reports; without one, the tracker uses the last stop the train passed.
- Texts from Rejseplanen (messages, facilities) come in Danish by default; switch to English under **Configure**.
