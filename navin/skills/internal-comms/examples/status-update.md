# Status update examples

Format: team and dates on the first line, then Progress, Plans, Problems, one
to three sentences each. Numbers over adjectives. Readable in under a minute.
All names, figures and products below are fictional.

## Squad level

```
Desk Platform squad - 3 to 7 March
Progress: Shipped the shared task board to all Studio desks (Career, Leads,
Tenders); 412 tasks created in the first four days, median load time down
from 1.9 s to 0.6 s. Closed 11 of the 14 bugs from the February triage.
Plans: Finish the dependency-aware "next" queue (design approved Tuesday),
migrate the two remaining desks, start the accessibility pass on the kanban
columns.
Problems: The SQLite lock contention seen on large boards (3 reports, boards
above 5,000 tasks) is not reproduced locally yet; one engineer is out until
the 12th, which pushes the migration of the Marketing desk by a week.
```

## Company level

```
Navinspire - February
Progress: Revenue grew 14 percent month over month; 3 new enterprise
accounts signed (2 in France, 1 in the UAE). Released Studio 2.4 with the
Montage editor and the shared board; 61 percent of weekly active workspaces
used at least one desk. Hired 4 people (2 engineering, 1 sales, 1 support).
Plans: Close the Series A data room by 20 March, launch the partner plugin
marketplace in beta, finish SOC 2 Type 1 evidence collection.
Problems: Support backlog reached 38 open tickets (target 20) after the 2.4
launch; we are adding one support hire and shipping the self-service FAQ.
The Riyadh tender response is blocked on a legal translation due the 10th.
```

## Project update variant

Add one line for the timeline when the update is about a single project:

```
Plugin marketplace beta - week 9
Timeline: beta opens 24 March (unchanged), public launch 28 April (at risk,
see Problems).
Progress: Publisher signing flow done and tested with 3 partner teams;
27 plugins validated by the structure checker.
Plans: Billing integration test, moderation queue UI, launch post draft.
Problems: The review SLA with the security team is 5 working days; with 40
plugins in the queue the public launch slips by two weeks unless we add a
second reviewer.
```

Notes for the writer:

- Ask for the team name and the period if they are not given.
- Keep the three headings even when a section is short ("Problems: none
  this week" is a valid, useful line).
- Put the figure first inside a sentence when it is the news ("412 tasks
  created" rather than "the board was well received").
