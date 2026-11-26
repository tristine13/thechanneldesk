-- Run once in phpMyAdmin: select the `channeldesk` database > SQL tab > paste > Go.
-- Removes every work written by the Adviser's account (Danica Balidio, an adviser is not a student)
-- and sets her position to "Adviser". This cannot be undone, so check the preview first.

SET @adviser := (SELECT id FROM users WHERE email = 'balidio_danica@fbc.ph.education');

-- 1) PREVIEW: the works that will be deleted
SELECT id, title, type, status, created_at FROM posts WHERE user_id = @adviser;

-- 2) DELETE the works and everything attached to them
DELETE FROM post_reactions WHERE post_id IN (SELECT id FROM posts WHERE user_id = @adviser);
DELETE FROM post_views     WHERE post_id IN (SELECT id FROM posts WHERE user_id = @adviser);
DELETE FROM post_images    WHERE post_id IN (SELECT id FROM posts WHERE user_id = @adviser);
DELETE FROM submissions    WHERE post_id IN (SELECT id FROM posts WHERE user_id = @adviser);
DELETE FROM notifications  WHERE reference_type IN ('post', 'submission')
                             AND reference_id IN (SELECT id FROM posts WHERE user_id = @adviser);
DELETE FROM posts WHERE user_id = @adviser;

-- 3) Her position is "Adviser", not Layout Artist
UPDATE registrations SET position = 'Adviser' WHERE email = 'balidio_danica@fbc.ph.education';
