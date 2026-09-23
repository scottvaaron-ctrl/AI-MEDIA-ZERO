-- Full article text for a lead, fetched just before a script is written from it.
-- Feed summaries are one line to a few hundred characters, too thin to write a video from.
ALTER TABLE source_items ADD COLUMN full_text TEXT;
ALTER TABLE source_items ADD COLUMN full_text_status TEXT;  -- ok | empty | skipped | failed: <reason>
