---
name: calendar
description: Check availability and manage personal calendar events through a connected Google or Microsoft account, using the accounts tool.
---

Use `accounts action=status` to select an account. `calendar.list` takes an ISO `start` and `end`, both with an explicit timezone. Check existing events before proposing availability.

`calendar.create` and `calendar.update` take `title`, `start`, `end`, optional `description`, and a stable `request_id`. Updates and `calendar.cancel` require the returned `event_id`. Creation currently records a personal calendar event, without sending attendee invitations. Use the email workflow for a reviewed invitation.

Preserve the user's timezone and distinguish a suggested slot from a confirmed meeting. Sensitive writes may return `approval_required`; the user reviews them in Connect accounts. Do not mark the calendar updated until the provider confirms acceptance, and do not retry an `unknown` write with a new request ID.
