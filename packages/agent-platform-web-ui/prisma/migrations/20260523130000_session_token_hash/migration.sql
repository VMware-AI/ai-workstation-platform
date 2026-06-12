-- Replace Session.token (plain) with Session.tokenHash (sha256 hex).
-- Existing rows cannot be migrated in-place because the original
-- pre-image is unknown to the server (only the user holds it via the
-- cookie). Truncating Session forces every user to log in again on
-- deploy; this is the intended security trade-off.

TRUNCATE TABLE "Session";

ALTER TABLE "Session" DROP CONSTRAINT IF EXISTS "Session_token_key";
ALTER TABLE "Session" RENAME COLUMN "token" TO "tokenHash";

CREATE UNIQUE INDEX "Session_tokenHash_key" ON "Session"("tokenHash");
