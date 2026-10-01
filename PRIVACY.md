# AI Media Zero — Privacy Policy

_Last updated: 2026-10-01_

AI Media Zero ("the software") is an open-source desktop program that a single person runs on
their own computer to produce short educational videos and publish them to that person's own
accounts on YouTube, TikTok and Bluesky. It is not a hosted service and has no accounts, sign-up,
or third-party users. The person running it may connect several of their own YouTube channels; each
channel is authorized separately, for that channel only, and its data is kept separately.

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
| `youtube.readonly` | identify the authorized channel; read the view, like and comment counts of the channel's own videos and its subscriber and total view counts; check the channel's own uploads list so an interrupted upload is never posted twice; read comments posted on the channel's own videos |
| `yt-analytics.readonly` | read the channel's own analytics (views, watch time, average view duration and percentage, likes, comments, shares, subscribers gained) |
| `yt-analytics-monetary.readonly` | read the channel's own estimated revenue reports (estimated revenue, ad revenue, CPM) once the channel is in the YouTube Partner Program |

The software does not access any other channel's private data. The only information about other
people it handles is the **public comments** on the channel's own videos: the commenter's display name
and the comment text. They are used only to classify audience feedback (for example a request for a
topic) for the channel owner, are never published or shared, and are deleted as described below. The
software computes its own internal score from the channel's statistics to decide what kind of video to
make next; that score is not shown to anyone outside the owner's computer.

## Use of TikTok APIs

The software optionally uses TikTok's **Login Kit**, **Content Posting API** (Direct Post) and
**Display API**, authorized by the account owner through TikTok's own OAuth consent screen. Use of
these APIs is subject to TikTok's [Terms of Service](https://www.tiktok.com/legal/terms-of-service)
and [Privacy Policy](https://www.tiktok.com/legal/privacy-policy).

| Scope | Purpose |
|---|---|
| `user.info.basic` | read the authorizing creator's own username and nickname, used to build the post's URL |
| `user.info.stats` | read the authorizing account's own follower count, to measure growth |
| `video.publish` / `video.upload` | upload videos the software produced to the authorizing user's own account |
| `video.list` | read the authorizing user's own public video list |

The software reads view, like, comment and share counts for the authorizing account's **own**
posts only. It never accesses another creator's private data, never browses or scrapes TikTok, and
never automates the TikTok website or app. Videos are uploaded with TikTok's AI-generated content
label set. Revoke access at any time in TikTok under Settings -> Security and permissions ->
Manage app permissions; the stored token then stops working.

## Use of the AT Protocol (Bluesky)

The software optionally posts to a Bluesky account using an **app password** that the account
owner creates and can revoke at any time in Bluesky under Settings -> Privacy and Security -> App
Passwords. It reads back only its own posts' like, reply, repost and quote counts, the account's
own follower count, and replies to its own posts. Revoking the app password immediately ends access.

## Where data is stored and for how long

All data (statistics, comments, OAuth tokens) is stored **only on the computer where the software
runs**, in a local SQLite database file and local token files, one set per channel. Nothing is sent to
any server operated by the software's authors. Data is not sold, shared, or transferred to any third
party.

- **YouTube statistics and analytics** (counts and percentages for the channel's own videos) are kept
  while the channel's authorization remains valid. Each video's figures are collected for 14 days after
  it is posted. The software checks on every run that it is still authorized.
- **YouTube comments** (commenter display name and text) are refreshed while the software still reads
  that video's comments, and are **deleted automatically once they have not been refreshed for 30
  days**, together with any comment text copied into a topic lead.
- **Upload responses** returned by YouTube are reduced to the video's id after 30 days.
- **If the authorization is revoked or stops working** for 7 days, or has not been confirmed for 30
  days, all YouTube data for that channel (statistics, analytics, comments, upload responses, video ids
  and links, and the token) is **deleted automatically**.

## Deleting data and revoking access

- Delete all stored data at any time by deleting the local `data/` folder, and all stored
  credentials by deleting the local `secrets/` folder.
- Revoke Google access at <https://security.google.com/settings/security/permissions>. The software
  then deletes that channel's YouTube data within 7 days (see above). To revoke and delete at once, the
  owner runs `python -m aimz youtube revoke` for that channel.
- Revoke TikTok access in the TikTok app: Settings -> Security and permissions -> Manage app permissions.
- Revoke Bluesky access by deleting the app password in Bluesky settings.

In each case the stored token stops working immediately and the software cannot post again until
the owner re-authorizes it.

## AI-generated content disclosure

Videos produced by the software use AI-generated narration and AI-assisted scripts. Every upload
declares this to the platform it is posted on: YouTube's altered/synthetic content flag, TikTok's
`is_aigc` AI-generated content label, and an explicit "AI-narrated" line in the Bluesky post text.
An AI disclosure and a source list accompany every video.

## Contact

Questions about this policy or the data described here: email backhousegroupnj@gmail.com, or open an
issue on the project's source repository.
