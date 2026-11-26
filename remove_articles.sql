-- Run once in phpMyAdmin: select the `channeldesk` database > SQL tab > paste > Go.
-- Removes the articles "FBC MASCOT", "Dog" and "SJJKSF" (and their images, review history,
-- notifications, reactions and views). This cannot be undone, so check the preview first.

-- 1) PREVIEW: the articles that will be deleted
SELECT id, title, type, status, created_at FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF');

-- 2) DELETE them and everything attached
DELETE FROM post_reactions WHERE post_id IN (SELECT id FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF'));
DELETE FROM post_views     WHERE post_id IN (SELECT id FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF'));
DELETE FROM post_images    WHERE post_id IN (SELECT id FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF'));
DELETE FROM submissions    WHERE post_id IN (SELECT id FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF'));
DELETE FROM notifications  WHERE reference_type IN ('post', 'submission')
                             AND reference_id IN (SELECT id FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF'));
DELETE FROM posts WHERE title IN ('FBC MASCOT', 'Dog', 'SJJKSF');
