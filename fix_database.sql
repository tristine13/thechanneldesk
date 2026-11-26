-- Run once in phpMyAdmin: select the `channeldesk` database > SQL tab > paste > Go.
-- Lets posts and tasks use the new sections (Opinion, Literary, Sci-Tech, Lokal, ...).
-- The old ENUM only allowed a fixed list, so saving a new section failed.

ALTER TABLE `posts` MODIFY `type` VARCHAR(30) NOT NULL;
ALTER TABLE `tasks` MODIFY `type` VARCHAR(30) NOT NULL DEFAULT 'News';

-- Old names -> the new section names (so those works appear under News / Sports on the student site)
UPDATE `posts` SET `type` = 'News'   WHERE `type` = 'College News';
UPDATE `posts` SET `type` = 'Sports' WHERE `type` = 'Sports News';

-- Old Op/Ed / Column labels -> Opinion (matches the student site)
UPDATE `posts` SET `type` = 'Opinion' WHERE `type` IN ('Op/Ed', 'OP/Ed', 'Op-Ed', 'Column');
UPDATE `tasks` SET `type` = 'Opinion' WHERE `type` IN ('Op/Ed', 'OP/Ed', 'Op-Ed', 'Column');
