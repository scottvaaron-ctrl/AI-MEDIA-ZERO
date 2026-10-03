-- YouTube API data retention (YouTube Developer Policies III.E.4 and III.D; plan stage C, 2026-10-01).
--
-- comments.refreshed_at  when the platform last returned this comment. A YouTube comment not refreshed
--                        for 30 days is deleted (aimz/youtube_data.py); statistics and analytics may be
--                        kept while the channel's authorization is valid.

ALTER TABLE comments ADD COLUMN refreshed_at TEXT;
UPDATE comments SET refreshed_at = created_at WHERE refreshed_at IS NULL;
