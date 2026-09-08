# AI Media Zero — Privacy Policy

_Last updated: 2026-09-08_

AI Media Zero ("the software") is an open-source desktop program that a single person runs on
their own computer to produce short educational videos and publish them to that person's own
YouTube channel. It is not a hosted service and has no accounts, sign-up, or third-party users.

## Use of YouTube API Services

The software uses **YouTube API Services**. By using it you agree to be bound by the
[YouTube Terms of Service](https://www.youtube.com/t/terms). Google's handling of your data is
described in the [Google Privacy Policy](https://policies.google.com/privacy).

## What data the software accesses

Only the data of the YouTube channel whose owner authorizes the software, through Google's OAuth
consent screen, with these scopes:

| Scope | Purpose |
|---|---|
| `youtube.upload` | upload videos the software produced to the authorizing user's own channel |
| `youtube.readonly` | read comments posted on the authorizing user's own videos |
| `yt-analytics.readonly` | read the authorizing user's own channel analytics (views, watch time, average view percentage, shares, subscribers gained) |

The software does not access any other channel's private data, and does not collect personal
information about viewers. Public comment text is read only to classify audience feedback for the
channel owner.

## Where data is stored and for how long

All data (analytics numbers, comment text, OAuth tokens) is stored **only on the computer where
the software runs**, in a local SQLite database file and local token files. Nothing is sent to any
server operated by the software's authors. Analytics data is refreshed on each run and kept until
the owner deletes the local database. Data is not sold, shared, or transferred to any third party.

## Deleting data and revoking access

- Delete all stored YouTube data at any time by deleting the local `data/` folder.
- Revoke the software's access to your Google account at any time at
  <https://security.google.com/settings/security/permissions>. The stored token then stops working.

## AI-generated content disclosure

Videos produced by the software use AI-generated narration and AI-assisted scripts. Each upload
sets YouTube's altered/synthetic content flag and includes an AI disclosure and source list in the
description.

## Contact

Questions about this policy: open an issue on the project's source repository or contact the
channel owner listed on the channel's About page.
