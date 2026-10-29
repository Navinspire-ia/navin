# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Native app connectors: catalog, credentials, OAuth and authenticated API calls.

The catalog data (apps, auth methods and fields, OAuth endpoints and scopes,
action names and parameters) and the per-app request rules (base URL, where
the credential goes, how an account is verified) are ported from
OpenConnector (modified Apache-2.0, Copyright 2026 OOMOL, see NOTICE.md).
Navin runs none of OpenConnector: this package is plain Python on httpx.

Layers, each with one owner:

- :mod:`navin.connectors.catalog` - read-only catalog, search and pagination.
- :mod:`navin.connectors.providers` - one request rule per app.
- :mod:`navin.connectors.store` - connections index and the secret vault.
- :mod:`navin.connectors.runtime` - verification and authenticated calls.
- :mod:`navin.connectors.oauth` - authorization URL, code exchange, refresh.
- :mod:`navin.connectors.manager` - what Settings and the agent call.
"""
