# Third-party notice - OpenConnector

`navin/connectors/data/catalog.json`, the icons in `webui/src/assets/connectors/`
and the request rules in `navin/connectors/providers.py` are derived from
OpenConnector (https://github.com/oomol-lab/open-connector).

Copyright 2026 OOMOL. Licensed under a modified version of the Apache License
2.0 (see `open-connector/LICENSE.txt` in the source checkout). Navin embeds this
work in a product whose primary value lies elsewhere, so that its own users
connect their accounts as part of using Navin, which the license allows without
a commercial agreement. Third-party brand names and logos remain the property of
their owners.

Changes made by Navinspire IA: the catalog is reduced to the apps Navin ships,
regrouped into Navin categories, and extended with a few documented auth
methods (YouTube API key, Sentry auth token, Slack bot token, HubSpot private
app token, HubSpot REST OAuth endpoints). The TypeScript runtime is not used;
the request rules are re-implemented in Python.

Regenerate the catalog with `python scripts/connectors/generate_catalog.py`.
