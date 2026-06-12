-- AlterTable (#90 M1: sliding idle window — bumped at most hourly on use)
ALTER TABLE "Session" ADD COLUMN     "lastSeenAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- #90 M3: emails are now lowercased at the API boundary; normalize existing
-- rows so historical mixed-case registrations can still log in. If two
-- accounts collide case-insensitively, the User.email unique index makes
-- this fail loudly — the operator must merge them before migrating.
UPDATE "User" SET "email" = lower("email") WHERE "email" <> lower("email");
