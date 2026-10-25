# Display profiles hold only their differences, and the server applies them

Status: accepted

Issues #47 and #48 give each kind of screen a **Display profile** (`GLOSSARY.md`),
chosen by `?display=<name>` in the screen's start URL. Settings become the
**Default profile**. Every other profile stores only its **Customised settings**,
and every setting it does not customise follows the Default profile live.
`GET /api/board?display=<name>` returns the board with that profile already
applied, so the screen still receives resolved answers and holds no settings logic.

## Considered options

- **A full copy of Settings per profile.** Rejected: changing the Default
  profile's rotation time would then miss every profile copied before the
  change, and nobody could tell which profile values were deliberate.
- **Profiles that follow other profiles.** Rejected: a chain makes "why does this
  screen look like this?" a search. Every profile follows the Default profile and
  nothing else.
- **Merging on the screen.** Rejected for the reason the board already gives:
  the display renders what it is told (CLAUDE.md, front-end behaviour).
- **Choosing the profile from the User-Agent alone.** Rejected as the primary
  signal: Android TV and Android tablet kiosks (Screenlite uses the stock
  WebView) send indistinguishable reduced User-Agents, and client hints need
  HTTPS. A profile may carry an optional User-Agent match as a fallback, but
  the URL always wins.

## Consequences

- Some settings are customised only together, as one unit (Theme and its
  colours, pagination and page size, each size preset and its scale, each stat's
  show and hide-when-empty pair), so a profile cannot hold half a Theme.
- A setting that changes what the sync fetches or stores can never be
  customised. The exception is "show upcoming beers": the sync works out
  Upcoming Beers while **any** profile has it on, and clears the cache (ADR-0006)
  only when none does.
- An unknown profile name renders the Default profile. Being runtime
  observations, the names asked for and the User-Agents seen are kept in
  memory, not in Settings.
