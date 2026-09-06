# Meeting calendar

The `Calendar` tab of the Meeting desk (`#/meeting`) reads an ICS export, surfaces the conference link of every upcoming event, and warns you locally when one is about to start. There is no OAuth connection and no calendar account is linked: Navin reads a file you provide.

## Importing an ICS file

| Source | Where to export from |
| --- | --- |
| Google Calendar | Settings, Import and export, Export, then unzip the `.ics` |
| Outlook / Microsoft 365 | Calendar, Share, Save to file, `.ics` |
| Apple Calendar | File, Export, Export as `.ics` |

Click **Import .ics** and pick the file. Events are stored locally and the badge on the `Calendar` tab shows how many are coming in the next 72 hours. Re-importing replaces the previous set, which is how you refresh the week.

## Join links

Navin looks for a conference link in the event, in this order: the `URL` property, the `X-GOOGLE-CONFERENCE` property, then the first known conference link found in the location or the description. Recognised hosts include Zoom, Google Meet, Microsoft Teams, Webex, Whereby, Jitsi, GoToMeeting, BlueJeans, Livestorm, and RingCentral.

Every event with a link gets a **Join** button. In the packaged desktop app the link is opened by the gateway in your system browser, because the embedded WebView blocks new tabs. If neither path works, the URL is shown so you can copy it.

## Linking an event to a meeting

**Link to meeting** copies the event into the active meeting: the title if it is still empty, the location and description as notes, and the event identifier so the summary can reference it. This is what makes an export self-describing three months later.

## Start alerts and auto-join

Every 30 seconds the desk looks for events starting within the next 5 minutes and raises one browser notification per event, once. Clicking the notification opens the conference link.

The **Open the conference link automatically at start time** toggle joins without a click. It is off by default and should stay off unless you keep Navin in the foreground: browsers and packaged WebViews block programmatic tab opening when the app is not focused, so an unattended auto-join can silently do nothing.

Navin never joins a meeting on your behalf as a bot, never dials into a room, and never records a call you are not in.

## Related docs

- [Overview](./README.md)
- [Privacy and audit trail](./privacy.md)
- [Troubleshooting](./troubleshooting.md)
