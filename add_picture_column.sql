-- Only needed if the staff app could not add the column itself (its terminal then prints
-- "could not add users.picture"). Run once in phpMyAdmin: `channeldesk` database > SQL tab > Go.
ALTER TABLE `users` ADD COLUMN `picture` VARCHAR(500) NULL;
